# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Action(Document):
	def validate(self):
		# Confirms handler resolves to a real, callable dotted path now,
		# rather than only discovering a typo (or a since-renamed/removed
		# function) the first time this Action is requested -
		# actions/engine.py._execute() records that as a Failed Action Log,
		# not a save-time error. Doesn't restrict which module a handler may
		# point into: Action is System-Manager-only to create/edit, the same
		# trust boundary as any other site-wide config only they can touch.
		try:
			target = frappe.get_attr(self.handler)
		except Exception:
			frappe.throw(_("{0} could not be imported.").format(self.handler))
		if not callable(target):
			frappe.throw(_("{0} is not callable.").format(self.handler))
