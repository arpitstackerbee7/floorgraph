# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

"""Shared helpers with no single doctype to live in.

Company resolution: shared by every doctype that needs to be scoped to one
ERPNext Company. This app is a distributed product, not a single-site
internal tool - it has to work correctly on a multi-company ERPNext
install, which core ERPNext itself only partly supports for this domain:
`Job Card` carries `company` directly, but `Workstation` does not (only a
`warehouse` link, and `Warehouse.company` is mandatory). Every doctype here
that keys off a workstation resolves through that chain instead of
duplicating it.
"""

import frappe
from frappe import _
from frappe.utils import cint


def is_workstation_holiday(holiday_list, date):
	"""Whether `date` is a holiday per this Workstation's holiday_list, honoring
	Manufacturing Settings.allow_production_on_holidays. Shared by the
	scheduler (skips holidays when placing slots) and OEE (a holiday isn't a
	planned working day at all - it must not count as full planned-but-
	unavailable time, or Availability reads 100% for a day the plant was
	closed). Lives here, not in either engine, since scheduling/engine.py and
	oee/engine.py already import from each other and can't import each other
	again without a cycle."""
	if not holiday_list:
		return False
	if cint(frappe.db.get_single_value("Manufacturing Settings", "allow_production_on_holidays")):
		return False
	from erpnext.support.doctype.issue.issue import get_holidays

	return date in set(get_holidays(holiday_list))


def resolve_company(*, workstation=None, job_card=None, reference_doctype=None, reference_name=None):
	"""Best-effort company lookup. Returns None if nothing resolves - callers
	that need a hard guarantee should use resolve_company_or_throw instead.

	Resolution order:
	  1. job_card.company (Job Card carries company directly)
	  2. workstation.warehouse.company (Workstation itself has no company)
	  3. reference_doctype/reference_name, recursing through whichever of
	     company/job_card/workstation that doctype happens to have
	"""
	if job_card:
		company = frappe.db.get_value("Job Card", job_card, "company")
		if company:
			return company

	if workstation:
		warehouse = frappe.db.get_value("Workstation", workstation, "warehouse")
		if warehouse:
			company = frappe.db.get_value("Warehouse", warehouse, "company")
			if company:
				return company

	if reference_doctype and reference_name:
		if reference_doctype == "Workstation":
			return resolve_company(workstation=reference_name)

		meta = frappe.get_meta(reference_doctype)
		if meta.has_field("company"):
			company = frappe.db.get_value(reference_doctype, reference_name, "company")
			if company:
				return company
		if meta.has_field("job_card"):
			jc = frappe.db.get_value(reference_doctype, reference_name, "job_card")
			if jc:
				company = resolve_company(job_card=jc)
				if company:
					return company
		if meta.has_field("workstation"):
			ws = frappe.db.get_value(reference_doctype, reference_name, "workstation")
			if ws:
				company = resolve_company(workstation=ws)
				if company:
					return company

	return None


def resolve_company_or_throw(*, workstation=None, job_card=None, reference_doctype=None, reference_name=None):
	company = resolve_company(
		workstation=workstation,
		job_card=job_card,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	if not company:
		frappe.throw(
			_(
				"Could not determine Company for Workstation {0} - configure a warehouse on "
				"the Workstation (Warehouse.company is where this app reads it from), or supply "
				"a Job Card."
			).format(workstation or reference_name)
		)
	return company


def guard_immutable(doc, message, guarded_fields=None):
	"""Blocks a direct Desk save from changing an audit-trail doctype whose
	real writes only ever happen via db_set() elsewhere (which bypasses this
	entirely) - read_only=1 in the doctype JSON only hides the fields from
	the form, it does nothing server-side without this.

	guarded_fields=None means every field is immutable once the doc exists.
	Pass a tuple to guard only those fields, leaving the rest genuinely
	user-editable (e.g. Scheduling Run's own remarks/direction).
	"""
	if doc.is_new():
		return
	if guarded_fields is None:
		frappe.throw(_(message), frappe.PermissionError)
		return
	changed = [f for f in guarded_fields if doc.has_value_changed(f)]
	if changed:
		frappe.throw(_(message).format(", ".join(changed)), frappe.PermissionError)
