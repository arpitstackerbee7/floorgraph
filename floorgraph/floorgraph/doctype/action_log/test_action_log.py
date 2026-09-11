# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe
from frappe import _

from floorgraph.actions.engine import approve_action, reject_action, request_action
from floorgraph.tests import FloorgraphTestCase

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# non-floorgraph doctypes. Covers User/DocType too, not just Company -
# User's link graph transitively reaches ERPNext's entire doctype closure,
# which a real CI failure surfaced. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "DocType", "Company"]

TEST_ROLE = "Test Floorgraph Restricted Action"


def _echo_handler(action_log):
	return {"echoed": True}


def _boom_handler(action_log):
	frappe.throw(_("boom"))


def _make_action(handler, requires_approval, allowed_roles=None):
	name = frappe.generate_hash(length=10)
	frappe.get_doc(
		{
			"doctype": "Action",
			"action_name": name,
			"label": name,
			"handler": handler,
			"requires_approval": requires_approval,
			"allowed_roles": [{"role": role} for role in (allowed_roles or [])],
		}
	).insert(ignore_permissions=True)
	return name


def _make_user(email, roles=None):
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Test",
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	for role in roles or []:
		user.add_roles(role)
	return user


class TestActionLog(FloorgraphTestCase):
	def test_auto_execute_without_approval(self):
		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=0,
		)

		log_name = request_action(action_name)
		log = frappe.get_doc("Action Log", log_name)

		self.assertEqual(log.status, "Executed")
		self.assertEqual(frappe.parse_json(log.result), {"echoed": True})
		self.assertIsNotNone(log.executed_on)

	def test_approve_executes_handler(self):
		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=1,
		)

		log_name = request_action(action_name)
		log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(log.status, "Pending")

		approve_action(log_name)
		log.reload()

		self.assertEqual(log.status, "Executed")
		self.assertEqual(log.approved_by, "Administrator")
		self.assertIsNotNone(log.approved_on)
		self.assertEqual(frappe.parse_json(log.result), {"echoed": True})

	def test_reject_does_not_execute(self):
		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=1,
		)

		log_name = request_action(action_name)
		reject_action(log_name, reason="not needed")
		log = frappe.get_doc("Action Log", log_name)

		self.assertEqual(log.status, "Rejected")
		self.assertEqual(log.rejected_by, "Administrator")
		self.assertEqual(log.rejection_reason, "not needed")
		self.assertIsNone(log.executed_on)
		self.assertFalse(log.result)

	def test_failed_handler_is_recorded_not_raised(self):
		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._boom_handler",
			requires_approval=0,
		)

		log_name = request_action(action_name)
		log = frappe.get_doc("Action Log", log_name)

		self.assertEqual(log.status, "Failed")
		self.assertIn("boom", log.error)

	def test_direct_save_cannot_forge_status(self):
		"""Found live in QA: read_only=1 in the doctype JSON only hides a
		field in the Desk form, it does nothing server-side. Without this
		guard, `doc.status = "Executed"; doc.save()` silently succeeded and
		flipped status without the handler ever running - a falsified audit
		trail claiming an action executed when nothing happened. Every real
		transition goes through db_set() in actions/engine.py, which
		bypasses validate() entirely, so this guard cannot break the
		legitimate approve/reject/execute path (covered by the other tests
		in this file)."""
		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=1,
		)
		log_name = request_action(action_name)

		log = frappe.get_doc("Action Log", log_name)
		log.status = "Executed"
		self.assertRaises(frappe.PermissionError, log.save)

		log.reload()
		self.assertEqual(log.status, "Pending")

	def test_permission_denied_for_unlisted_role(self):
		if not frappe.db.exists("Role", TEST_ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": TEST_ROLE}).insert(ignore_permissions=True)

		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=1,
			allowed_roles=[TEST_ROLE],
		)

		user = _make_user("floorgraph-test-no-role@example.com", roles=[])
		frappe.set_user(user.name)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

		self.assertRaises(frappe.PermissionError, request_action, action_name)
		self.assertEqual(frappe.db.count("Action Log", {"action": action_name}), 0)

	def test_permission_allowed_for_listed_role(self):
		if not frappe.db.exists("Role", TEST_ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": TEST_ROLE}).insert(ignore_permissions=True)

		action_name = _make_action(
			handler="floorgraph.floorgraph.doctype.action_log.test_action_log._echo_handler",
			requires_approval=0,
			allowed_roles=[TEST_ROLE],
		)

		user = _make_user("floorgraph-test-with-role@example.com", roles=[TEST_ROLE])
		frappe.set_user(user.name)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

		log_name = request_action(action_name)
		log = frappe.get_doc("Action Log", log_name)
		self.assertEqual(log.status, "Executed")
		self.assertEqual(log.requested_by, user.name)
