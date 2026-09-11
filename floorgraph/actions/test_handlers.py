# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe
from frappe.utils import getdate

from floorgraph.actions.engine import approve_action, request_action, request_action_as_agent
from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# No IGNORE_TEST_RECORD_DEPENDENCIES here, deliberately: on Frappe v16,
# IntegrationTestCase.setUpClass only supports that constant for a test
# module living inside a doctype/ folder (it auto-detects cls.doctype from
# the file's own location) - floorgraph/actions/ isn't one, and setting it
# raises NotImplementedError there. Every fixture below is built explicitly
# anyway, so there's nothing for that mechanism to suppress here.

_ASSET_CATEGORY = "Test Floorgraph Asset Category"
_ASSET_ITEM = "Test Floorgraph Asset Item"
_LOCATION = "Test Floorgraph Location"


def _make_workstation(asset=None):
	_ensure_test_company()
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": frappe.generate_hash(length=8),
			"production_capacity": 1,
			"warehouse": "Work In Progress - _TC",
			"custom_asset": asset,
		}
	).insert(ignore_permissions=True)


def _account(account_type):
	"""Resolve a real leaf Account under TEST_COMPANY by account_type rather
	than a hardcoded name - ERPNext's Standard Chart of Accounts template
	isn't identical across supported versions (confirmed: v15's leaf Fixed
	Asset account is "Capital Equipments", v16's restructured that same
	group under different leaf names entirely), so any literal account name
	is a version-specific guess. account_type itself is a fixed field on
	the Account doctype, not a per-template detail, so this is stable
	across versions in a way a name never is."""
	account = frappe.db.get_value(
		"Account", {"company": TEST_COMPANY, "account_type": account_type, "is_group": 0}, "name"
	)
	if not account:
		frappe.throw(f"No {account_type} account found for {TEST_COMPANY} - Standard CoA not applied?")
	return account


def _make_asset():
	"""Deliberately not erpnext.assets.doctype.asset.test_asset.create_asset:
	that helper's create_asset_category() hardcodes account names
	("_Test Fixed Asset - _TC" etc.) that only exist when ERPNext's own real
	test-bootstrap Company fixture has run - floorgraph's CI never runs
	that (see floorgraph/tests/__init__.py), it only has the Standard-CoA
	`_Test Company` built by ensure_test_company().
	"""
	_ensure_test_company()

	if not frappe.db.exists("Location", _LOCATION):
		frappe.get_doc({"doctype": "Location", "location_name": _LOCATION}).insert(ignore_permissions=True)

	if not frappe.db.exists("Asset Category", _ASSET_CATEGORY):
		frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": _ASSET_CATEGORY,
				"accounts": [
					{
						"company_name": TEST_COMPANY,
						"fixed_asset_account": _account("Fixed Asset"),
						"accumulated_depreciation_account": _account("Accumulated Depreciation"),
						"depreciation_expense_account": _account("Depreciation"),
					}
				],
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Item", _ASSET_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": _ASSET_ITEM,
				"item_name": _ASSET_ITEM,
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_fixed_asset": 1,
				"asset_category": _ASSET_CATEGORY,
			}
		).insert(ignore_permissions=True)

	asset = frappe.get_doc(
		{
			"doctype": "Asset",
			"asset_name": f"Test Asset {frappe.generate_hash(length=6)}",
			"asset_category": _ASSET_CATEGORY,
			"item_code": _ASSET_ITEM,
			"company": TEST_COMPANY,
			"location": _LOCATION,
			"purchase_date": getdate(),
			"gross_purchase_amount": 100000,
			"purchase_amount": 100000,
			# net_purchase_amount doesn't exist on v15's Asset doctype at all
			# (silently ignored there) but is a separate, required-on-validate
			# field on v16's - see asset.py's validate_asset_values().
			"net_purchase_amount": 100000,
			"available_for_use_date": getdate(),
			"is_existing_asset": 1,
			"asset_quantity": 1,
		}
	)
	asset.insert(ignore_permissions=True)
	asset.submit()
	return asset


class TestRequestMaintenance(FloorgraphTestCase):
	def test_creates_draft_asset_repair_against_linked_asset(self):
		asset = _make_asset()
		workstation = _make_workstation(asset=asset.name)

		log_name = request_action(
			"request_maintenance",
			params={"workstation": workstation.name, "description": "Bearing noise on spindle"},
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Executed")

		result = frappe.parse_json(action_log.result)
		repair = frappe.get_doc("Asset Repair", result["asset_repair"])

		self.assertEqual(repair.asset, asset.name)
		self.assertEqual(repair.description, "Bearing noise on spindle")
		self.assertEqual(repair.docstatus, 0)  # draft - a human submits it, not the handler
		self.assertEqual(repair.company, TEST_COMPANY)  # fetch_from asset.company

	def test_resolves_workstation_from_reference_doctype(self):
		"""Matches how an Agent Rule watching Workstation would call this -
		no explicit `workstation` param, just reference_doctype/reference_name."""
		asset = _make_asset()
		workstation = _make_workstation(asset=asset.name)

		log_name = request_action(
			"request_maintenance",
			params={"description": "Vibration threshold exceeded"},
			reference_doctype="Workstation",
			reference_name=workstation.name,
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Executed")
		result = frappe.parse_json(action_log.result)
		self.assertEqual(result["workstation"], workstation.name)
		self.assertEqual(result["asset"], asset.name)

	def test_fails_clearly_without_linked_asset(self):
		workstation = _make_workstation(asset=None)

		log_name = request_action(
			"request_maintenance",
			params={"workstation": workstation.name, "description": "Anything"},
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Failed")
		self.assertIn("no linked Asset", action_log.error)
		self.assertEqual(frappe.db.count("Asset Repair", {"asset": ["is", "not set"]}), 0)

	def test_agent_sourced_request_gets_auto_generated_description(self):
		"""Regression test for a real gap found verifying this live: Agent
		Rule's scheduled evaluator (agent/engine.py) never passes params at
		all, so an Agent-sourced request has no description to give - it
		must not fail the way a description-less Human request correctly
		does."""
		asset = _make_asset()
		workstation = _make_workstation(asset=asset.name)

		log_name = request_action_as_agent(
			"request_maintenance",
			reference_doctype="Workstation",
			reference_name=workstation.name,
		)
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Executed")
		result = frappe.parse_json(action_log.result)
		repair = frappe.get_doc("Asset Repair", result["asset_repair"])
		self.assertIn("Agent", repair.description)
		self.assertIn(workstation.name, repair.description)

	def test_fails_without_description(self):
		asset = _make_asset()
		workstation = _make_workstation(asset=asset.name)

		log_name = request_action("request_maintenance", params={"workstation": workstation.name})
		approve_action(log_name)

		action_log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(action_log.status, "Failed")
		self.assertIn("description is required", action_log.error)
