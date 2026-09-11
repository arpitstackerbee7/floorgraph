# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.actions.engine import approve_action, request_action
from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# On Frappe v16+ (frappe.tests.IntegrationTestCase), setUpClass auto-builds
# test records for this doctype's own dependency graph - floorgraph never
# relies on that (every fixture here is built explicitly, in Python), and
# walking into these ERPNext-core doctypes trips a real ERPNext v16 bug
# (erpnext.tests.utils.BootStrapTestData fails on a bare, non-CI-bootstrapped
# site) - see floorgraph/tests/__init__.py. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Workstation", "Job Card", "Company"]


def _make_workstation():
	_ensure_test_company()
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": frappe.generate_hash(length=8),
			"production_capacity": 1,
			# Company resolves via Workstation.warehouse.company (Workstation
			# itself has no company field) - see floorgraph.utils.resolve_company.
			"warehouse": "Work In Progress - _TC",
		}
	).insert(ignore_permissions=True)


class TestDowntimeLog(FloorgraphTestCase):
	def test_record_downtime_action_creates_downtime_log(self):
		"""Regression test for the real seeded `record_downtime` Action, not a
		throwaway handler - this is the seam Phase 2 changed under Phase 1's
		engine, so it must be covered directly."""
		workstation = _make_workstation()

		log_name = request_action(
			"record_downtime",
			params={"workstation": workstation.name, "reason": "Breakdown", "minutes": 25},
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Executed")

		result = frappe.parse_json(action_log.result)
		downtime_log = frappe.get_doc("Downtime Log", result["downtime_log"])

		self.assertEqual(downtime_log.workstation, workstation.name)
		self.assertEqual(downtime_log.downtime_reason, "Breakdown")
		self.assertAlmostEqual(downtime_log.duration_minutes, 25, delta=0.5)
		self.assertEqual(downtime_log.action_log, log_name)

	def test_record_downtime_fails_on_unknown_reason(self):
		workstation = _make_workstation()

		log_name = request_action(
			"record_downtime",
			params={"workstation": workstation.name, "reason": "Not A Real Reason", "minutes": 10},
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Failed")
		self.assertIn("Unknown Downtime Reason", action_log.error)
		self.assertEqual(frappe.db.count("Downtime Log", {"workstation": workstation.name}), 0)
