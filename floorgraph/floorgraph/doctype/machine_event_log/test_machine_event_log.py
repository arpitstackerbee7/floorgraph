# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import hashlib
import hmac
import json

import frappe

from floorgraph.iot.webhook import _ingest_event
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


def _make_action(requires_approval, name=None):
	name = name or frappe.generate_hash(length=10)
	frappe.get_doc(
		{
			"doctype": "Action",
			"action_name": name,
			"label": name,
			"handler": "floorgraph.floorgraph.doctype.machine_event_log.test_machine_event_log._echo_handler",
			"requires_approval": requires_approval,
		}
	).insert(ignore_permissions=True)
	return name


def _make_source(secret="super-secret", enabled=1, allowed_actions=None):
	_ensure_test_company()
	name = frappe.generate_hash(length=10)
	frappe.get_doc(
		{
			"doctype": "Machine Event Source",
			"source_name": name,
			"secret": secret,
			"enabled": enabled,
			"company": TEST_COMPANY,
			"allowed_actions": [{"action": action} for action in (allowed_actions or [])],
		}
	).insert(ignore_permissions=True)
	return name


def _sign(secret, raw_body):
	return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def _body(**fields):
	return json.dumps(fields).encode()


class TestMachineEventLog(FloorgraphTestCase):
	# Counts below are scoped by machine_event_source (or action), not bare
	# frappe.db.count("Machine Event Log") - FloorgraphTestCase's rollback
	# isolates test *classes*, not individual test *methods* within one
	# class, so an unscoped table-wide count would also see rows other
	# methods in this class have already created. Same pattern as
	# test_agent_rule.py.

	def test_valid_event_creates_pending_action_log(self):
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(
			source_name=source_name,
			device_event_id="evt-1",
			event_type="threshold_breach",
			action=action_name,
		)
		result = _ingest_event(body, _sign("super-secret", body))

		self.assertEqual(result["status"], "accepted")
		log = frappe.get_doc("Machine Event Log", result["machine_event_log"])
		self.assertEqual(log.machine_event_source, source_name)
		self.assertFalse(log.error)

		action_log = frappe.get_doc("Action Log", log.resulting_action_log)
		self.assertEqual(action_log.source, "Sensor")
		self.assertEqual(action_log.status, "Pending")
		self.assertIsNone(action_log.requested_by)

	def test_valid_event_auto_executes_when_action_does_not_require_approval(self):
		action_name = _make_action(requires_approval=0)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(
			source_name=source_name,
			device_event_id="evt-1",
			event_type="threshold_breach",
			action=action_name,
		)
		result = _ingest_event(body, _sign("super-secret", body))

		log = frappe.get_doc("Machine Event Log", result["machine_event_log"])
		action_log = frappe.get_doc("Action Log", log.resulting_action_log)
		self.assertEqual(action_log.status, "Executed")

	def test_unknown_source_rejected_no_log_created(self):
		body = _body(
			source_name="does-not-exist",
			device_event_id="evt-1",
			event_type="x",
			action="does-not-exist-either",
		)
		self.assertRaises(frappe.AuthenticationError, _ingest_event, body, _sign("whatever", body))
		self.assertEqual(frappe.db.count("Machine Event Log", {"machine_event_source": "does-not-exist"}), 0)

	def test_disabled_source_rejected_no_log_created(self):
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(enabled=0, allowed_actions=[action_name])

		body = _body(source_name=source_name, device_event_id="evt-1", event_type="x", action=action_name)
		self.assertRaises(frappe.AuthenticationError, _ingest_event, body, _sign("super-secret", body))
		self.assertEqual(frappe.db.count("Machine Event Log", {"machine_event_source": source_name}), 0)

	def test_bad_signature_rejected_no_log_created(self):
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(source_name=source_name, device_event_id="evt-1", event_type="x", action=action_name)
		self.assertRaises(frappe.AuthenticationError, _ingest_event, body, _sign("wrong-secret", body))
		self.assertEqual(frappe.db.count("Machine Event Log", {"machine_event_source": source_name}), 0)

	def test_missing_required_field_rejected_no_log_created(self):
		source_name = _make_source()
		body = _body(source_name=source_name, device_event_id="evt-1")  # missing event_type/action
		self.assertRaises(frappe.ValidationError, _ingest_event, body, _sign("super-secret", body))
		self.assertEqual(frappe.db.count("Machine Event Log", {"machine_event_source": source_name}), 0)

	def test_action_not_in_allowed_actions_logs_error_no_action_log(self):
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[])  # deny-by-default: nothing allowed

		body = _body(source_name=source_name, device_event_id="evt-1", event_type="x", action=action_name)
		result = _ingest_event(body, _sign("super-secret", body))

		log = frappe.get_doc("Machine Event Log", result["machine_event_log"])
		self.assertIn("PermissionError", log.error)
		self.assertFalse(log.resulting_action_log)
		self.assertEqual(frappe.db.count("Action Log", {"action": action_name}), 0)

	def test_falsy_but_present_device_event_id_is_accepted(self):
		# device_event_id is device-assigned and may legitimately be 0 (or any
		# other falsy value) - a truthiness check on required fields would
		# wrongly reject this as malformed.
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(source_name=source_name, device_event_id=0, event_type="x", action=action_name)
		result = _ingest_event(body, _sign("super-secret", body))

		self.assertEqual(result["status"], "accepted")
		log = frappe.get_doc("Machine Event Log", result["machine_event_log"])
		self.assertEqual(log.device_event_id, "0")

	def test_duplicate_device_event_id_is_noop(self):
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(source_name=source_name, device_event_id="evt-1", event_type="x", action=action_name)
		first = _ingest_event(body, _sign("super-secret", body))
		second = _ingest_event(body, _sign("super-secret", body))

		self.assertEqual(second["status"], "duplicate")
		self.assertEqual(second["machine_event_log"], first["machine_event_log"])
		self.assertEqual(frappe.db.count("Machine Event Log", {"machine_event_source": source_name}), 1)
		self.assertEqual(frappe.db.count("Action Log", {"action": action_name}), 1)

	def test_direct_save_cannot_forge_result(self):
		"""Found live in QA: read_only=1 in the doctype JSON only hides a
		field in the Desk form, it does nothing server-side. Without this
		guard, a direct save could forge `error`/`resulting_action_log` (or
		any other field) after creation - the webhook only ever writes
		those via db_set(), which bypasses validate() entirely."""
		action_name = _make_action(requires_approval=1)
		source_name = _make_source(allowed_actions=[action_name])

		body = _body(
			source_name=source_name, device_event_id="evt-immutable", event_type="x", action=action_name
		)
		result = _ingest_event(body, _sign("super-secret", body))

		log = frappe.get_doc("Machine Event Log", result["machine_event_log"])
		log.error = "forged error"
		self.assertRaises(frappe.PermissionError, log.save)

		log.reload()
		self.assertFalse(log.error)
