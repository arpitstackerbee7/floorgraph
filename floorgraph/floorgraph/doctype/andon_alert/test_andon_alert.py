# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.actions.engine import approve_action, request_action
from floorgraph.andon.engine import acknowledge_alert, raise_alert, resolve_alert
from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# non-floorgraph doctypes (Workstation/Company are the real ERPNext v16
# bug; User is blocked defensively - see test_action_log.py for why "just
# a core Frappe doctype" isn't a safe assumption on its own). No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Workstation", "User", "Company"]


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


class TestAndonAlert(FloorgraphTestCase):
	def test_unplanned_downtime_raises_andon_alert(self):
		workstation = _make_workstation()

		log_name = request_action(
			"record_downtime",
			params={"workstation": workstation.name, "reason": "Breakdown", "minutes": 15},
		)
		approve_action(log_name)

		alerts = frappe.get_all(
			"Andon Alert", filters={"workstation": workstation.name, "alert_type": "Downtime"}
		)
		self.assertEqual(len(alerts), 1)

	def test_planned_downtime_does_not_raise_andon_alert(self):
		workstation = _make_workstation()

		log_name = request_action(
			"record_downtime",
			params={"workstation": workstation.name, "reason": "Changeover", "minutes": 15},
		)
		approve_action(log_name)

		self.assertEqual(frappe.db.count("Andon Alert", {"workstation": workstation.name}), 0)

	def test_major_quality_deviation_with_workstation_param_raises_alert(self):
		workstation = _make_workstation()

		log_name = request_action(
			"flag_quality_deviation",
			params={
				"description": "Weld porosity",
				"severity": "Major",
				"workstation": workstation.name,
			},
		)
		approve_action(log_name)

		alerts = frappe.get_all(
			"Andon Alert", filters={"workstation": workstation.name, "alert_type": "Quality"}
		)
		self.assertEqual(len(alerts), 1)

	def test_minor_quality_deviation_does_not_raise_alert(self):
		workstation = _make_workstation()

		log_name = request_action(
			"flag_quality_deviation",
			params={
				"description": "Minor cosmetic scratch",
				"severity": "Minor",
				"workstation": workstation.name,
			},
		)
		approve_action(log_name)

		self.assertEqual(frappe.db.count("Andon Alert", {"workstation": workstation.name}), 0)

	def test_acknowledge_then_resolve(self):
		workstation = _make_workstation()
		alert_name = raise_alert(workstation.name, alert_type="Downtime", message="test")

		acknowledge_alert(alert_name)
		alert = frappe.get_doc("Andon Alert", alert_name)
		self.assertEqual(alert.status, "Acknowledged")
		self.assertEqual(alert.acknowledged_by, "Administrator")

		resolve_alert(alert_name)
		alert.reload()
		self.assertEqual(alert.status, "Resolved")
		self.assertEqual(alert.resolved_by, "Administrator")

	def test_cannot_acknowledge_already_resolved_state_twice(self):
		workstation = _make_workstation()
		alert_name = raise_alert(workstation.name, alert_type="Downtime", message="test")

		acknowledge_alert(alert_name)
		self.assertRaises(frappe.ValidationError, acknowledge_alert, alert_name)
