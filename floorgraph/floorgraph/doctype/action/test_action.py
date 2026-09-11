# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.tests import FloorgraphTestCase

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# Role - reached via the allowed_roles child table's own link field, not a
# direct link on Action itself, but Frappe's dependency walker follows
# table fields too. Blocked defensively (see test_action_log.py for why
# "just a core Frappe doctype" isn't a safe assumption on its own). No-op
# on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Role"]


def _make_action(handler, name=None):
	name = name or frappe.generate_hash(length=10)
	return frappe.get_doc(
		{
			"doctype": "Action",
			"action_name": name,
			"label": name,
			"handler": handler,
		}
	).insert(ignore_permissions=True)


def _echo_handler(action_log):
	return {"echoed": True}


class TestAction(FloorgraphTestCase):
	def test_handler_that_resolves_to_a_callable_is_accepted(self):
		action = _make_action("floorgraph.floorgraph.doctype.action.test_action._echo_handler")
		self.assertEqual(action.handler, "floorgraph.floorgraph.doctype.action.test_action._echo_handler")

	def test_handler_that_does_not_exist_is_rejected_at_save(self):
		# Without this, a typo (or a since-renamed/removed function) here
		# only surfaces the first time the Action is actually requested, as a
		# Failed Action Log - see actions/engine.py._execute().
		with self.assertRaises(frappe.ValidationError):
			_make_action("floorgraph.floorgraph.doctype.action.test_action._does_not_exist")

	def test_handler_that_is_not_callable_is_rejected_at_save(self):
		with self.assertRaises(frappe.ValidationError):
			_make_action("floorgraph.floorgraph.doctype.action.test_action.IGNORE_TEST_RECORD_DEPENDENCIES")
