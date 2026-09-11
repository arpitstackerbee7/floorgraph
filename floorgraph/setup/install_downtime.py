"""Seeds the built-in Downtime Reason masters. Idempotent - see
install_actions.py for why this runs on both after_install and after_migrate.
"""

import frappe

SEED_REASONS = [
	{"reason_name": "Changeover", "category": "Planned"},
	{"reason_name": "Planned Maintenance", "category": "Planned"},
	{"reason_name": "Breakdown", "category": "Unplanned"},
	{"reason_name": "Material Shortage", "category": "Unplanned"},
	{"reason_name": "Tooling Issue", "category": "Unplanned"},
]


def ensure_seed_downtime_reasons():
	for seed in SEED_REASONS:
		if frappe.db.exists("Downtime Reason", seed["reason_name"]):
			continue
		frappe.get_doc({"doctype": "Downtime Reason", **seed}).insert(ignore_permissions=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit - after_install/after_migrate (hooks.py), not a request/test transaction
