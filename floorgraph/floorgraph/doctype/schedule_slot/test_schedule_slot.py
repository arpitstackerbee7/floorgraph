# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe
from frappe.utils import get_datetime, getdate

from floorgraph.actions.engine import approve_action, request_action
from floorgraph.scheduling.engine import run_scheduling
from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_setup_wizard_fixtures as _ensure_setup_wizard_fixtures
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# ERPNext's Job Card/Work Order/Workstation/Company doctypes, which trips a
# real ERPNext v16 bug on a bare site. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Job Card", "Work Order", "Workstation", "Company"]

DEFAULT_WORKING_HOURS = [
	{"start_time": "08:00:00", "end_time": "12:00:00", "enabled": 1},
	{"start_time": "13:00:00", "end_time": "17:00:00", "enabled": 1},
]


def _make_item(prefix="Item"):
	_ensure_setup_wizard_fixtures()
	suffix = frappe.generate_hash(length=8)
	return frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": f"Test Sched {prefix} {suffix}",
			"item_name": f"Test Sched {prefix} {suffix}",
			"item_group": "Products",
			"stock_uom": "Nos",
			"is_stock_item": 1,
		}
	).insert(ignore_permissions=True)


def _make_workstation(working_hours=None):
	# Schedule Slot itself can resolve company via job_card (Job Card carries
	# company directly), so a workstation with no warehouse worked here
	# before Changeover Rule gained its own company field - Changeover Rule
	# has no job_card fallback, only workstation, so it needs a real one.
	_ensure_test_company()
	suffix = frappe.generate_hash(length=8)
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": f"Test Sched WS {suffix}",
			"production_capacity": 1,
			"working_hours": working_hours or DEFAULT_WORKING_HOURS,
			"warehouse": "Work In Progress - _TC",
		}
	).insert(ignore_permissions=True)


def _make_work_order_with_operations(ops, wo_qty=10, item=None):
	"""ops: list of (workstation_name, time_in_mins_per_unit, sequence_id).
	Returns (work_order_name, item_name, {sequence_id: job_card_name})."""
	_ensure_test_company()
	suffix = frappe.generate_hash(length=8)
	item = item or _make_item()
	raw_material = _make_item("RM")

	bom_operations, wo_operations = [], []
	for workstation, time_per_unit, seq in ops:
		operation_name = f"Test Sched Op {seq} {suffix}"
		frappe.get_doc({"doctype": "Operation", "name": operation_name}).insert(ignore_permissions=True)
		bom_operations.append(
			{
				"operation": operation_name,
				"workstation": workstation,
				"time_in_mins": time_per_unit,
				"sequence_id": seq,
			}
		)
		wo_operations.append(
			{
				"operation": operation_name,
				"workstation": workstation,
				"time_in_mins": time_per_unit * wo_qty,
				"sequence_id": seq,
			}
		)

	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": item.name,
			"quantity": 1,
			"company": TEST_COMPANY,
			"with_operations": 1,
			"items": [{"item_code": raw_material.name, "qty": 1, "uom": "Nos", "rate": 1}],
			"operations": bom_operations,
		}
	)
	bom.insert(ignore_permissions=True)
	bom.submit()

	work_order = frappe.get_doc(
		{
			"doctype": "Work Order",
			"production_item": item.name,
			"bom_no": bom.name,
			"qty": wo_qty,
			"company": TEST_COMPANY,
			"fg_warehouse": "Finished Goods - _TC",
			"wip_warehouse": "Work In Progress - _TC",
			"planned_start_date": frappe.utils.now_datetime(),
			"operations": wo_operations,
		}
	)
	work_order.insert(ignore_permissions=True)
	work_order.submit()

	job_cards = frappe.get_all(
		"Job Card", filters={"work_order": work_order.name}, fields=["name", "sequence_id"]
	)
	by_seq = {jc.sequence_id: jc.name for jc in job_cards}
	return work_order.name, item.name, by_seq


def _slots_for(job_card, slot_type=None):
	filters = {"job_card": job_card}
	if slot_type:
		filters["slot_type"] = slot_type
	return frappe.get_all(
		"Schedule Slot",
		filters=filters,
		fields=["name", "start_time", "end_time", "workstation", "slot_type", "segment_index"],
		order_by="start_time asc",
	)


def _overlaps(a, b):
	return get_datetime(a.start_time) < get_datetime(b.end_time) and get_datetime(
		b.start_time
	) < get_datetime(a.end_time)


class TestScheduleSlot(FloorgraphTestCase):
	def test_capacity_respected_rolls_over_to_next_working_day(self):
		# 480 min/day (08:00-12:00 + 13:00-17:00). 10 units * 50 min = 500 min
		# total -> must roll 20 min into the next day's 08:00 window, and must
		# never place time inside the 12:00-13:00 gap.
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 50, 1)], wo_qty=10)
		job_card = by_seq[1]

		anchor = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor)

		slots = _slots_for(job_card, "Production")
		self.assertEqual(len(slots), 3)
		self.assertEqual(
			sum((get_datetime(s.end_time) - get_datetime(s.start_time)).total_seconds() / 60 for s in slots),
			500,
		)

		day1_start = get_datetime(f"{getdate()} 08:00:00")
		day1_lunch_start = get_datetime(f"{getdate()} 12:00:00")
		day1_afternoon_start = get_datetime(f"{getdate()} 13:00:00")
		day1_end = get_datetime(f"{getdate()} 17:00:00")

		self.assertEqual(get_datetime(slots[0].start_time), day1_start)
		self.assertEqual(get_datetime(slots[0].end_time), day1_lunch_start)
		self.assertEqual(get_datetime(slots[1].start_time), day1_afternoon_start)
		self.assertEqual(get_datetime(slots[1].end_time), day1_end)
		self.assertEqual(
			get_datetime(slots[2].start_time), get_datetime(f"{frappe.utils.add_days(getdate(), 1)} 08:00:00")
		)
		self.assertEqual(
			get_datetime(slots[2].end_time), get_datetime(f"{frappe.utils.add_days(getdate(), 1)} 08:20:00")
		)

	def test_no_double_booking_across_two_work_orders_on_same_workstation(self):
		ws = _make_workstation()
		_wo_a, _item_a, by_seq_a = _make_work_order_with_operations([(ws.name, 20, 1)], wo_qty=5)  # 100 min
		_wo_b, _item_b, by_seq_b = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=5)  # 150 min
		job_a, job_b = by_seq_a[1], by_seq_b[1]

		anchor = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_a, job_b], direction="Forward", anchor_datetime=anchor)

		slots_a = _slots_for(job_a, "Production")
		slots_b = _slots_for(job_b, "Production")
		for a in slots_a:
			for b in slots_b:
				self.assertFalse(_overlaps(a, b), f"{a} overlaps {b}")

	def test_changeover_rule_inserts_setup_time_between_items(self):
		ws = _make_workstation()
		item_a = _make_item("ItemA")
		item_b = _make_item("ItemB")
		frappe.get_doc(
			{
				"doctype": "Changeover Rule",
				"workstation": ws.name,
				"from_item": item_a.name,
				"to_item": item_b.name,
				"changeover_minutes": 45,
			}
		).insert(ignore_permissions=True)

		_wo_a, _ia, by_seq_a = _make_work_order_with_operations(
			[(ws.name, 10, 1)], wo_qty=2, item=item_a
		)  # 20 min
		_wo_b, _ib, by_seq_b = _make_work_order_with_operations(
			[(ws.name, 10, 1)], wo_qty=2, item=item_b
		)  # 20 min
		job_a, job_b = by_seq_a[1], by_seq_b[1]

		# _wo_a's Work Order name sorts before _wo_b's (created first, same
		# naming series) so the engine places A before B on this workstation
		# and the changeover A->B applies between them.
		self.assertLess(_wo_a, _wo_b)

		anchor = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_a, job_b], direction="Forward", anchor_datetime=anchor)

		changeover_slots = _slots_for(job_b, "Changeover")
		self.assertEqual(len(changeover_slots), 1)
		self.assertEqual(
			(
				get_datetime(changeover_slots[0].end_time) - get_datetime(changeover_slots[0].start_time)
			).total_seconds()
			/ 60,
			45,
		)

		prod_a = _slots_for(job_a, "Production")
		self.assertEqual(get_datetime(changeover_slots[0].start_time), get_datetime(prod_a[-1].end_time))

		prod_b = _slots_for(job_b, "Production")
		self.assertEqual(get_datetime(prod_b[0].start_time), get_datetime(changeover_slots[0].end_time))

	def test_operation_precedence_within_a_work_order_is_respected(self):
		ws_1 = _make_workstation()
		ws_2 = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations(
			[(ws_1.name, 10, 1), (ws_2.name, 15, 2)], wo_qty=4
		)  # op1: 40 min, op2: 60 min
		job_1, job_2 = by_seq[1], by_seq[2]

		anchor = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_1, job_2], direction="Forward", anchor_datetime=anchor)

		slots_1 = _slots_for(job_1, "Production")
		slots_2 = _slots_for(job_2, "Production")
		self.assertGreaterEqual(get_datetime(slots_2[0].start_time), get_datetime(slots_1[-1].end_time))

	def test_backward_scheduling_ends_at_or_before_anchor(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 20, 1)], wo_qty=3)  # 60 min
		job_card = by_seq[1]

		deadline = get_datetime(f"{getdate()} 17:00:00")
		run_scheduling(job_cards=[job_card], direction="Backward", anchor_datetime=deadline)

		slots = _slots_for(job_card, "Production")
		self.assertEqual(len(slots), 1)
		self.assertEqual(get_datetime(slots[0].end_time), deadline)
		self.assertEqual(get_datetime(slots[0].start_time), get_datetime(f"{getdate()} 16:00:00"))

	def test_backward_scheduling_requires_explicit_anchor(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]

		with self.assertRaises(frappe.ValidationError):
			run_scheduling(job_cards=[job_card], direction="Backward")

	def test_rerun_replaces_previous_slots_not_appends(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]

		anchor = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor)
		run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor)

		self.assertEqual(frappe.db.count("Schedule Slot", {"job_card": job_card}), 1)

	def test_scheduling_workstation_with_no_working_hours_fails_clearly(self):
		ws = frappe.get_doc(
			{
				"doctype": "Workstation",
				"workstation_name": f"Test Sched No-Hours WS {frappe.generate_hash(length=8)}",
				"production_capacity": 1,
			}
		).insert(ignore_permissions=True)
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]

		with self.assertRaises(frappe.ValidationError):
			run_scheduling(job_cards=[job_card], direction="Forward")

	def test_reassign_job_card_via_governed_action_moves_slot_within_working_hours(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=2)  # 60 min
		job_card = by_seq[1]

		run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)
		slot = _slots_for(job_card, "Production")[0]

		new_start = get_datetime(f"{getdate()} 14:00:00")
		log_name = request_action(
			"reassign_job_card",
			params={"new_start_time": str(new_start)},
			reference_doctype="Schedule Slot",
			reference_name=slot.name,
		)
		approve_action(log_name)

		log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(log.status, "Executed")

		moved = _slots_for(job_card, "Production")[0]
		self.assertEqual(get_datetime(moved.start_time), new_start)
		self.assertEqual(get_datetime(moved.end_time), get_datetime(f"{getdate()} 15:00:00"))

	def test_reassign_job_card_rejects_overlap_with_existing_slot(self):
		ws = _make_workstation()
		_wo_a, _ia, by_seq_a = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=1)  # 30 min
		_wo_b, _ib, by_seq_b = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=1)  # 30 min
		job_a, job_b = by_seq_a[1], by_seq_b[1]

		run_scheduling(
			job_cards=[job_a, job_b],
			direction="Forward",
			anchor_datetime=get_datetime(f"{getdate()} 08:00:00"),
		)
		slot_a = _slots_for(job_a, "Production")[0]
		slot_b = _slots_for(job_b, "Production")[0]

		log_name = request_action(
			"reassign_job_card",
			params={"new_start_time": str(get_datetime(slot_a.start_time))},
			reference_doctype="Schedule Slot",
			reference_name=slot_b.name,
		)
		approve_action(log_name)

		log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(log.status, "Failed")
		self.assertIn("Overlaps", log.error)

	def test_direct_save_of_overlapping_slot_is_rejected_at_doctype_level(self):
		# Schedule Slot's own validate() must enforce this - not just the
		# reassign_job_card action path - since a System Manager's full write
		# permission on this doctype lets them save one directly from Desk.
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=1)  # 30 min
		job_card = by_seq[1]
		run_name = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)
		existing = _slots_for(job_card, "Production")[0]

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Schedule Slot",
					"scheduling_run": run_name,
					"job_card": job_card,
					"work_order": _wo,
					"workstation": ws.name,
					"slot_type": "Production",
					"segment_index": 0,
					"start_time": existing.start_time,
					"end_time": existing.end_time,
					"duration_minutes": 30,
				}
			).insert(ignore_permissions=True)

	def test_direct_save_of_slot_outside_working_hours_is_rejected_at_doctype_level(self):
		ws = _make_workstation()  # 08:00-12:00, 13:00-17:00
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 30, 1)], wo_qty=1)
		job_card = by_seq[1]
		run_name = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Schedule Slot",
					"scheduling_run": run_name,
					"job_card": job_card,
					"work_order": _wo,
					"workstation": ws.name,
					"slot_type": "Production",
					"segment_index": 0,
					"start_time": get_datetime(f"{getdate()} 03:00:00"),
					"end_time": get_datetime(f"{getdate()} 03:30:00"),
					"duration_minutes": 30,
				}
			).insert(ignore_permissions=True)
