"""Scheduled evaluator for Agent Rule: scans `source_doctype` for records
where `field <operator> threshold`, and requests `target_action` (source=
"Agent", always forced to require approval - see request_action_as_agent)
for each match that doesn't already have an outstanding or completed request
for that same rule/action/record.

Deliberately structured-condition-only (doctype + field + operator +
threshold + optional static filters), no eval of user-supplied expressions:
Agent Rule is Desk-editable and this repo is going public, so an expression
field here would be an RCE-adjacent surface.
"""

import frappe

from floorgraph.utils import resolve_company

_OPERATOR_TO_FRAPPE_FILTER = {
	"<": "<",
	"<=": "<=",
	">": ">",
	">=": ">=",
	"==": "=",
	"!=": "!=",
}

# Action Log statuses that mean "don't re-request" - a Rejected request is
# not included: a human said no to that specific instance, but if the
# condition still holds next tick that's a fresh decision to re-surface, not
# spam to suppress forever.
_OUTSTANDING_OR_DONE_STATUSES = ["Pending", "Approved", "Executed"]


def evaluate_agent_rules():
	from floorgraph.actions.engine import request_action_as_agent

	for rule_name in frappe.get_all("Agent Rule", filters={"enabled": 1}, pluck="name"):
		rule = frappe.get_doc("Agent Rule", rule_name)
		for match_name in _matching_records(rule):
			if _already_outstanding_or_done(rule, match_name):
				continue
			request_action_as_agent(
				rule.target_action,
				reference_doctype=rule.source_doctype,
				reference_name=match_name,
			)


def _matching_records(rule):
	filters = frappe.parse_json(rule.filters) if rule.filters else {}
	filters[rule.field] = [_OPERATOR_TO_FRAPPE_FILTER[rule.operator], rule.threshold]

	# Scope the scan to this rule's Company. Most Agent Rule targets carry a
	# `company` field directly (Workstation OEE Log, Action Log, ...) and
	# filter at the DB layer; Workstation itself doesn't (see
	# floorgraph.utils' module docstring), so those matches are resolved and
	# filtered in Python instead. Guarded by has_field rather than adding
	# `company` to the query unconditionally - that would raise a SQL error
	# on any source_doctype without the column, not silently ignore it.
	if frappe.get_meta(rule.source_doctype).has_field("company"):
		filters["company"] = rule.company
		return frappe.get_all(rule.source_doctype, filters=filters, pluck="name")

	matches = frappe.get_all(rule.source_doctype, filters=filters, pluck="name")
	if rule.source_doctype == "Workstation":
		return [m for m in matches if resolve_company(workstation=m) == rule.company]
	return matches


def _already_outstanding_or_done(rule, match_name):
	return frappe.db.exists(
		"Action Log",
		{
			"action": rule.target_action,
			"source": "Agent",
			"reference_doctype": rule.source_doctype,
			"reference_name": match_name,
			"status": ["in", _OUTSTANDING_OR_DONE_STATUSES],
		},
	)
