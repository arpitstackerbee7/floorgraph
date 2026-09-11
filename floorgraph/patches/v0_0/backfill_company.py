# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

"""Backfills the `company` field added to every workstation/job-card-scoped
floorgraph doctype (see floorgraph.utils for why Workstation itself needs a
warehouse-based lookup). Runs post_model_sync, so the column already exists
by the time this executes.

Best-effort, same as the runtime resolver: a row this can't resolve (no
warehouse on its Workstation, no Job Card, an Agent Rule/Machine Event
Source with no Workstation to infer from) is left blank and counted, not
raised - one unresolvable row shouldn't abort the whole migration. Existing
installs with unresolved rows should fill in Company by hand afterward
(the new field is mandatory going forward, but this patch runs ahead of
that validation on already-persisted rows).
"""

import frappe

from floorgraph.utils import resolve_company


def execute():
	_backfill_workstation_scoped("Downtime Log")
	_backfill_workstation_scoped("Andon Alert")
	_backfill_workstation_scoped("Workstation OEE Log")
	_backfill_schedule_slot()
	_backfill_scheduling_run()
	_backfill_machine_event_source()
	_backfill_machine_event_log()
	_backfill_action_log()


def _unresolved(doctype):
	return frappe.get_all(doctype, filters={"company": ["is", "not set"]}, fields=["name"])


def _backfill_workstation_scoped(doctype):
	has_job_card = frappe.get_meta(doctype).has_field("job_card")
	resolved, skipped = 0, 0
	for row in _unresolved(doctype):
		if has_job_card:
			workstation, job_card = frappe.db.get_value(doctype, row.name, ["workstation", "job_card"])
		else:
			workstation, job_card = frappe.db.get_value(doctype, row.name, "workstation"), None
		company = resolve_company(workstation=workstation, job_card=job_card)
		if company:
			frappe.db.set_value(doctype, row.name, "company", company, update_modified=False)
			resolved += 1
		else:
			skipped += 1
	_log(doctype, resolved, skipped)


def _backfill_schedule_slot():
	resolved, skipped = 0, 0
	for row in _unresolved("Schedule Slot"):
		workstation, job_card = frappe.db.get_value("Schedule Slot", row.name, ["workstation", "job_card"])
		company = resolve_company(workstation=workstation, job_card=job_card)
		if company:
			frappe.db.set_value("Schedule Slot", row.name, "company", company, update_modified=False)
			resolved += 1
		else:
			skipped += 1
	_log("Schedule Slot", resolved, skipped)


def _backfill_scheduling_run():
	# Scheduling Run has no workstation/job_card of its own - read it off any
	# Schedule Slot from that run (backfilled above, so this must run after).
	resolved, skipped = 0, 0
	for row in _unresolved("Scheduling Run"):
		company = frappe.db.get_value(
			"Schedule Slot", {"scheduling_run": row.name, "company": ["is", "set"]}, "company"
		)
		if company:
			frappe.db.set_value("Scheduling Run", row.name, "company", company, update_modified=False)
			resolved += 1
		else:
			skipped += 1
	_log("Scheduling Run", resolved, skipped)


def _backfill_machine_event_source():
	# Config, not derived at runtime - but for an existing row we can still
	# take a best guess from its (optional) Workstation link rather than
	# leaving every pre-upgrade device with a blank, now-mandatory Company.
	resolved, skipped = 0, 0
	for row in _unresolved("Machine Event Source"):
		workstation = frappe.db.get_value("Machine Event Source", row.name, "workstation")
		company = resolve_company(workstation=workstation) if workstation else None
		if company:
			frappe.db.set_value("Machine Event Source", row.name, "company", company, update_modified=False)
			resolved += 1
		else:
			skipped += 1
	_log("Machine Event Source", resolved, skipped)
	if skipped:
		frappe.log_error(
			title="floorgraph.patches.backfill_company",
			message=(
				f"{skipped} Machine Event Source record(s) have no Company and no Workstation to "
				"infer one from. Company is now mandatory on this doctype - set it manually before "
				"editing/saving these records."
			),
		)


def _backfill_machine_event_log():
	resolved = 0
	for row in _unresolved("Machine Event Log"):
		source = frappe.db.get_value("Machine Event Log", row.name, "machine_event_source")
		company = frappe.db.get_value("Machine Event Source", source, "company")
		if company:
			frappe.db.set_value("Machine Event Log", row.name, "company", company, update_modified=False)
			resolved += 1
	_log("Machine Event Log", resolved, 0)


def _backfill_action_log():
	# Best-effort, same as request_action() itself: Action Log's reference is
	# generic, so plenty of existing rows will legitimately stay blank.
	resolved, skipped = 0, 0
	for row in _unresolved("Action Log"):
		params, reference_doctype, reference_name = frappe.db.get_value(
			"Action Log", row.name, ["params", "reference_doctype", "reference_name"]
		)
		workstation = None
		if params:
			try:
				workstation = (frappe.parse_json(params) or {}).get("workstation")
			except Exception:
				workstation = None
		company = resolve_company(
			workstation=workstation, reference_doctype=reference_doctype, reference_name=reference_name
		)
		if company:
			frappe.db.set_value("Action Log", row.name, "company", company, update_modified=False)
			resolved += 1
		else:
			skipped += 1
	_log("Action Log", resolved, skipped)


def _log(doctype, resolved, skipped):
	frappe.logger().info(
		f"floorgraph.patches.backfill_company: {doctype} resolved={resolved} skipped={skipped}"
	)
