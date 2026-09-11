# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe
from frappe.utils import get_datetime, getdate

from floorgraph.floorgraph.doctype.schedule_slot.test_schedule_slot import (
	_make_work_order_with_operations,
	_make_workstation,
	_slots_for,
)
from floorgraph.scheduling.engine import rollback_scheduling_run, run_scheduling
from floorgraph.tests import FloorgraphTestCase

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# ERPNext's Company doctype, which trips a real ERPNext v16 bug on a bare
# site. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company"]


class TestSchedulingRun(FloorgraphTestCase):
	def test_rollback_restores_prior_slots_and_removes_new_ones(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)  # 10 min
		job_card = by_seq[1]

		anchor_1 = get_datetime(f"{getdate()} 08:00:00")
		run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor_1)
		first_run_slots = _slots_for(job_card, "Production")
		self.assertEqual(get_datetime(first_run_slots[0].start_time), anchor_1)

		anchor_2 = get_datetime(f"{getdate()} 09:00:00")
		second_run = run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor_2)
		second_run_slots = _slots_for(job_card, "Production")
		self.assertEqual(get_datetime(second_run_slots[0].start_time), anchor_2)

		rollback_scheduling_run(second_run)

		restored_slots = _slots_for(job_card, "Production")
		self.assertEqual(len(restored_slots), 1)
		self.assertEqual(get_datetime(restored_slots[0].start_time), anchor_1)
		self.assertEqual(frappe.db.get_value("Scheduling Run", second_run, "status"), "Rolled Back")

	def test_rollback_twice_fails_clearly(self):
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]

		run = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)
		rollback_scheduling_run(run)

		with self.assertRaises(frappe.ValidationError):
			rollback_scheduling_run(run)

	def test_rolling_back_a_superseded_run_is_rejected(self):
		# run_1 gets superseded the instant run_2 is created for the same Job
		# Card. Rolling run_1 back directly (instead of rolling run_2 back
		# first) would restore run_1's slots ALONGSIDE run_2's still-live
		# slots - duplicate/overlapping bookings, not a real undo.
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]

		run_1 = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)
		run_2 = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 09:00:00")
		)
		self.assertEqual(frappe.db.get_value("Scheduling Run", run_1, "status"), "Superseded")

		with self.assertRaises(frappe.ValidationError):
			rollback_scheduling_run(run_1)

		# run_2's own slots must be untouched by the rejected attempt.
		self.assertEqual(len(_slots_for(job_card, "Production")), 1)
		self.assertEqual(
			frappe.db.get_value("Schedule Slot", {"job_card": job_card}, "scheduling_run"), run_2
		)

		# Rolling back run_2 (the actual latest run) restores run_1's slots
		# and correctly un-supersedes run_1, since it's live again.
		rollback_scheduling_run(run_2)
		self.assertEqual(frappe.db.get_value("Scheduling Run", run_1, "status"), "Completed")
		self.assertEqual(
			frappe.db.get_value("Schedule Slot", {"job_card": job_card}, "scheduling_run"), run_1
		)

	def test_direct_save_cannot_forge_status_but_remarks_stays_editable(self):
		"""Found live in QA: read_only=1 in the doctype JSON only hides a
		field in the Desk form, it does nothing server-side. Without this
		guard, a direct save could flip status (or forge company/
		job_cards_scheduled/previous_slots_snapshot - the last of which the
		rollback mechanism trusts completely) without the scheduling engine
		ever running. Unlike Action Log, this doctype has genuinely
		user-editable fields (remarks), so the fix must not make the whole
		document immutable."""
		ws = _make_workstation()
		_wo, _item, by_seq = _make_work_order_with_operations([(ws.name, 10, 1)], wo_qty=1)
		job_card = by_seq[1]
		run_name = run_scheduling(
			job_cards=[job_card], direction="Forward", anchor_datetime=get_datetime(f"{getdate()} 08:00:00")
		)

		run = frappe.get_doc("Scheduling Run", run_name)
		run.remarks = "a legitimate note"
		run.save()
		run.reload()
		self.assertEqual(run.remarks, "a legitimate note")

		run.status = "Rolled Back"
		self.assertRaises(frappe.PermissionError, run.save)
		run.reload()
		self.assertEqual(run.status, "Completed")
