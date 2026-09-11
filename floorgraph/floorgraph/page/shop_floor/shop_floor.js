frappe.pages["shop-floor"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Shop Floor"),
		single_column: true,
	});

	page.add_inner_button(__("Refresh"), () => frappe.floorgraph.shop_floor.load(page));

	frappe.floorgraph = frappe.floorgraph || {};
	frappe.floorgraph.shop_floor = new ShopFloorDashboard(page);
	frappe.floorgraph.shop_floor.load();

	// Server scopes this event to doctype="Andon Alert" (only users who can
	// read that doctype receive it) - doctype_subscribe is what puts this
	// client in that room; without it the event would silently never arrive.
	frappe.realtime.doctype_subscribe("Andon Alert");
	frappe.realtime.on("floorgraph:andon_alert", (data) => {
		frappe.show_alert(
			{
				message: __("Andon: {0} on {1}", [data.message, data.workstation]),
				indicator: data.severity === "Critical" ? "red" : "orange",
			},
			10
		);
		frappe.floorgraph.shop_floor.load();
	});
};

class ShopFloorDashboard {
	constructor(page) {
		this.page = page;
		this.$body = $(`
			<div class="shop-floor-dashboard">
				<style>
					.shop-floor-dashboard table td { vertical-align: middle; padding: 10px 12px; }
					.shop-floor-dashboard table th { white-space: nowrap; padding: 10px 12px; vertical-align: bottom; }
					.shop-floor-dashboard .btn-log-downtime,
					.shop-floor-dashboard .btn-acknowledge,
					.shop-floor-dashboard .btn-resolve,
					.shop-floor-dashboard .indicator-pill { white-space: nowrap; }
					.shop-floor-dashboard .row-actions { display: flex; gap: 6px; flex-wrap: nowrap; align-items: center; }
				</style>
			</div>
		`).appendTo(page.body);
		this.$workstations = $("<div></div>").appendTo(this.$body);
		this.$alerts = $('<div style="margin-top: 30px;"></div>').appendTo(this.$body);

		// Shown until the first successful load() replaces them - without
		// this the page is blank until the round-trip completes, which reads
		// as broken rather than loading. Not re-shown on later refreshes
		// (Refresh button, downtime submit, realtime Andon events) since
		// those already resolve fast enough that flashing this text again
		// would just be flicker.
		this.$workstations.html(`<p class="text-muted">${__("Loading workstations…")}</p>`);
		this.$alerts.html(`<p class="text-muted">${__("Loading Andon alerts…")}</p>`);
	}

	load() {
		frappe.call({
			method: "floorgraph.floorgraph.page.shop_floor.shop_floor.get_dashboard_data",
			callback: (r) => {
				if (!r.exc) {
					this.render(r.message);
				}
			},
		});
	}

	render(data) {
		this.render_workstations(data.workstations || []);
		this.render_alerts(data.open_alerts || []);
	}

	render_workstations(workstations) {
		const rows = workstations
			.map((w) => {
				const oee = w.oee;
				const pct = (v) =>
					v === null || v === undefined
						? "-"
						: `<span class="indicator-pill ${oee_indicator(
								v
						  )}" style="white-space: nowrap;">${flt(v).toFixed(1)}%</span>`;
				const alert_badge = w.open_alerts
					? `<span class="indicator-pill red">${w.open_alerts} ${__("open")}</span>`
					: `<span class="indicator-pill green">${__("clear")}</span>`;

				return `
					<tr data-workstation="${frappe.utils.escape_html(w.name)}">
						<td>${frappe.utils.escape_html(w.name)}</td>
						<td>${frappe.utils.escape_html(w.status || "")}</td>
						<td>${oee ? frappe.datetime.str_to_user(oee.log_date) : "-"}</td>
						<td>${oee ? pct(oee.availability) : "-"}</td>
						<td>${oee ? pct(oee.performance) : "-"}</td>
						<td>${oee ? pct(oee.quality) : "-"}</td>
						<td><b>${oee ? pct(oee.oee) : "-"}</b></td>
						<td>${alert_badge}</td>
						<td><button class="btn btn-xs btn-default btn-log-downtime">${__("Log Downtime")}</button></td>
					</tr>
				`;
			})
			.join("");

		this.$workstations.html(`
			<h4>${__("Workstations")}</h4>
			<div style="overflow-x: auto;">
				<table class="table table-bordered">
					<thead>
						<tr>
							<th>${__("Workstation")}</th>
							<th>${__("Status")}</th>
							<th>${__("OEE Log Date")}</th>
							<th>${__("Availability")}</th>
							<th>${__("Performance")}</th>
							<th>${__("Quality")}</th>
							<th>${__("OEE")}</th>
							<th>${__("Andon")}</th>
							<th></th>
						</tr>
					</thead>
					<tbody>${rows || `<tr><td colspan="9">${__("No workstations")}</td></tr>`}</tbody>
				</table>
			</div>
		`);

		this.$workstations.find(".btn-log-downtime").on("click", (e) => {
			const workstation = $(e.currentTarget).closest("tr").data("workstation");
			this.show_log_downtime_dialog(workstation);
		});
	}

	show_log_downtime_dialog(workstation) {
		const dialog = new frappe.ui.Dialog({
			title: __("Log Downtime - {0}", [workstation]),
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Link",
					options: "Downtime Reason",
					label: __("Reason"),
					reqd: 1,
					get_query: () => ({ filters: { is_active: 1 } }),
				},
				{
					fieldname: "minutes",
					fieldtype: "Float",
					label: __("Minutes"),
					reqd: 1,
				},
			],
			primary_action_label: __("Submit"),
			primary_action: (values) => {
				// requires_approval=1 on the record_downtime Action (see
				// install_actions.py) - this only requests the action, an
				// approver still has to act on it in the inbox.
				frappe.call({
					method: "floorgraph.actions.engine.request_action_as_human",
					args: {
						action_name: "record_downtime",
						params: { workstation, reason: values.reason, minutes: values.minutes },
					},
					callback: () => {
						dialog.hide();
						frappe.show_alert({
							message: __("Downtime request submitted for approval"),
							indicator: "green",
						});
						this.load();
					},
				});
			},
		});
		dialog.show();
	}

	render_alerts(alerts) {
		if (!alerts.length) {
			this.$alerts.html(
				`<h4>${__("Open Andon Alerts")}</h4><p class="text-muted">${__(
					"No open alerts"
				)}</p>`
			);
			return;
		}

		const rows = alerts
			.map(
				(a) => `
					<tr data-alert="${a.name}">
						<td>${frappe.utils.escape_html(a.workstation)}</td>
						<td>${frappe.utils.escape_html(a.alert_type)}</td>
						<td><span class="indicator-pill ${a.severity === "Critical" ? "red" : "orange"}">${
					a.severity
				}</span></td>
						<td>${frappe.utils.escape_html(a.status)}</td>
						<td>${frappe.utils.escape_html(a.message || "")}</td>
						<td>
							<div class="row-actions">
								${
									a.status === "Open"
										? `<button class="btn btn-xs btn-default btn-acknowledge">${__(
												"Acknowledge"
										  )}</button>`
										: ""
								}
								<button class="btn btn-xs btn-primary btn-resolve">${__("Resolve")}</button>
							</div>
						</td>
					</tr>
				`
			)
			.join("");

		this.$alerts.html(`
			<h4>${__("Open Andon Alerts")}</h4>
			<div style="overflow-x: auto;">
				<table class="table table-bordered">
					<thead>
						<tr>
							<th>${__("Workstation")}</th>
							<th>${__("Type")}</th>
							<th>${__("Severity")}</th>
							<th>${__("Status")}</th>
							<th>${__("Message")}</th>
							<th></th>
						</tr>
					</thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
		`);

		this.$alerts.find(".btn-acknowledge").on("click", (e) => {
			const alert = $(e.currentTarget).closest("tr").data("alert");
			frappe.call({
				method: "floorgraph.andon.engine.acknowledge_alert",
				args: { alert },
				callback: () => this.load(),
			});
		});

		this.$alerts.find(".btn-resolve").on("click", (e) => {
			const alert = $(e.currentTarget).closest("tr").data("alert");
			frappe.call({
				method: "floorgraph.andon.engine.resolve_alert",
				args: { alert },
				callback: () => this.load(),
			});
		});
	}
}

function flt(v) {
	return parseFloat(v) || 0;
}

// Thresholds match common OEE convention (world-class ~85%+, typical ~60%,
// below that is a real problem) - reuses the same indicator-pill widget as
// the Andon column on this same table instead of introducing a new color
// scheme, so it stays theme-correct for free.
function oee_indicator(v) {
	if (v >= 85) return "green";
	if (v >= 60) return "orange";
	return "red";
}
