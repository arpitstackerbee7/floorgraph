"""Andon alerts: raised by action handlers when something on the shop floor
needs a human's attention now, pushed live via frappe.publish_realtime so
the shop-floor dashboard (Phase 2's last piece) updates without a refresh.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

REALTIME_EVENT = "floorgraph:andon_alert"
RESPONDER_ROLES = {"Floorgraph Operator", "System Manager"}


def raise_alert(workstation, alert_type, message, severity="Warning", action_log=None):
	alert = frappe.get_doc(
		{
			"doctype": "Andon Alert",
			"workstation": workstation,
			"alert_type": alert_type,
			"message": message,
			"severity": severity,
			"action_log": action_log,
		}
	)
	alert.insert(ignore_permissions=True)

	frappe.publish_realtime(
		REALTIME_EVENT,
		{
			"name": alert.name,
			"workstation": workstation,
			"alert_type": alert_type,
			"severity": severity,
			"message": message,
		},
		doctype="Andon Alert",
	)

	return alert.name


@frappe.whitelist()
def acknowledge_alert(alert: str):
	doc = frappe.get_doc("Andon Alert", alert, for_update=True)
	_check_responder_permission()

	if doc.status != "Open":
		frappe.throw(_("Andon Alert {0} is not open").format(doc.name))

	doc.db_set(
		{
			"status": "Acknowledged",
			"acknowledged_by": frappe.session.user,
			"acknowledged_on": now_datetime(),
		}
	)
	return doc.name


@frappe.whitelist()
def resolve_alert(alert: str):
	doc = frappe.get_doc("Andon Alert", alert, for_update=True)
	_check_responder_permission()

	if doc.status == "Resolved":
		frappe.throw(_("Andon Alert {0} is already resolved").format(doc.name))

	doc.db_set(
		{
			"status": "Resolved",
			"resolved_by": frappe.session.user,
			"resolved_on": now_datetime(),
		}
	)
	return doc.name


def _check_responder_permission():
	if frappe.session.user == "Administrator":
		return
	if not RESPONDER_ROLES & set(frappe.get_roles(frappe.session.user)):
		frappe.throw(
			_("Only a Floorgraph Operator or System Manager can respond to Andon Alerts"),
			frappe.PermissionError,
		)
