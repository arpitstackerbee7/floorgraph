"""Extends ERPNext core doctypes with fields floorgraph needs but doesn't
own - the standard Frappe way to add data to a doctype without forking it.
Idempotent - see install_actions.py for why this runs on both after_install
and after_migrate.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
	"Workstation": [
		{
			"fieldname": "custom_asset",
			"label": "Asset",
			"fieldtype": "Link",
			"options": "Asset",
			"insert_after": "warehouse",
			"description": "Used by floorgraph's Request Maintenance action to raise an Asset Repair for this workstation.",
		}
	]
}


def ensure_workstation_asset_field():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - after_install/after_migrate (hooks.py), not a request/test transaction
