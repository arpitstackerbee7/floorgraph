# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from floorgraph.utils import resolve_company


class MachineEventSource(Document):
	def validate(self):
		if not self.workstation:
			return
		# company is its own mandatory field here (Workstation has no company
		# of its own - see floorgraph.utils' module docstring), not derived
		# from workstation. The multi-company scoping this app relies on
		# elsewhere (Agent Rule matching, OEE) implicitly assumes the two
		# agree; without this check they could silently diverge (e.g. a
		# Workstation moved to a different Warehouse/Company after this
		# record was created).
		workstation_company = resolve_company(workstation=self.workstation)
		if workstation_company and workstation_company != self.company:
			frappe.throw(
				_(
					"Workstation {0} belongs to Company {1}, not {2} - set Company to match "
					"the Workstation, or choose a Workstation in {2}."
				).format(self.workstation, workstation_company, self.company)
			)
