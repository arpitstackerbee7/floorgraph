// Copyright (c) 2026, Sidharth P V and contributors
// For license information, please see license.txt

frappe.ui.form.on("Action Log", {
	refresh(frm) {
		if (frm.doc.status !== "Pending") {
			return;
		}

		// Was previously add_custom_button(label, fn, null, "primary") - that
		// 4th argument doesn't exist on add_custom_button(label, fn, group)
		// (see frappe/public/js/frappe/form/form.js), so it was silently
		// discarded and Approve rendered as a plain button buried in the
		// "..." menu instead of the prominent action it's meant to be.
		// set_primary_action puts it where Save normally sits - the one
		// clear next step while a request is Pending, matching how Submit
		// behaves on every other doctype in this app.
		frm.page.set_primary_action(__("Approve"), () => {
			frappe.call({
				method: "floorgraph.actions.engine.approve_action",
				args: { action_log: frm.doc.name },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		});

		frm.add_custom_button(__("Reject"), () => {
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason") },
				(values) => {
					frappe.call({
						method: "floorgraph.actions.engine.reject_action",
						args: { action_log: frm.doc.name, reason: values.reason },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
				},
				__("Reject Action")
			);
		});
	},
});
