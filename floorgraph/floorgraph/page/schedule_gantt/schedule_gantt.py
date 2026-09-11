# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, get_datetime, getdate


@frappe.whitelist()
def get_gantt_data(from_date: str | None = None, to_date: str | None = None):
	from_date = getdate(from_date) if from_date else getdate()
	to_date = getdate(to_date) if to_date else add_days(from_date, 3)

	window_start = get_datetime(f"{from_date} 00:00:00")
	window_end = get_datetime(f"{to_date} 23:59:59")

	workstations = frappe.get_all("Workstation", fields=["name", "status"], order_by="name")

	slots = frappe.get_all(
		"Schedule Slot",
		filters={"start_time": ["<", window_end], "end_time": [">", window_start]},
		fields=[
			"name",
			"scheduling_run",
			"job_card",
			"work_order",
			"workstation",
			"sequence_id",
			"slot_type",
			"segment_index",
			"start_time",
			"end_time",
			"duration_minutes",
		],
		order_by="workstation asc, start_time asc",
	)

	work_order_items = {}
	for row in frappe.get_all(
		"Work Order",
		filters={"name": ["in", list({s.work_order for s in slots})]} if slots else {"name": "__none__"},
		fields=["name", "production_item"],
	):
		work_order_items[row.name] = row.production_item

	# Only fetched to flag overdue blocks on the Gantt (a Production slot
	# whose end_time has already passed but its Job Card isn't Completed/
	# Cancelled) - not used for anything else here.
	job_card_status = {}
	for row in frappe.get_all(
		"Job Card",
		filters={"name": ["in", list({s.job_card for s in slots if s.job_card})]}
		if slots
		else {"name": "__none__"},
		fields=["name", "status"],
	):
		job_card_status[row.name] = row.status

	for slot in slots:
		slot["production_item"] = work_order_items.get(slot.work_order)
		slot["job_card_status"] = job_card_status.get(slot.job_card)

	return {
		"from_date": str(from_date),
		"to_date": str(to_date),
		"workstations": workstations,
		"slots": slots,
	}
