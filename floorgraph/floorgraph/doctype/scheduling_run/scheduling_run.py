# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from floorgraph.utils import guard_immutable

# These are the only fields the scheduling engine (scheduling/engine.py)
# itself ever writes, always via db_set() - never a normal save.
# `direction`/`anchor_datetime`/`remarks` are genuinely user-editable
# metadata, so this doctype isn't fully immutable - only these fields are.
_GUARDED_FIELDS = ("status", "company", "job_cards_scheduled", "previous_slots_snapshot")


class SchedulingRun(Document):
	def validate(self):
		guard_immutable(
			self,
			"{0} can only change via the scheduling engine, not a direct save.",
			guarded_fields=_GUARDED_FIELDS,
		)
