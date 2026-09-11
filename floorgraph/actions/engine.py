"""Governed action engine.

Every action - whether triggered by a human in the Desk UI, a sensor
webhook, or (later) an agent rule - goes through `request_action()`. No
caller ever invokes a handler directly. This is the one architectural idea
worth borrowing from Palantir Foundry's Ontology/AIP model per the project
plan: objects aren't just displayed, they're acted on, through a single
governed, audited path.

`request_action()` itself is deliberately NOT whitelisted: `source` decides
whether an Action Log always requires approval regardless of the Action's
own setting (agent-sourced requests will, from Phase 4 onward). If this
function were exposed directly over HTTP, any caller could pass their own
`source` and defeat that guarantee. Each calling surface (Desk/API today,
a sensor webhook or the agent scheduler later) gets its own thin whitelisted
wrapper that hardcodes `source` server-side.
"""

import frappe
from frappe import _

from floorgraph.utils import resolve_company


def request_action(
	action_name,
	params=None,
	reference_doctype=None,
	reference_name=None,
	source="Human",
	force_requires_approval=False,
):
	action = frappe.get_doc("Action", action_name)

	if action.disabled:
		frappe.throw(_("Action {0} is disabled").format(action_name))

	if source == "Human":
		# Deny-by-default via Action.allowed_roles is a Desk/API concept - it
		# answers "may this human ask for this action". Sensor and Agent
		# requests are gated by their own calling surfaces instead (HMAC
		# signature + Machine Event Source.allowed_actions for Sensor; System
		# Manager-only Agent Rule creation for Agent) - see
		# request_action_as_sensor/request_action_as_agent.
		_check_request_permission(action)

	if reference_doctype and reference_name and not frappe.db.exists(reference_doctype, reference_name):
		frappe.throw(_("{0} {1} does not exist").format(reference_doctype, reference_name))

	requires_approval = bool(action.requires_approval) or force_requires_approval

	log = frappe.get_doc(
		{
			"doctype": "Action Log",
			"action": action.name,
			"status": "Pending",
			"source": source,
			# Only a Human request has a meaningful requester - a Sensor's
			# request runs as Guest and an Agent's as whatever the scheduler
			# runs as (Administrator), neither of which identifies who/what
			# actually triggered the request.
			"requested_by": frappe.session.user if source == "Human" else None,
			"requires_approval": requires_approval,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"params": frappe.as_json(params or {}),
			# Best-effort, not required: Action Log's reference is generic
			# (any doctype, or none at all), so there's no single field this
			# can always derive from. A handler's `workstation` param covers
			# record_downtime/flag_quality_deviation; reference_doctype
			# covers reassign_job_card (Schedule Slot) and agent-sourced
			# requests (whatever source_doctype the Agent Rule watches).
			"company": resolve_company(
				workstation=(params or {}).get("workstation") if isinstance(params, dict) else None,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			),
		}
	)
	log.insert(ignore_permissions=True)

	if not requires_approval:
		_execute(log.name)

	return log.name


def _check_request_permission(action):
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))

	if user == "Administrator" or "System Manager" in user_roles:
		return

	allowed_roles = {row.role for row in action.allowed_roles}
	if allowed_roles & user_roles:
		return

	# Deny-by-default: an Action with no configured Action Allowed Role rows
	# can only be requested by System Manager/Administrator.
	frappe.throw(
		_("You are not permitted to request the action {0}").format(action.name),
		frappe.PermissionError,
	)


@frappe.whitelist()
def request_action_as_human(
	action_name: str,
	params: str | dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
):
	"""Whitelisted entry point for Desk/API callers. Hardcodes source="Human"
	- that hardcoding is what makes exposing this over HTTP safe."""
	if isinstance(params, str):
		params = frappe.parse_json(params)
	return request_action(
		action_name,
		params=params,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		source="Human",
	)


def request_action_as_sensor(action_name, params=None, reference_doctype=None, reference_name=None):
	"""Called internally by the Machine Event webhook handler, after HMAC
	signature verification - never whitelisted, so no HTTP caller can reach
	this directly or pass its own `source`. Hardcodes source="Sensor"."""
	return request_action(
		action_name,
		params=params,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		source="Sensor",
	)


def request_action_as_agent(action_name, params=None, reference_doctype=None, reference_name=None):
	"""Called internally by the Agent Rule scheduled evaluator - never
	whitelisted. Hardcodes source="Agent" and force_requires_approval=True:
	an agent-sourced request always lands Pending, even for an Action whose
	own requires_approval is unchecked. That override lives here in code,
	not as data on the Action record, so a System Manager editing an
	Action's requires_approval checkbox in Desk can't silently weaken the
	agent path's guarantee."""
	return request_action(
		action_name,
		params=params,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		source="Agent",
		force_requires_approval=True,
	)


@frappe.whitelist()
def approve_action(action_log: str):
	log = frappe.get_doc("Action Log", action_log, for_update=True)
	_check_approval_permission()

	if log.status != "Pending":
		frappe.throw(_("Action Log {0} is not pending approval").format(log.name))

	log.db_set(
		{
			"status": "Approved",
			"approved_by": frappe.session.user,
			"approved_on": frappe.utils.now_datetime(),
		}
	)
	_execute(log.name)
	return log.name


@frappe.whitelist()
def reject_action(action_log: str, reason: str | None = None):
	log = frappe.get_doc("Action Log", action_log, for_update=True)
	_check_approval_permission()

	if log.status != "Pending":
		frappe.throw(_("Action Log {0} is not pending approval").format(log.name))

	log.db_set(
		{
			"status": "Rejected",
			"rejected_by": frappe.session.user,
			"rejected_on": frappe.utils.now_datetime(),
			"rejection_reason": reason,
		}
	)
	return log.name


def _check_approval_permission():
	# MVP: approval authority is System Manager. Administrators always carry
	# that role, so self-approval (one user both requests and approves) is
	# possible today - a deliberate MVP simplification, not an oversight.
	# Revisit with a dedicated "Action Approver" role if that separation
	# turns out to matter.
	if "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only System Manager can approve or reject actions"), frappe.PermissionError)


def _execute(action_log_name):
	log = frappe.get_doc("Action Log", action_log_name, for_update=True)
	action = frappe.get_doc("Action", log.action)

	try:
		handler = frappe.get_attr(action.handler)
		result = handler(log)
		log.db_set(
			{
				"status": "Executed",
				"executed_on": frappe.utils.now_datetime(),
				"result": frappe.as_json(result or {}),
			}
		)
	except Exception:
		# Recorded, not raised: a bad handler call is an audited failure on
		# this Action Log, not an exception that should roll back the
		# approve/reject transition that led here.
		log.db_set(
			{
				"status": "Failed",
				"executed_on": frappe.utils.now_datetime(),
				"error": frappe.get_traceback(),
			}
		)
