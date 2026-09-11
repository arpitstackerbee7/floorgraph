# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.agent.engine import evaluate_agent_rules
from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# non-floorgraph doctypes (Company is the real ERPNext v16 bug; DocType is
# blocked defensively - see test_action_log.py for why "just a core Frappe
# doctype" isn't a safe assumption on its own). No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["DocType", "Company"]


def _echo_handler(action_log):
	return {"echoed": True}


def _make_action(requires_approval):
	name = frappe.generate_hash(length=10)
	frappe.get_doc(
		{
			"doctype": "Action",
			"action_name": name,
			"label": name,
			"handler": "floorgraph.floorgraph.doctype.agent_rule.test_agent_rule._echo_handler",
			"requires_approval": requires_approval,
		}
	).insert(ignore_permissions=True)
	return name


def _make_workstation(production_capacity=1):
	_ensure_test_company()
	suffix = frappe.generate_hash(length=8)
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": f"Test Agent WS {suffix}",
			"production_capacity": production_capacity,
			# Company resolves via Workstation.warehouse.company (Workstation
			# itself has no company field) - see floorgraph.utils.resolve_company.
			# Must match _make_rule's company below for _matching_records'
			# Workstation-source-doctype company filter to include it.
			"warehouse": "Work In Progress - _TC",
		}
	).insert(ignore_permissions=True)


def _make_rule(operator, threshold, target_action, filters=None, enabled=1, field="production_capacity"):
	name = frappe.generate_hash(length=10)
	frappe.get_doc(
		{
			"doctype": "Agent Rule",
			"rule_name": name,
			"enabled": enabled,
			"company": TEST_COMPANY,
			"source_doctype": "Workstation",
			"field": field,
			"operator": operator,
			"threshold": threshold,
			"filters": frappe.as_json(filters) if filters else None,
			"target_action": target_action,
		}
	).insert(ignore_permissions=True)
	return name


class TestAgentRule(FloorgraphTestCase):
	# Every rule below is scoped with filters={"workstation_name": ...} to
	# just the Workstation(s) each test creates: FloorgraphTestCase's
	# rollback isolates test *classes*, not individual test *methods*
	# (methods share a savepoint, rolled back only at class teardown), so
	# an unscoped rule would also match every other Workstation this
	# class's other methods created, plus the real persisted demo-fixture
	# Workstations (e.g. "Cutting Station 1") that always have
	# production_capacity=1. _matching_records() itself is correctly
	# unscoped (a real Agent Rule scans the whole table) - it's the tests
	# that must pin down which record they're asserting about.
	def test_rule_fires_and_forces_approval_even_when_action_does_not_require_it(self):
		# The property that matters: an Agent-sourced request always lands
		# Pending, even for an Action whose own requires_approval is 0.
		action_name = _make_action(requires_approval=0)
		ws = _make_workstation(production_capacity=1)
		_make_rule(
			operator="<",
			threshold=2,
			target_action=action_name,
			filters={"workstation_name": ws.workstation_name},
		)

		evaluate_agent_rules()

		log = frappe.get_doc(
			"Action Log",
			{"action": action_name, "reference_doctype": "Workstation", "reference_name": ws.name},
		)
		self.assertEqual(log.source, "Agent")
		self.assertEqual(log.status, "Pending")
		self.assertTrue(log.requires_approval)
		self.assertIsNone(log.requested_by)

	def test_non_matching_record_is_not_actioned(self):
		action_name = _make_action(requires_approval=0)
		ws = _make_workstation(production_capacity=5)
		_make_rule(
			operator="<",
			threshold=2,
			target_action=action_name,
			filters={"workstation_name": ws.workstation_name},
		)

		evaluate_agent_rules()

		self.assertEqual(frappe.db.count("Action Log", {"action": action_name}), 0)

	def test_disabled_rule_is_not_evaluated(self):
		action_name = _make_action(requires_approval=0)
		ws = _make_workstation(production_capacity=1)
		_make_rule(
			operator="<",
			threshold=2,
			target_action=action_name,
			filters={"workstation_name": ws.workstation_name},
			enabled=0,
		)

		evaluate_agent_rules()

		self.assertEqual(frappe.db.count("Action Log", {"action": action_name}), 0)

	def test_does_not_refire_while_a_request_is_already_outstanding(self):
		action_name = _make_action(requires_approval=0)
		ws = _make_workstation(production_capacity=1)
		_make_rule(
			operator="<",
			threshold=2,
			target_action=action_name,
			filters={"workstation_name": ws.workstation_name},
		)

		evaluate_agent_rules()
		evaluate_agent_rules()

		self.assertEqual(
			frappe.db.count(
				"Action Log",
				{"action": action_name, "reference_doctype": "Workstation", "reference_name": ws.name},
			),
			1,
		)

	def test_extra_filters_scope_the_scan(self):
		action_name = _make_action(requires_approval=0)
		in_scope = _make_workstation(production_capacity=1)
		out_of_scope = _make_workstation(production_capacity=1)
		_make_rule(
			operator="<",
			threshold=2,
			target_action=action_name,
			filters={"workstation_name": in_scope.workstation_name},
		)

		evaluate_agent_rules()

		# Scoped by action=action_name, not just reference_name: some other
		# test method in this class may have left its own (unrelated,
		# differently-scoped) Agent Rule visible - see the class docstring
		# comment above. What this test actually asserts is "did *this*
		# rule's scan respect its filters", which only the action_name it
		# targets can answer.
		self.assertTrue(
			frappe.db.exists(
				"Action Log",
				{
					"action": action_name,
					"reference_doctype": "Workstation",
					"reference_name": in_scope.name,
				},
			)
		)
		self.assertFalse(
			frappe.db.exists(
				"Action Log",
				{
					"action": action_name,
					"reference_doctype": "Workstation",
					"reference_name": out_of_scope.name,
				},
			)
		)

	def test_field_not_on_source_doctype_is_rejected_at_save(self):
		# Without this, a typo here doesn't fail until the hourly evaluator's
		# frappe.get_all() call hits an unhandled SQL error for an unknown
		# column - see agent/engine.py._matching_records().
		action_name = _make_action(requires_approval=0)
		with self.assertRaises(frappe.ValidationError):
			_make_rule(operator="<", threshold=2, target_action=action_name, field="not_a_real_field_xyz")

	def test_filters_must_be_valid_json(self):
		action_name = _make_action(requires_approval=0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Agent Rule",
					"rule_name": frappe.generate_hash(length=10),
					"enabled": 1,
					"company": TEST_COMPANY,
					"source_doctype": "Workstation",
					"field": "production_capacity",
					"operator": "<",
					"threshold": 2,
					"filters": "{not valid json",
					"target_action": action_name,
				}
			).insert(ignore_permissions=True)

	def test_filters_must_be_a_json_object(self):
		action_name = _make_action(requires_approval=0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Agent Rule",
					"rule_name": frappe.generate_hash(length=10),
					"enabled": 1,
					"company": TEST_COMPANY,
					"source_doctype": "Workstation",
					"field": "production_capacity",
					"operator": "<",
					"threshold": 2,
					"filters": "[1, 2, 3]",
					"target_action": action_name,
				}
			).insert(ignore_permissions=True)
