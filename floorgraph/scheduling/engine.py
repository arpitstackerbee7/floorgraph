"""Finite-capacity scheduler: places Job Card operations into `Schedule Slot`
rows, respecting per-workstation working hours/holidays, Work Order operation
precedence (sequence_id), and Changeover Rule setup time.

Schedule Slot is a planning overlay, NOT a mutation of Job Card.time_logs.
floorgraph.oee.engine reads Job Card Time Log for ACTUAL production
(completed_qty, run/idle time) - if planned slots were written into
time_logs instead, OEE would count planned production as actual production.
That's a concrete regression, not a design preference.

This also assumes `Manufacturing Settings.disable_capacity_planning = 1`
(set by the Phase 0 demo fixture for fixture-build convenience, but now
load-bearing): if that flag is ever turned back on, ERPNext's own
Job Card.schedule_time_logs() starts producing a second, competing schedule
against the same Workstation.working_hours data this scheduler reads.

Greedy placement, single pass, two cursors:
  - a per-workstation cursor: what this workstation is doing up to/from
  - a per-work-order cursor: how far this work order's operation chain has
    been placed so far
An operation is placed at max(workstation_cursor, work_order_cursor) going
forward, or min(...) going backward - which is what keeps two different
operations of the same Work Order from being scheduled out of sequence, and
what keeps two operations sharing a Workstation from overlapping.

Backward scheduling takes an explicit `anchor_datetime` (the deadline to
schedule backward from) rather than inferring one from a Work Order field:
this project's demo Work Orders only populate `planned_start_date` - neither
`planned_end_date` nor `expected_delivery_date` is reliably set, so inferring
a deadline from either would be guessing at data that isn't there.
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime

from floorgraph.oee.engine import ideal_cycle_time_per_unit
from floorgraph.utils import is_workstation_holiday

MAX_LOOKAHEAD_DAYS = 90


def lock_workstations(names):
	"""Serializes concurrent writers (run_scheduling batches, reassign_job_card
	drags) touching the same Workstation(s) - without this, two concurrent
	transactions each read "existing slots" before either commits and can both
	place overlapping Schedule Slots (a plain TOCTOU race). Every entry point
	that writes Schedule Slot rows must call this, on the same sorted order,
	before reading or writing anything for those workstations: sorted order is
	what prevents two callers locking an overlapping-but-differently-ordered
	set of workstations from deadlocking each other."""
	for name in sorted(set(n for n in names if n)):
		frappe.db.get_value("Workstation", name, "name", for_update=True)


def run_scheduling(job_cards=None, work_orders=None, direction="Forward", anchor_datetime=None):
	"""Schedule a set of open Job Cards, writing Schedule Slot rows under a
	new Scheduling Run. Re-running for the same Job Cards replaces their
	previous slots (snapshotted on the run for rollback), rather than
	appending duplicates.
	"""
	direction = direction or "Forward"
	if direction not in ("Forward", "Backward"):
		frappe.throw(_("direction must be 'Forward' or 'Backward'"))

	if direction == "Backward" and not anchor_datetime:
		frappe.throw(
			_(
				"Backward scheduling requires an explicit anchor_datetime (the deadline to "
				"schedule backward from) - no Work Order date field here is reliably populated "
				"enough to infer one from."
			)
		)
	anchor_datetime = get_datetime(anchor_datetime) if anchor_datetime else get_datetime(now_datetime())

	rows = _job_cards_to_schedule(job_cards, work_orders)
	if not rows:
		frappe.throw(_("No open Job Cards found to schedule."))

	lock_workstations(r.workstation for r in rows)

	reverse = direction == "Backward"
	rows.sort(key=lambda r: (r.work_order, r.sequence_id or 0), reverse=reverse)

	run_doc = _start_run(rows, direction, anchor_datetime)

	workstation_docs = {}
	workstation_cursor = _seed_workstation_cursors(rows, direction, anchor_datetime)
	work_order_cursor = {}
	workstation_last_item = {}
	created = 0

	for row in rows:
		ws = row.workstation
		wo = row.work_order
		ws_doc = workstation_docs.setdefault(ws, frappe.get_doc("Workstation", ws))
		production_item = frappe.db.get_value("Work Order", wo, "production_item")
		duration = ideal_cycle_time_per_unit(row.name) * flt(row.for_quantity)
		if duration <= 0:
			frappe.throw(
				_(
					"Job Card {0}: cannot determine a planned duration (missing Work Order "
					"Operation time or quantity) - fix the source data before scheduling."
				).format(row.name)
			)

		if direction == "Forward":
			created += _place_forward_operation(
				run_doc.name,
				row,
				ws_doc,
				production_item,
				duration,
				workstation_cursor,
				work_order_cursor,
				workstation_last_item,
				anchor_datetime,
			)
		else:
			created += _place_backward_operation(
				run_doc.name,
				row,
				ws_doc,
				production_item,
				duration,
				workstation_cursor,
				work_order_cursor,
				workstation_last_item,
				anchor_datetime,
			)

		workstation_last_item[ws] = production_item

	run_doc.db_set("job_cards_scheduled", len(rows))
	return run_doc.name


def _job_cards_to_schedule(job_cards, work_orders):
	filters = {"docstatus": ["<", 2], "status": ["not in", ["Completed", "Cancelled"]]}
	if job_cards:
		filters["name"] = ["in", job_cards]
	elif work_orders:
		filters["work_order"] = ["in", work_orders]

	return frappe.get_all(
		"Job Card",
		filters=filters,
		fields=["name", "work_order", "workstation", "operation", "sequence_id", "for_quantity", "company"],
	)


def _seed_workstation_cursors(rows, direction, anchor_datetime):
	"""Without this, a second run_scheduling() call for a different batch of
	Job Cards on a workstation already touched by a PRIOR run would start
	placing from anchor_datetime as if that workstation were completely
	idle - double-booking against slots the prior run committed. Seed each
	touched workstation's cursor from its existing (non-batch) slots so a
	later incremental run still respects earlier capacity commitments.

	Deliberately tail-append, not gap-filling: this may leave an earlier
	free gap on the workstation unused rather than backfilling into it -
	acceptable for a greedy MVP scheduler, and never produces an overlap
	either way.
	"""
	batch_job_cards = [r.name for r in rows]
	cursor = {}
	for ws in {r.workstation for r in rows}:
		existing = frappe.get_all(
			"Schedule Slot",
			filters={"workstation": ws, "job_card": ["not in", batch_job_cards]},
			fields=["start_time", "end_time"],
		)
		if not existing:
			continue
		if direction == "Forward":
			latest_end = max(get_datetime(e.end_time) for e in existing)
			cursor[ws] = max(anchor_datetime, latest_end)
		else:
			earliest_start = min(get_datetime(e.start_time) for e in existing)
			cursor[ws] = min(anchor_datetime, earliest_start)
	return cursor


def _start_run(rows, direction, anchor_datetime):
	companies = {r.company for r in rows if r.company}
	if len(companies) > 1:
		frappe.throw(
			_(
				"Cannot run scheduling across Job Cards from multiple Companies ({0}) in a single "
				"run - schedule each Company separately."
			).format(", ".join(sorted(companies)))
		)
	company = companies.pop() if companies else None

	job_card_names = [r.name for r in rows]
	previous_slots = frappe.get_all(
		"Schedule Slot", filters={"job_card": ["in", job_card_names]}, fields=["*"]
	)
	snapshot = [dict(s) for s in previous_slots]
	superseded_runs = {s.scheduling_run for s in previous_slots if s.scheduling_run}
	for slot in previous_slots:
		frappe.delete_doc("Schedule Slot", slot.name, ignore_permissions=True, force=True)

	# Mark the run(s) whose slots this run just replaced as Superseded, so
	# rollback_scheduling_run can refuse to restore a run that's no longer the
	# current state of these Job Cards - restoring it would coexist with (not
	# replace) whatever superseded it, producing duplicate/overlapping slots.
	# A run that was already rolled back stays "Rolled Back", not superseded.
	for old_run in superseded_runs:
		if frappe.db.get_value("Scheduling Run", old_run, "status") != "Rolled Back":
			frappe.db.set_value("Scheduling Run", old_run, "status", "Superseded")

	run_doc = frappe.get_doc(
		{
			"doctype": "Scheduling Run",
			"direction": direction,
			"anchor_datetime": anchor_datetime,
			"company": company,
			"previous_slots_snapshot": json.dumps(snapshot, default=str),
		}
	)
	run_doc.insert(ignore_permissions=True)
	return run_doc


def _place_forward_operation(
	run_name,
	row,
	ws_doc,
	production_item,
	duration,
	workstation_cursor,
	work_order_cursor,
	workstation_last_item,
	anchor_datetime,
):
	ws, wo = row.workstation, row.work_order
	earliest = max(workstation_cursor.get(ws, anchor_datetime), work_order_cursor.get(wo, anchor_datetime))

	cursor = earliest
	created = 0
	last_item = workstation_last_item.get(ws)
	changeover = get_changeover_minutes(ws, last_item, production_item)
	if changeover:
		co_segments = _place_forward(ws_doc, cursor, changeover)
		_write_slots(run_name, row, "Changeover", co_segments)
		created += len(co_segments)
		cursor = co_segments[-1][1]

	prod_segments = _place_forward(ws_doc, cursor, duration)
	_write_slots(run_name, row, "Production", prod_segments)
	created += len(prod_segments)

	end_point = prod_segments[-1][1]
	workstation_cursor[ws] = end_point
	work_order_cursor[wo] = end_point
	return created


def _place_backward_operation(
	run_name,
	row,
	ws_doc,
	production_item,
	duration,
	workstation_cursor,
	work_order_cursor,
	workstation_last_item,
	anchor_datetime,
):
	ws, wo = row.workstation, row.work_order
	latest = min(workstation_cursor.get(ws, anchor_datetime), work_order_cursor.get(wo, anchor_datetime))

	created = 0
	# workstation_last_item, in a backward pass, holds the item of the
	# operation already placed chronologically AFTER this one on this
	# workstation - so the changeover we need here is "this item -> that one".
	next_item = workstation_last_item.get(ws)
	changeover = get_changeover_minutes(ws, production_item, next_item) if next_item else 0.0

	prod_latest_end = latest
	co_segments = []
	if changeover:
		co_segments = _place_backward(ws_doc, latest, changeover)
		prod_latest_end = co_segments[0][0]

	prod_segments = _place_backward(ws_doc, prod_latest_end, duration)
	_write_slots(run_name, row, "Production", prod_segments)
	created += len(prod_segments)
	if co_segments:
		_write_slots(run_name, row, "Changeover", co_segments)
		created += len(co_segments)

	start_point = prod_segments[0][0]
	workstation_cursor[ws] = start_point
	work_order_cursor[wo] = start_point
	return created


def _write_slots(run_name, row, slot_type, segments):
	for idx, (start, end) in enumerate(segments):
		frappe.get_doc(
			{
				"doctype": "Schedule Slot",
				"scheduling_run": run_name,
				"job_card": row.name,
				"work_order": row.work_order,
				"workstation": row.workstation,
				"sequence_id": row.sequence_id,
				"slot_type": slot_type,
				"segment_index": idx,
				"start_time": start,
				"end_time": end,
				"duration_minutes": (end - start).total_seconds() / 60,
			}
		).insert(ignore_permissions=True)


def get_changeover_minutes(workstation, from_item, to_item):
	if not from_item or not to_item or from_item == to_item:
		return 0.0
	minutes = frappe.db.get_value(
		"Changeover Rule",
		{"workstation": workstation, "from_item": from_item, "to_item": to_item},
		"changeover_minutes",
	)
	return flt(minutes)


def _is_holiday(workstation_doc, date):
	return is_workstation_holiday(workstation_doc.holiday_list, date)


def _sorted_windows(workstation_doc):
	rows = [r for r in workstation_doc.working_hours if cint(r.enabled)]
	if not rows:
		frappe.throw(
			_(
				"Workstation {0} has no enabled working hours configured. Configure "
				"Workstation.working_hours before scheduling it."
			).format(workstation_doc.name)
		)
	return sorted(rows, key=lambda r: r.start_time)


def _windows_for_date(workstation_doc, date):
	# A working-hours row can never have end_time <= start_time - ERPNext's
	# own Workstation.validate_working_hours() rejects any row where
	# start >= end at .insert() time. A night shift is still representable
	# as two same-day rows (e.g. 22:00-23:59:59 + 00:00-06:00), already
	# handled since this iterates every enabled row per date.
	if _is_holiday(workstation_doc, date):
		return []
	windows = []
	for row in _sorted_windows(workstation_doc):
		start = get_datetime(f"{date} {row.start_time}")
		end = get_datetime(f"{date} {row.end_time}")
		if end > start:
			windows.append((start, end))
	return windows


def _place_forward(workstation_doc, earliest_start, minutes_needed):
	"""Segments covering minutes_needed, starting at/after earliest_start,
	split across working windows/days as needed. Chronological order."""
	if minutes_needed <= 0:
		return []

	segments = []
	remaining = minutes_needed
	cursor = get_datetime(earliest_start)
	date = getdate(cursor)

	for _day in range(MAX_LOOKAHEAD_DAYS):
		if remaining <= 1e-6:
			break
		for w_start, w_end in _windows_for_date(workstation_doc, date):
			if w_end <= cursor:
				continue
			seg_start = max(w_start, cursor)
			if seg_start >= w_end:
				continue
			take = min((w_end - seg_start).total_seconds() / 60, remaining)
			seg_end = seg_start + timedelta(minutes=take)
			segments.append((seg_start, seg_end))
			remaining -= take
			cursor = seg_end
			if remaining <= 1e-6:
				break
		if remaining <= 1e-6:
			break
		date = add_days(date, 1)
		cursor = get_datetime(f"{date} 00:00:00")

	if remaining > 1e-6:
		frappe.throw(
			_("Could not place {0} minutes on {1} within {2} days.").format(
				minutes_needed, workstation_doc.name, MAX_LOOKAHEAD_DAYS
			)
		)
	return segments


def _place_backward(workstation_doc, latest_end, minutes_needed):
	"""Segments covering minutes_needed, ending at/before latest_end, packed
	as late as possible. Returned in chronological order."""
	if minutes_needed <= 0:
		return []

	segments = []
	remaining = minutes_needed
	cursor = get_datetime(latest_end)
	date = getdate(cursor)

	for _day in range(MAX_LOOKAHEAD_DAYS):
		if remaining <= 1e-6:
			break
		for w_start, w_end in reversed(_windows_for_date(workstation_doc, date)):
			if w_start >= cursor:
				continue
			seg_end = min(w_end, cursor)
			if seg_end <= w_start:
				continue
			take = min((seg_end - w_start).total_seconds() / 60, remaining)
			seg_start = seg_end - timedelta(minutes=take)
			segments.append((seg_start, seg_end))
			remaining -= take
			cursor = seg_start
			if remaining <= 1e-6:
				break
		if remaining <= 1e-6:
			break
		date = add_days(date, -1)
		cursor = get_datetime(f"{date} 23:59:59")

	if remaining > 1e-6:
		frappe.throw(
			_("Could not place {0} minutes on {1} within {2} days.").format(
				minutes_needed, workstation_doc.name, MAX_LOOKAHEAD_DAYS
			)
		)
	segments.reverse()
	return segments


def validate_slot_within_working_hours(workstation, start, end):
	ws_doc = frappe.get_doc("Workstation", workstation)
	start, end = get_datetime(start), get_datetime(end)
	date = getdate(start)
	if getdate(end) != date:
		frappe.throw(
			_(
				"Reassignment must stay within a single calendar day's working windows for "
				"MVP - use Run Scheduling for placements that need to span days."
			)
		)
	for w_start, w_end in _windows_for_date(ws_doc, date):
		if w_start <= start and end <= w_end:
			return
	frappe.throw(
		_("{0} - {1} on {2} falls outside its configured working hours.").format(start, end, workstation)
	)


def validate_no_overlap(workstation, start, end, exclude_slot=None):
	filters = {"workstation": workstation, "start_time": ["<", end], "end_time": [">", start]}
	if exclude_slot:
		filters["name"] = ["!=", exclude_slot]
	clashes = frappe.get_all("Schedule Slot", filters=filters, pluck="name")
	if clashes:
		frappe.throw(_("Overlaps with existing Schedule Slot(s): {0}").format(", ".join(clashes)))


def validate_precedence(slot, new_start, new_end):
	if slot.sequence_id is None:
		return
	siblings = frappe.get_all(
		"Schedule Slot",
		filters={"work_order": slot.work_order, "name": ["!=", slot.name]},
		fields=["name", "sequence_id", "start_time", "end_time"],
	)
	for sib in siblings:
		if sib.sequence_id is None:
			continue
		if sib.sequence_id < slot.sequence_id and get_datetime(sib.end_time) > new_start:
			frappe.throw(
				_("Reassignment would start before preceding operation's slot {0} finishes.").format(sib.name)
			)
		if sib.sequence_id > slot.sequence_id and get_datetime(sib.start_time) < new_end:
			frappe.throw(
				_("Reassignment would end after following operation's slot {0} starts.").format(sib.name)
			)


def validate_reassignment(slot, new_workstation, new_start, new_end):
	validate_slot_within_working_hours(new_workstation, new_start, new_end)
	validate_no_overlap(new_workstation, new_start, new_end, exclude_slot=slot.name)
	validate_precedence(slot, new_start, new_end)


@frappe.whitelist()
def api_run_scheduling(
	job_cards: str | list | None = None,
	work_orders: str | list | None = None,
	direction: str = "Forward",
	anchor_datetime: str | None = None,
):
	"""Whitelisted entry point for the Gantt page's "Run Scheduling" button.
	System Manager only - this rewrites the plan, unlike reassign_job_card
	(a single-slot drag), which is a governed Action instead."""
	if "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only System Manager can run scheduling"), frappe.PermissionError)

	if isinstance(job_cards, str):
		job_cards = frappe.parse_json(job_cards) if job_cards else None
	if isinstance(work_orders, str):
		work_orders = frappe.parse_json(work_orders) if work_orders else None

	return run_scheduling(
		job_cards=job_cards, work_orders=work_orders, direction=direction, anchor_datetime=anchor_datetime
	)


@frappe.whitelist()
def rollback_scheduling_run(scheduling_run: str):
	if "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only System Manager can roll back a scheduling run"), frappe.PermissionError)

	run_doc = frappe.get_doc("Scheduling Run", scheduling_run)
	if run_doc.status == "Rolled Back":
		frappe.throw(_("Scheduling Run {0} is already rolled back.").format(scheduling_run))
	if run_doc.status == "Superseded":
		frappe.throw(
			_(
				"Scheduling Run {0} has been superseded by a later run affecting its Job "
				"Cards - restoring it now would coexist with, not replace, that later run's "
				"slots and produce duplicate/overlapping bookings. Roll back the superseding "
				"run first (or re-run scheduling instead)."
			).format(scheduling_run)
		)

	current_slots = frappe.get_all(
		"Schedule Slot", filters={"scheduling_run": scheduling_run}, fields=["name", "workstation"]
	)
	snapshot = json.loads(run_doc.previous_slots_snapshot or "[]")
	lock_workstations([s.workstation for s in current_slots] + [row.get("workstation") for row in snapshot])

	for slot in current_slots:
		frappe.delete_doc("Schedule Slot", slot.name, ignore_permissions=True, force=True)

	drop_fields = {"name", "creation", "modified", "modified_by", "owner", "docstatus", "idx"}
	for row in snapshot:
		row = {k: v for k, v in row.items() if k not in drop_fields}
		frappe.get_doc({"doctype": "Schedule Slot", **row}).insert(ignore_permissions=True)

	# The run(s) whose slots this rollback just restored are live again, not
	# superseded - otherwise a further rollback of THEM would be wrongly
	# refused as "superseded" even though nothing supersedes them anymore.
	restored_runs = {row.get("scheduling_run") for row in snapshot if row.get("scheduling_run")}
	for restored_run in restored_runs:
		if frappe.db.get_value("Scheduling Run", restored_run, "status") == "Superseded":
			frappe.db.set_value("Scheduling Run", restored_run, "status", "Completed")

	run_doc.db_set("status", "Rolled Back")
	return {"restored_slots": len(snapshot), "removed_slots": len(current_slots)}
