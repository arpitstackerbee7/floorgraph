# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from floorgraph.utils import resolve_company_or_throw


class ChangeoverRule(Document):
	def before_insert(self):
		self.company = resolve_company_or_throw(workstation=self.workstation)
