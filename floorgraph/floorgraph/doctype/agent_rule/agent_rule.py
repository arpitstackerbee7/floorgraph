# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Standard Frappe columns every doctype has that never appear in
# frappe.get_meta(doctype).has_field() (that only covers doctype-defined
# DocFields) but are still valid frappe.get_all() filter fields - the exact
# way agent/engine.py._matching_records() uses Agent Rule.field.
_STANDARD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}


class AgentRule(Document):
	def validate(self):
		self._validate_field_exists()
		self._validate_filters_json()

	def _validate_field_exists(self):
		# Without this, a typo in `field` doesn't fail here - it fails inside
		# the hourly evaluator's frappe.get_all() call instead (agent/engine.py
		# ._matching_records()), as an unhandled SQL error with no per-rule
		# isolation there, at whatever time the scheduler happens to run it.
		if self.field in _STANDARD_FIELDS:
			return
		if not frappe.get_meta(self.source_doctype).has_field(self.field):
			frappe.throw(_("{0} is not a field on {1}").format(self.field, self.source_doctype))

	def _validate_filters_json(self):
		if not self.filters:
			return
		try:
			parsed = frappe.parse_json(self.filters)
		except Exception:
			frappe.throw(_("Filters must be valid JSON"))
		if not isinstance(parsed, dict):
			frappe.throw(_('Filters must be a JSON object (e.g. {"field": "value"})'))
