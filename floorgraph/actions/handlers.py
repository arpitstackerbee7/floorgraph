"""Handler functions for registered Actions.

Every handler takes the Action Log document that triggered it and returns a
JSON-serializable dict, which the engine stores on `Action Log.result`. A
handler should raise on bad input - the engine records that as a Failed
Action Log rather than letting it propagate.

flag_quality_deviation is intentionally light: it validates its params and
leaves an audit comment on the referenced document. There's no dedicated
"Quality Deviation" doctype yet - if one gets built later, this handler's
body changes to create a real record there, but the Action/engine contract
around it doesn't.

record_downtime creates a `Downtime Log` row, NOT ERPNext's native
`Downtime Entry` (erpnext.manufacturing). That's deliberate, not an
oversight: `Downtime Entry.operator` is a mandatory Employee link (actions
here are requested by a User, not necessarily one with an Employee record),
and `Downtime Entry.stop_reason` is a fixed Select with no Planned option -
floorgraph's OEE Availability calculation needs the Planned/Unplanned split
that `Downtime Reason.category` provides. ERPNext's native doctype covers
basic logging; it can't carry that split without a competing second
mandatory reason field on the same record.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from floorgraph.andon.engine import raise_alert
from floorgraph.scheduling.engine import lock_workstations, validate_reassignment


def record_downtime(action_log):
	params = frappe.parse_json(action_log.params) or {}
	workstation = params.get("workstation")
	reason = params.get("reason")
	minutes = params.get("minutes")

	if not (workstation and reason and minutes):
		frappe.throw(_("workstation, reason and minutes are all required"))

	reason_category = frappe.db.get_value("Downtime Reason", reason, "category")
	if reason_category is None:
		frappe.throw(_("Unknown Downtime Reason {0}").format(reason))

	to_time = now_datetime()
	from_time = add_to_date(to_time, minutes=-float(minutes))

	log = frappe.get_doc(
		{
			"doctype": "Downtime Log",
			"workstation": workstation,
			"downtime_reason": reason,
			"from_time": from_time,
			"to_time": to_time,
			"job_card": action_log.reference_name if action_log.reference_doctype == "Job Card" else None,
			"action_log": action_log.name,
		}
	)
	log.insert(ignore_permissions=True)

	_add_reference_comment(
		action_log,
		_("Downtime recorded: {0} min ({1}) on {2}").format(minutes, reason, workstation),
	)

	if reason_category == "Unplanned":
		raise_alert(
			workstation,
			alert_type="Downtime",
			message=_("{0} min unplanned downtime on {1} ({2})").format(minutes, workstation, reason),
			severity="Warning",
			action_log=action_log.name,
		)

	return {"downtime_log": log.name, "workstation": workstation, "reason": reason, "minutes": minutes}


def request_maintenance(action_log):
	"""Creates a draft ERPNext Asset Repair against the workstation's linked
	Asset. Left as a draft (not submitted) - a submittable accounting-
	adjacent doctype shouldn't be auto-submitted by automation; a human
	reviews and submits it from Desk like any other Asset Repair.

	Workstation carries no native Asset link in ERPNext core - `custom_asset`
	(see setup/install_custom_fields.py) is floorgraph's own extension point
	for it, seeded idempotently at install/migrate like every other seed
	here. A Workstation with no Asset set is a real "not configured yet"
	state, not a bug - fails clearly rather than guessing one.

	description is required from a Human caller (explain the ask), but an
	Agent Rule's scheduled evaluator (agent/engine.py) never passes params
	at all - it only ever has reference_doctype/reference_name to go on -
	so an Agent-sourced request without one gets an auto-generated
	description instead of failing on every single real agent-triggered
	call, which would defeat the point of this Action existing.
	"""
	params = frappe.parse_json(action_log.params) or {}
	description = params.get("description")
	if not description:
		if action_log.source == "Human":
			frappe.throw(_("description is required"))
		description = _("Auto-requested by {0} rule match on {1} {2}").format(
			action_log.source, action_log.reference_doctype, action_log.reference_name
		)

	workstation = _resolve_workstation(action_log, params)
	if not workstation:
		frappe.throw(_("Could not resolve a workstation for this request"))

	asset = frappe.db.get_value("Workstation", workstation, "custom_asset")
	if not asset:
		frappe.throw(_("Workstation {0} has no linked Asset (see its Asset field)").format(workstation))

	repair = frappe.get_doc(
		{
			"doctype": "Asset Repair",
			"asset": asset,
			"failure_date": frappe.utils.now_datetime(),
			"description": description,
		}
	)
	repair.insert(ignore_permissions=True)

	_add_reference_comment(
		action_log,
		_("Asset Repair {0} requested for {1} ({2})").format(repair.name, workstation, asset),
	)

	return {"asset_repair": repair.name, "workstation": workstation, "asset": asset}


def flag_quality_deviation(action_log):
	params = frappe.parse_json(action_log.params) or {}
	description = params.get("description")
	severity = params.get("severity") or "Minor"

	if not description:
		frappe.throw(_("description is required"))

	_add_reference_comment(
		action_log,
		_("Quality deviation flagged ({0}): {1}").format(severity, description),
	)

	if severity in ("Major", "Critical"):
		workstation = _resolve_workstation(action_log, params)
		if workstation:
			raise_alert(
				workstation,
				alert_type="Quality",
				message=_("Quality deviation ({0}): {1}").format(severity, description),
				severity="Critical" if severity == "Critical" else "Warning",
				action_log=action_log.name,
			)

	return {"severity": severity, "description": description}


def reassign_job_card(action_log):
	"""Drag-to-reschedule on the Gantt page. Reference is the Schedule Slot
	being moved - single-slot only for MVP: a slot split across multiple
	segments (e.g. across a lunch break) must be re-placed via Run
	Scheduling instead of dragged."""
	if action_log.reference_doctype != "Schedule Slot":
		frappe.throw(_("reassign_job_card requires reference_doctype = 'Schedule Slot'"))

	slot = frappe.get_doc("Schedule Slot", action_log.reference_name)
	params = frappe.parse_json(action_log.params) or {}
	new_workstation = params.get("new_workstation") or slot.workstation
	new_start_time = params.get("new_start_time")
	if not new_start_time:
		frappe.throw(_("new_start_time is required"))

	new_start_time = get_datetime(new_start_time)
	new_end_time = new_start_time + timedelta(minutes=slot.duration_minutes)

	lock_workstations([slot.workstation, new_workstation])
	validate_reassignment(slot, new_workstation, new_start_time, new_end_time)

	slot.workstation = new_workstation
	slot.start_time = new_start_time
	slot.end_time = new_end_time
	slot.save(ignore_permissions=True)

	_add_reference_comment(
		action_log,
		_("Schedule Slot {0} reassigned to {1} at {2}").format(slot.name, new_workstation, new_start_time),
	)

	return {
		"schedule_slot": slot.name,
		"workstation": new_workstation,
		"start_time": str(new_start_time),
		"end_time": str(new_end_time),
	}


def _resolve_workstation(action_log, params):
	"""Shared by every handler that needs a workstation but may be reached
	via different reference_doctypes (Job Card, Workstation itself, or an
	Agent Rule watching Workstation OEE Log) - fall back to an explicit
	`workstation` param, else skip/fail rather than guess."""
	if params.get("workstation"):
		return params["workstation"]
	if action_log.reference_doctype == "Job Card":
		return frappe.db.get_value("Job Card", action_log.reference_name, "workstation")
	if action_log.reference_doctype == "Workstation":
		return action_log.reference_name
	if action_log.reference_doctype == "Workstation OEE Log":
		return frappe.db.get_value("Workstation OEE Log", action_log.reference_name, "workstation")
	return None


def _add_reference_comment(action_log, text):
	if action_log.reference_doctype and action_log.reference_name:
		frappe.get_doc(action_log.reference_doctype, action_log.reference_name).add_comment("Info", text)
