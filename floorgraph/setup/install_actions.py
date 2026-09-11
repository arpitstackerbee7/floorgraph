"""Seeds the built-in Action definitions. Idempotent - safe to run on every
install and every migrate (see hooks.py), since a fresh site created by CI
may only run one of the two.
"""

import frappe

SEED_ACTIONS = [
	{
		"action_name": "record_downtime",
		"label": "Record Downtime",
		"description": "Log a downtime event against a workstation.",
		"handler": "floorgraph.actions.handlers.record_downtime",
		"requires_approval": 1,
	},
	{
		"action_name": "flag_quality_deviation",
		"label": "Flag Quality Deviation",
		"description": "Flag a quality deviation against a job card or item.",
		"handler": "floorgraph.actions.handlers.flag_quality_deviation",
		"requires_approval": 1,
	},
	{
		"action_name": "reassign_job_card",
		"label": "Reassign Job Card",
		"description": "Drag-to-reschedule a Schedule Slot to a new workstation/time on the Gantt.",
		"handler": "floorgraph.actions.handlers.reassign_job_card",
		"requires_approval": 1,
	},
	{
		"action_name": "request_maintenance",
		"label": "Request Maintenance",
		"description": "Raise a draft Asset Repair against a workstation's linked Asset.",
		"handler": "floorgraph.actions.handlers.request_maintenance",
		"requires_approval": 1,
	},
]

OPERATOR_ROLE = "Floorgraph Operator"


def ensure_seed_actions():
	if not frappe.db.exists("Role", OPERATOR_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": OPERATOR_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)

	for seed in SEED_ACTIONS:
		if frappe.db.exists("Action", seed["action_name"]):
			continue
		frappe.get_doc(
			{
				"doctype": "Action",
				**seed,
				"allowed_roles": [{"role": OPERATOR_ROLE}],
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit - after_install/after_migrate (hooks.py), not a request/test transaction
