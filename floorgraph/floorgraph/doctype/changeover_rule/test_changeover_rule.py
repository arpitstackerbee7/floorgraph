# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_setup_wizard_fixtures as _ensure_setup_wizard_fixtures
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# ERPNext's Workstation/Item/Company doctypes, which trips a real ERPNext
# v16 bug on a bare site. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Workstation", "Item", "Company"]


def _make_workstation():
	_ensure_test_company()
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": frappe.generate_hash(length=8),
			"production_capacity": 1,
			"warehouse": "Work In Progress - _TC",
		}
	).insert(ignore_permissions=True)


def _make_item():
	_ensure_setup_wizard_fixtures()
	return frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": frappe.generate_hash(length=10),
			"item_name": frappe.generate_hash(length=10),
			"item_group": "Products",
			"stock_uom": "Nos",
			"is_stock_item": 1,
		}
	).insert(ignore_permissions=True)


class TestChangeoverRule(FloorgraphTestCase):
	def test_company_derived_from_workstation(self):
		ws = _make_workstation()
		item_a, item_b = _make_item(), _make_item()

		rule = frappe.get_doc(
			{
				"doctype": "Changeover Rule",
				"workstation": ws.name,
				"from_item": item_a.name,
				"to_item": item_b.name,
				"changeover_minutes": 10,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(rule.company, TEST_COMPANY)

	def test_workstation_with_no_resolvable_company_is_rejected(self):
		ws = frappe.get_doc(
			{
				"doctype": "Workstation",
				"workstation_name": frappe.generate_hash(length=8),
				"production_capacity": 1,
			}
		).insert(ignore_permissions=True)
		item_a, item_b = _make_item(), _make_item()

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Changeover Rule",
					"workstation": ws.name,
					"from_item": item_a.name,
					"to_item": item_b.name,
					"changeover_minutes": 10,
				}
			).insert(ignore_permissions=True)
