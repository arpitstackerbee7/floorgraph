# Copyright (c) 2026, Sidharth P V and Contributors
# See license.txt

import frappe

from floorgraph.tests import FloorgraphTestCase
from floorgraph.tests.fixtures import TEST_COMPANY
from floorgraph.tests.fixtures import ensure_test_company as _ensure_test_company

# See floorgraph/tests/__init__.py: on Frappe v16+, avoids walking into
# ERPNext's Workstation/Company doctypes, which trips a real ERPNext v16
# bug on a bare site. No-op on v15.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Workstation", "Company"]

SECOND_COMPANY = "_Test MES Company 2"
SECOND_COMPANY_ABBR = "_TMC2"


def _ensure_second_company():
	# A distinct Company (own abbr, own default warehouses) is what makes
	# workstation.company != self.company reachable at all - needed to
	# exercise the cross-check, not just the happy path.
	_ensure_test_company(company_name=SECOND_COMPANY, abbr=SECOND_COMPANY_ABBR)


def _make_workstation(warehouse):
	suffix = frappe.generate_hash(length=8)
	return frappe.get_doc(
		{
			"doctype": "Workstation",
			"workstation_name": f"Test MES WS {suffix}",
			"production_capacity": 1,
			"warehouse": warehouse,
		}
	).insert(ignore_permissions=True)


def _make_source(company, workstation=None, secret="super-secret"):
	return frappe.get_doc(
		{
			"doctype": "Machine Event Source",
			"source_name": frappe.generate_hash(length=10),
			"secret": secret,
			"company": company,
			"workstation": workstation,
		}
	).insert(ignore_permissions=True)


class TestMachineEventSource(FloorgraphTestCase):
	def test_workstation_in_a_different_company_is_rejected(self):
		_ensure_test_company()
		_ensure_second_company()
		ws = _make_workstation(f"Work In Progress - {SECOND_COMPANY_ABBR}")

		with self.assertRaises(frappe.ValidationError):
			_make_source(company=TEST_COMPANY, workstation=ws.name)

	def test_workstation_matching_company_is_accepted(self):
		_ensure_test_company()
		ws = _make_workstation("Work In Progress - _TC")

		source = _make_source(company=TEST_COMPANY, workstation=ws.name)

		self.assertEqual(source.workstation, ws.name)

	def test_no_workstation_skips_the_company_check(self):
		_ensure_second_company()
		source = _make_source(company=SECOND_COMPANY)
		self.assertFalse(source.workstation)
