frappe.listview_settings["Action Log"] = {
	add_fields: ["status", "source"],
	get_indicator: function (doc) {
		const colors = {
			Pending: "orange",
			Approved: "blue",
			Executed: "green",
			Rejected: "grey",
			Failed: "red",
		};
		return [__(doc.status), colors[doc.status] || "grey", "status,=," + doc.status];
	},
	onload: function (listview) {
		listview.page.add_action_item(__("Approve"), function () {
			frappe.floorgraph.dispatch_bulk_action(
				listview,
				"floorgraph.actions.engine.approve_action"
			);
		});
		listview.page.add_action_item(__("Reject"), function () {
			frappe.floorgraph.dispatch_bulk_action(
				listview,
				"floorgraph.actions.engine.reject_action"
			);
		});

		// Approval inbox, split by who's asking: doctype-level standard
		// filters (Action Log.source has in_standard_filter=1) only add a
		// quick-filter dropdown, not a default-visible split - the built-in
		// sidebar "Filter By" stats section requires each user to opt in via
		// their own "Edit Filters", so it isn't a queue every approver sees
		// out of the box. These buttons are.
		["Human", "Sensor", "Agent"].forEach((source) => {
			listview.page.add_inner_button(
				__(source),
				async function () {
					// clear(false)'s field.set_value("") calls are async - add()
					// decides whether to skip a filter by checking the standard
					// filter dropdown's *current* value, so calling add() right
					// after clear() without awaiting it sees the stale value,
					// treats that field as "already filtered", and skips setting
					// it, while clear()'s own promise then wipes it out anyway.
					await listview.filter_area.clear(false);
					listview.filter_area.add([
						["Action Log", "status", "=", "Pending"],
						["Action Log", "source", "=", source],
					]);
				},
				__("Pending By Source")
			);
		});
	},
};

frappe.provide("frappe.floorgraph");

frappe.floorgraph.dispatch_bulk_action = function (listview, method) {
	const names = listview.get_checked_items(true);
	if (!names.length) {
		frappe.msgprint(__("Select at least one Pending Action Log first."));
		return;
	}

	// approve_action/reject_action take one action_log at a time; loop
	// client-side over the selection and refresh once all calls settle.
	frappe.dom.freeze();
	Promise.all(names.map((name) => frappe.call({ method: method, args: { action_log: name } })))
		.then(() => listview.refresh())
		.finally(() => frappe.dom.unfreeze());
};
