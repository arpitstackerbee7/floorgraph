# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import time_diff_in_hours

from floorgraph.utils import resolve_company_or_throw


class DowntimeLog(Document):
	def before_insert(self):
		self.company = resolve_company_or_throw(workstation=self.workstation, job_card=self.job_card)

	def validate(self):
		if self.to_time and self.from_time and self.to_time < self.from_time:
			frappe.throw(_("To Time cannot be before From Time"))

		if self.to_time and self.from_time:
			self.duration_minutes = round(time_diff_in_hours(self.to_time, self.from_time) * 60, 2)
