# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

"""Full audit trail for one Job Card: every Downtime Log, Action Log,
Schedule Slot placement, Scheduling Run, and same-window Workstation OEE Log
tied to it, in one chronological table. Everything here is data floorgraph
already collects, this just joins what's already there rather than
recording anything new."""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()

	if not filters.get("job_card"):
		return columns, []

	data = get_data(filters.job_card)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Reference Type"),
			"fieldname": "reference_doctype",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 130,
			"hidden": 1,
		},
		{"label": _("Date/Time"), "fieldname": "timestamp", "fieldtype": "Datetime", "width": 170},
		{"label": _("Event"), "fieldname": "event_type", "fieldtype": "Data", "width": 140},
		{
			"label": _("Reference"),
			"fieldname": "reference",
			"fieldtype": "Dynamic Link",
			"options": "reference_doctype",
			"width": 160,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Detail"), "fieldname": "detail", "fieldtype": "Data", "width": 360},
	]


def get_data(job_card):
	job = frappe.db.get_value(
		"Job Card",
		job_card,
		[
			"workstation",
			"actual_start_date",
			"actual_end_date",
			"expected_start_date",
			"expected_end_date",
		],
		as_dict=True,
	)
	if not job:
		frappe.throw(_("Job Card {0} not found").format(job_card))

	rows = []
	rows += _downtime_rows(job_card)
	rows += _action_log_rows(job_card)
	rows += _schedule_slot_rows(job_card)
	rows += _scheduling_run_rows(job_card)
	rows += _oee_rows(job)

	rows.sort(key=lambda row: row["timestamp"])
	return rows


def _downtime_rows(job_card):
	logs = frappe.get_all(
		"Downtime Log",
		filters={"job_card": job_card},
		fields=["name", "from_time", "to_time", "downtime_reason", "workstation"],
	)
	return [
		{
			"timestamp": log.from_time,
			"event_type": _("Downtime"),
			"reference_doctype": "Downtime Log",
			"reference": log.name,
			"status": log.downtime_reason,
			"detail": _("Downtime on {0}: {1} → {2}").format(log.workstation, log.from_time, log.to_time),
		}
		for log in logs
	]


def _action_log_rows(job_card):
	logs = frappe.get_all(
		"Action Log",
		filters={"reference_doctype": "Job Card", "reference_name": job_card},
		fields=["name", "action", "status", "source", "creation", "requested_by"],
	)
	return [
		{
			"timestamp": log.creation,
			"event_type": _("Action Request"),
			"reference_doctype": "Action Log",
			"reference": log.name,
			"status": log.status,
			"detail": _("{0} requested by {1}").format(log.action, log.requested_by or log.source),
		}
		for log in logs
	]


def _schedule_slot_rows(job_card):
	slots = frappe.get_all(
		"Schedule Slot",
		filters={"job_card": job_card},
		fields=["name", "workstation", "start_time", "end_time", "slot_type", "scheduling_run"],
	)
	return [
		{
			"timestamp": slot.start_time,
			"event_type": _("Schedule Slot"),
			"reference_doctype": "Schedule Slot",
			"reference": slot.name,
			"status": slot.slot_type,
			"detail": _("Placed on {0}: {1} → {2} (run {3})").format(
				slot.workstation, slot.start_time, slot.end_time, slot.scheduling_run
			),
		}
		for slot in slots
	]


def _scheduling_run_rows(job_card):
	run_names = frappe.get_all(
		"Schedule Slot",
		filters={"job_card": job_card, "scheduling_run": ["is", "set"]},
		pluck="scheduling_run",
		distinct=True,
	)
	if not run_names:
		return []

	runs = frappe.get_all(
		"Scheduling Run",
		filters={"name": ["in", run_names]},
		fields=["name", "creation", "direction", "status", "remarks"],
	)
	return [
		{
			"timestamp": run.creation,
			"event_type": _("Scheduling Run"),
			"reference_doctype": "Scheduling Run",
			"reference": run.name,
			"status": run.status,
			"detail": run.remarks or _("{0} scheduling run").format(run.direction),
		}
		for run in runs
	]


def _oee_rows(job):
	# OEE is per-workstation-per-day, not per-Job Card - scoped to this
	# job's own actual (falling back to expected) date window so "what was
	# the workstation doing while this job ran" stays honest instead of
	# pulling in a workstation's entire unrelated OEE history.
	if not job.workstation:
		return []

	# Checked before getdate(), not after: getdate(None) returns *today*,
	# not None/falsy - an unscheduled Job Card with no date fields at all
	# would otherwise silently get "today" as its window and pick up
	# whatever unrelated OEE happened to log today.
	start_raw = job.actual_start_date or job.expected_start_date
	if not start_raw:
		return []
	start = getdate(start_raw)
	end = getdate(job.actual_end_date or job.expected_end_date or start_raw)

	logs = frappe.get_all(
		"Workstation OEE Log",
		filters={"workstation": job.workstation, "log_date": ["between", [start, end]]},
		fields=["name", "log_date", "availability", "performance", "quality", "oee"],
	)
	return [
		{
			"timestamp": get_datetime(log.log_date),
			"event_type": _("Workstation OEE"),
			"reference_doctype": "Workstation OEE Log",
			"reference": log.name,
			"status": None,
			"detail": _("OEE {0}% (Availability {1}% / Performance {2}% / Quality {3}%) on {4}").format(
				log.oee, log.availability, log.performance, log.quality, job.workstation
			),
		}
		for log in logs
	]
