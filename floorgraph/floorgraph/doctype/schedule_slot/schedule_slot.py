# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from floorgraph.scheduling.engine import (
	validate_no_overlap,
	validate_precedence,
	validate_slot_within_working_hours,
)
from floorgraph.utils import resolve_company_or_throw


class ScheduleSlot(Document):
	def before_insert(self):
		if not self.company:
			self.company = resolve_company_or_throw(workstation=self.workstation, job_card=self.job_card)

	def validate(self):
		# Defense in depth: the scheduler (scheduling/engine.py) and
		# reassign_job_card already construct valid placements and re-check
		# via validate_reassignment before saving, but without this, any
		# direct Desk edit or API save of a Schedule Slot - which a System
		# Manager's full write permission on this doctype allows - bypassed
		# every one of these invariants (overlap, working hours, precedence).
		validate_slot_within_working_hours(self.workstation, self.start_time, self.end_time)
		validate_no_overlap(self.workstation, self.start_time, self.end_time, exclude_slot=self.name)
		validate_precedence(self, self.start_time, self.end_time)
