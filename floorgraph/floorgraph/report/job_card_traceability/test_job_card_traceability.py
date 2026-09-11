# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, get_datetime, getdate, now_datetime

from floorgraph.actions.engine import approve_action, request_action
from floorgraph.floorgraph.doctype.schedule_slot.test_schedule_slot import (
	DEFAULT_WORKING_HOURS,
	_make_work_order_with_operations,
	_make_workstation,
)
from floorgraph.floorgraph.report.job_card_traceability.job_card_traceability import execute
from floorgraph.scheduling.engine import run_scheduling
from floorgraph.tests import FloorgraphTestCase

# No IGNORE_TEST_RECORD_DEPENDENCIES here, deliberately: on Frappe v16,
# IntegrationTestCase.setUpClass only supports that constant for a test
# module living inside a doctype/ folder (it auto-detects cls.doctype from
# the file's own location) - a report test module has no such doctype, and
# setting it there raises NotImplementedError. Every fixture this test needs
# (Company, Workstation, Job Card, ...) is built explicitly below anyway, so
# there's nothing for that mechanism to suppress here in the first place.


class TestJobCardTraceability(FloorgraphTestCase):
	def test_no_job_card_filter_returns_empty(self):
		columns, data = execute({})
		self.assertTrue(columns)
		self.assertEqual(data, [])

	def test_collects_every_event_type_chronologically(self):
		workstation = _make_workstation(working_hours=DEFAULT_WORKING_HOURS)
		_, _, job_cards = _make_work_order_with_operations([(workstation.name, 10, 1)])
		job_card = job_cards[1]

		anchor = get_datetime(add_days(now_datetime(), 1)).replace(hour=8, minute=0, second=0)
		run_scheduling(job_cards=[job_card], direction="Forward", anchor_datetime=anchor)

		log_name = request_action(
			"record_downtime",
			params={"workstation": workstation.name, "reason": "Breakdown", "minutes": 15},
			reference_doctype="Job Card",
			reference_name=job_card,
		)
		approve_action(log_name)

		# _oee_rows() scopes to the Job Card's own actual/expected date
		# window, using end = actual_end_date or expected_end_date or start.
		# The Work Order fixture sets expected_end_date at creation time,
		# independent of run_scheduling() (which never touches Job Card date
		# fields) - so forcing only expected_start_date to "tomorrow" left
		# that earlier expected_end_date in place, producing an inverted
		# [tomorrow, today] window that silently excludes every OEE row via
		# BETWEEN. Setting both ends explicitly closes that gap.
		expected_start = getdate(anchor)
		frappe.db.set_value(
			"Job Card",
			job_card,
			{"expected_start_date": expected_start, "expected_end_date": expected_start},
		)

		oee_log = frappe.get_doc(
			{
				"doctype": "Workstation OEE Log",
				"workstation": workstation.name,
				"log_date": getdate(expected_start),
				"planned_minutes": 480,
				"run_minutes": 400,
				"availability": 83.3,
				"actual_qty": 90,
				"ideal_run_minutes": 380,
				"performance": 95.0,
				"good_qty": 85,
				"scrap_qty": 5,
				"quality": 94.4,
				"oee": 74.6,
			}
		).insert(ignore_permissions=True)

		columns, data = execute({"job_card": job_card})

		event_types = {row["event_type"] for row in data}
		self.assertEqual(
			event_types,
			{"Downtime", "Action Request", "Schedule Slot", "Scheduling Run", "Workstation OEE"},
		)

		timestamps = [row["timestamp"] for row in data]
		self.assertEqual(timestamps, sorted(timestamps))

		action_row = next(row for row in data if row["event_type"] == "Action Request")
		self.assertEqual(action_row["reference"], log_name)
		self.assertEqual(action_row["status"], "Executed")

		downtime_row = next(row for row in data if row["event_type"] == "Downtime")
		self.assertEqual(downtime_row["status"], "Breakdown")

		oee_row = next(row for row in data if row["event_type"] == "Workstation OEE")
		self.assertEqual(oee_row["reference"], oee_log.name)
		self.assertIn(workstation.name, oee_row["detail"])

	def test_unknown_job_card_fails_clearly(self):
		self.assertRaises(frappe.ValidationError, execute, {"job_card": "Not A Real Job Card"})

	def test_job_card_with_no_dates_does_not_pick_up_unrelated_todays_oee(self):
		"""Regression test for a real bug found in QA: getdate(None) returns
		*today*, not None - an unscheduled/un-started Job Card (a completely
		normal state) with every date field genuinely blank was silently
		defaulting to a "today" window and picking up whatever unrelated OEE
		happened to log for its workstation that day."""
		workstation = _make_workstation(working_hours=DEFAULT_WORKING_HOURS)
		_, _, job_cards = _make_work_order_with_operations([(workstation.name, 10, 1)])
		job_card = job_cards[1]

		frappe.db.set_value(
			"Job Card",
			job_card,
			{
				"actual_start_date": None,
				"actual_end_date": None,
				"expected_start_date": None,
				"expected_end_date": None,
			},
		)

		frappe.get_doc(
			{
				"doctype": "Workstation OEE Log",
				"workstation": workstation.name,
				"log_date": getdate(),
				"planned_minutes": 480,
				"run_minutes": 480,
				"availability": 100.0,
				"actual_qty": 0,
				"ideal_run_minutes": 0,
				"performance": 0.0,
				"good_qty": 0,
				"scrap_qty": 0,
				"quality": 100.0,
				"oee": 0.0,
			}
		).insert(ignore_permissions=True)

		columns, data = execute({"job_card": job_card})
		self.assertEqual(data, [])
