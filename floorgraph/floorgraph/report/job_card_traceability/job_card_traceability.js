// Copyright (c) 2026, Sidharth P V and contributors
// For license information, please see license.txt

frappe.query_reports["Job Card Traceability"] = {
	filters: [
		{
			label: __("Job Card"),
			fieldname: "job_card",
			fieldtype: "Link",
			options: "Job Card",
			reqd: 1,
		},
	],
};
