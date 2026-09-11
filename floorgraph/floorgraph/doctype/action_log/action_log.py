# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from floorgraph.utils import guard_immutable


class ActionLog(Document):
	def validate(self):
		# Real state transitions (approve/reject/execute) go through db_set()
		# in actions/engine.py, which bypasses validate() entirely - a normal
		# Desk save is never legitimate on an existing Action Log.
		guard_immutable(
			self,
			"Action Log records are immutable after creation - status changes only through approve/reject.",
		)
