"""Dev/test fixture: a small steel bicycle-frame fabrication shop on ERPNext.

Gives floorgraph's later phases (Action engine, OEE, scheduler) real
Workstation/BOM/Work Order/Job Card records to operate on instead of an
empty database. Safe to re-run - every step checks for an existing record
before creating one.

Run with:
    bench --site <site> execute floorgraph.setup.demo_manufacturing.create_demo_manufacturing_data

Defaults to an India/INR company, matching this project's own dev bench. For a
different locale, pass country/currency explicitly (raw material rates below
are plain numbers against whatever currency you choose, not INR-specific):
    bench --site <site> execute floorgraph.setup.demo_manufacturing.create_demo_manufacturing_data \\
        --kwargs '{"country": "United States", "currency": "USD"}'
"""

import frappe
from frappe.utils import now_datetime

DEFAULT_COUNTRY = "India"
DEFAULT_CURRENCY = "INR"

COMPANY_NAME = "Ironclad Fabrication Works"
COMPANY_ABBR = "IFW"

OPERATIONS = ["Tube Cutting", "Frame Welding", "Powder Coating", "Final Assembly"]

WORKING_HOURS = [
	{"start_time": "08:00:00", "end_time": "12:00:00", "enabled": 1},
	{"start_time": "13:00:00", "end_time": "17:00:00", "enabled": 1},
]

WORKSTATIONS = [
	# hour_rate is a computed sum (labour + electricity + consumable + rent) on the
	# Workstation controller - set the components, not hour_rate itself.
	{
		"workstation_name": "Cutting Station 1",
		"hour_rate_labour": 90,
		"hour_rate_electricity": 40,
		"hour_rate_consumable": 15,
		"hour_rate_rent": 5,
		"production_capacity": 1,
		"warehouse_key": "Work In Progress",
	},
	{
		"workstation_name": "Welding Bay 2",
		"hour_rate_labour": 120,
		"hour_rate_electricity": 70,
		"hour_rate_consumable": 25,
		"hour_rate_rent": 5,
		"production_capacity": 1,
		"warehouse_key": "Work In Progress",
	},
	{
		"workstation_name": "Powder Coating Booth",
		"hour_rate_labour": 60,
		"hour_rate_electricity": 90,
		"hour_rate_consumable": 25,
		"hour_rate_rent": 5,
		"production_capacity": 1,
		"warehouse_key": "Work In Progress",
	},
	{
		"workstation_name": "Final Assembly Line 1",
		"hour_rate_labour": 140,
		"hour_rate_electricity": 10,
		"hour_rate_consumable": 5,
		"hour_rate_rent": 5,
		"production_capacity": 2,
		"warehouse_key": "Finished Goods",
	},
]

RAW_MATERIALS = [
	{"item_code": "MS Square Tube 25x25x2mm", "uom": "Kg", "rate": 85},
	{"item_code": "MS Round Rod 12mm", "uom": "Kg", "rate": 78},
	{"item_code": "Welding Electrode E7018 3.15mm", "uom": "Kg", "rate": 210},
	{"item_code": "Powder Coat Paint RAL 9005 Black", "uom": "Kg", "rate": 340},
	{"item_code": "Wheel Dropout Bracket - Forged Steel", "uom": "Nos", "rate": 45},
]

FG_ITEM_CODE = "Bicycle Frame - Trailblazer 26 (Steel)"
FG_ITEM_UOM = "Nos"

BUYING_PRICE_LIST = "Standard Buying"

BOM_ITEMS = [
	{"item_code": "MS Square Tube 25x25x2mm", "qty": 3.2, "uom": "Kg"},
	{"item_code": "MS Round Rod 12mm", "qty": 0.6, "uom": "Kg"},
	{"item_code": "Welding Electrode E7018 3.15mm", "qty": 0.15, "uom": "Kg"},
	{"item_code": "Powder Coat Paint RAL 9005 Black", "qty": 0.25, "uom": "Kg"},
	{"item_code": "Wheel Dropout Bracket - Forged Steel", "qty": 2, "uom": "Nos"},
]

BOM_OPERATIONS = [
	{"operation": "Tube Cutting", "workstation": "Cutting Station 1", "time_in_mins": 12},
	{"operation": "Frame Welding", "workstation": "Welding Bay 2", "time_in_mins": 35},
	{"operation": "Powder Coating", "workstation": "Powder Coating Booth", "time_in_mins": 20},
	{"operation": "Final Assembly", "workstation": "Final Assembly Line 1", "time_in_mins": 18},
]

WORK_ORDER_QTY = 50


def create_demo_manufacturing_data(country=DEFAULT_COUNTRY, currency=DEFAULT_CURRENCY):
	_install_baseline_fixtures(country)
	_disable_capacity_planning()

	company = _create_company(country, currency)
	warehouses = _get_default_warehouses(company)

	_create_operations()
	_create_workstations(warehouses)
	_create_items()
	_create_price_list(currency)
	_create_item_prices()

	bom_name = _create_bom(company)
	wo_name = _create_work_order(company, warehouses, bom_name)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit - run via `bench execute` (see module docstring), not a request/test transaction

	return {
		"company": company,
		"bom": bom_name,
		"work_order": wo_name,
		"job_cards": frappe.get_all("Job Card", filters={"work_order": wo_name}, pluck="name"),
	}


def _install_baseline_fixtures(country):
	if frappe.db.exists("Item Group", "All Item Groups"):
		return
	from erpnext.setup.setup_wizard.operations.install_fixtures import install

	install(country=country)


def _disable_capacity_planning():
	# ERPNext's built-in finite-capacity scheduling is exactly the gap floorgraph
	# fills later (Phase 3) - disable it here so Job Card creation doesn't depend
	# on Workstation working-hours/holiday setup that's out of scope for this fixture.
	settings = frappe.get_single("Manufacturing Settings")
	if not settings.disable_capacity_planning:
		settings.disable_capacity_planning = 1
		settings.save(ignore_permissions=True)


def _create_company(country, currency):
	if frappe.db.exists("Company", COMPANY_NAME):
		return COMPANY_NAME

	company = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": COMPANY_NAME,
			"abbr": COMPANY_ABBR,
			"default_currency": currency,
			"country": country,
		}
	)
	company.insert(ignore_permissions=True)
	return company.name


def _get_default_warehouses(company):
	warehouses = {}
	for wh_name in ("Stores", "Work In Progress", "Finished Goods"):
		warehouses[wh_name] = frappe.db.get_value(
			"Warehouse", {"warehouse_name": wh_name, "company": company}, "name"
		)
	return warehouses


def _create_operations():
	for operation in OPERATIONS:
		if not frappe.db.exists("Operation", operation):
			frappe.get_doc({"doctype": "Operation", "name": operation}).insert(ignore_permissions=True)


def _create_workstations(warehouses):
	for w in WORKSTATIONS:
		if frappe.db.exists("Workstation", w["workstation_name"]):
			_ensure_working_hours(w["workstation_name"])
			continue
		frappe.get_doc(
			{
				"doctype": "Workstation",
				"workstation_name": w["workstation_name"],
				"hour_rate_labour": w["hour_rate_labour"],
				"hour_rate_electricity": w["hour_rate_electricity"],
				"hour_rate_consumable": w["hour_rate_consumable"],
				"hour_rate_rent": w["hour_rate_rent"],
				"production_capacity": w["production_capacity"],
				"warehouse": warehouses.get(w["warehouse_key"]),
				"working_hours": WORKING_HOURS,
			}
		).insert(ignore_permissions=True)


def _ensure_working_hours(workstation_name):
	# Single shift, 08:00-12:00 and 13:00-17:00 (8 hours/day with a lunch
	# gap) - this is the real Planned Production Time source for the OEE
	# job, deliberately NOT derived from Job Card/Downtime Entry activity
	# (that would make Availability tautologically ~100%, see the OEE
	# engine's module docstring).
	doc = frappe.get_doc("Workstation", workstation_name)
	if doc.working_hours:
		return
	doc.set("working_hours", WORKING_HOURS)
	doc.save(ignore_permissions=True)


def _create_items():
	for rm in RAW_MATERIALS:
		_create_item(rm["item_code"], "Raw Material", rm["uom"])
	_create_item(FG_ITEM_CODE, "Products", FG_ITEM_UOM)


def _create_item(item_code, item_group, uom):
	if frappe.db.exists("Item", item_code):
		return
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": item_group,
			"stock_uom": uom,
			"is_stock_item": 1,
		}
	).insert(ignore_permissions=True)


def _create_price_list(currency):
	if frappe.db.exists("Price List", BUYING_PRICE_LIST):
		return
	frappe.get_doc(
		{
			"doctype": "Price List",
			"price_list_name": BUYING_PRICE_LIST,
			"currency": currency,
			"buying": 1,
			"enabled": 1,
		}
	).insert(ignore_permissions=True)


def _create_item_prices():
	for rm in RAW_MATERIALS:
		if frappe.db.exists("Item Price", {"item_code": rm["item_code"], "price_list": BUYING_PRICE_LIST}):
			continue
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": rm["item_code"],
				"price_list": BUYING_PRICE_LIST,
				"uom": rm["uom"],
				"price_list_rate": rm["rate"],
			}
		).insert(ignore_permissions=True)


def _create_bom(company):
	existing = frappe.db.get_value(
		"BOM", {"item": FG_ITEM_CODE, "is_active": 1, "is_default": 1, "docstatus": 1}, "name"
	)
	if existing:
		return existing

	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": FG_ITEM_CODE,
			"quantity": 1,
			"company": company,
			"with_operations": 1,
			"rm_cost_as_per": "Price List",
			"buying_price_list": BUYING_PRICE_LIST,
			"items": BOM_ITEMS,
			"operations": BOM_OPERATIONS,
		}
	)
	bom.insert(ignore_permissions=True)
	bom.submit()
	return bom.name


def _create_work_order(company, warehouses, bom_name):
	existing = frappe.db.get_value("Work Order", {"bom_no": bom_name, "docstatus": 1}, "name")
	if existing:
		return existing

	bom = frappe.get_doc("BOM", bom_name)
	operations = [
		{
			"operation": op.operation,
			"workstation": op.workstation,
			"time_in_mins": (op.time_in_mins or 0) * WORK_ORDER_QTY / (bom.quantity or 1),
		}
		for op in bom.operations
	]

	wo = frappe.get_doc(
		{
			"doctype": "Work Order",
			"production_item": FG_ITEM_CODE,
			"bom_no": bom_name,
			"qty": WORK_ORDER_QTY,
			"company": company,
			"fg_warehouse": warehouses["Finished Goods"],
			"wip_warehouse": warehouses["Work In Progress"],
			"planned_start_date": now_datetime(),
			"operations": operations,
		}
	)
	wo.insert(ignore_permissions=True)
	wo.submit()
	return wo.name
