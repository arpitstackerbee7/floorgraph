# Copyright (c) 2026, Sidharth P V and contributors
# For license information, please see license.txt

"""Version-compatible test base class for every floorgraph test file.

`frappe.tests.utils.FrappeTestCase` (the class every floorgraph test used
through Frappe v15) is only kept in Frappe v16 via a deprecated compat shim
(see `frappe/deprecation_dumpster.py`, explicitly commented "remove alongside
get_tests_CompatFrappeTestCase"). That shim's test-preparation walks every
test_*.py file in the whole app up front, trying to build test records for
the full transitive doctype link graph - which pulls in ERPNext's own
`erpnext.tests.utils.BootStrapTestData` (an app-wide singleton instantiated
at import time) for anything that touches Company/Job Card/Workstation.
On a bare site that never ran ERPNext's own CI bootstrap, that singleton
fails outright - confirmed not specific to floorgraph by reproducing the
identical failure testing ERPNext's own `Company` doctype alone, with zero
floorgraph code involved.

`frappe.tests.IntegrationTestCase` (Frappe v16+) doesn't have this problem:
its own `setUpClass` only builds test records for the ONE doctype under
test (`cls.doctype`, auto-detected from the test file's own location), not
every test file in the app. Every floorgraph test file still needs
`IGNORE_TEST_RECORD_DEPENDENCIES` for its own doctype's direct ERPNext-core
link fields (Workstation, Job Card, Company, Item, Work Order) - floorgraph
never relies on Frappe's JSON/TOML auto-generated test records for any
doctype, so it's always safe to keep those out of the walk; every fixture
floorgraph tests need is built explicitly, in Python, in the test itself.

v15 has no `IntegrationTestCase` at all - `frappe.tests.utils.FrappeTestCase`
there is the real, non-deprecated, day-one base class, and v15's own test
runner has no equivalent eager whole-app-walk problem (floorgraph's CI has
always passed `--skip-test-records` there, which genuinely disables any
dependency-record generation on that version).
"""

try:
	from frappe.tests import IntegrationTestCase as FloorgraphTestCase
except ImportError:
	from frappe.tests.utils import FrappeTestCase as FloorgraphTestCase
