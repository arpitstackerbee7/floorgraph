# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe


@frappe.whitelist()
def get_dashboard_data():
	workstations = frappe.get_all(
		"Workstation", fields=["name", "status", "workstation_type"], order_by="name"
	)

	latest_oee_by_workstation = {}
	for row in frappe.db.sql(
		"""
		select t1.workstation, t1.log_date, t1.availability, t1.performance, t1.quality, t1.oee
		from `tabWorkstation OEE Log` t1
		where t1.log_date = (
			select max(t2.log_date) from `tabWorkstation OEE Log` t2 where t2.workstation = t1.workstation
		)
		""",
		as_dict=True,
	):
		latest_oee_by_workstation[row.workstation] = row

	open_alert_counts = frappe._dict(
		frappe.db.sql(
			"""
			select workstation, count(*)
			from `tabAndon Alert`
			where status != 'Resolved'
			group by workstation
			"""
		)
	)

	for w in workstations:
		w["oee"] = latest_oee_by_workstation.get(w["name"])
		w["open_alerts"] = open_alert_counts.get(w["name"], 0)

	open_alerts = frappe.get_all(
		"Andon Alert",
		filters={"status": ["!=", "Resolved"]},
		fields=["name", "workstation", "alert_type", "severity", "status", "message", "creation"],
		order_by="creation desc",
		limit_page_length=50,
	)

	return {"workstations": workstations, "open_alerts": open_alerts}
