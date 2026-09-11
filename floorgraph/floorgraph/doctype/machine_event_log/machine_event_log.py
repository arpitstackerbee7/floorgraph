# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from floorgraph.utils import guard_immutable


class MachineEventLog(Document):
	def before_insert(self):
		self.company = frappe.db.get_value("Machine Event Source", self.machine_event_source, "company")

	def validate(self):
		# Every field is read_only=1 in the doctype JSON (full immutability is
		# the intent); the webhook (iot/webhook.py) only ever writes
		# resulting_action_log/error via db_set() after insert, which bypasses
		# validate() - a normal Desk save is never legitimate here.
		guard_immutable(self, "Machine Event Log records are immutable after creation.")
