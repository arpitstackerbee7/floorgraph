frappe.pages["schedule-gantt"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Scheduling Gantt"),
		single_column: true,
	});

	page.add_inner_button(__("Refresh"), () => gantt.load());
	page.add_inner_button(__("Run Scheduling"), () => gantt.show_run_scheduling_dialog());

	const gantt = new ScheduleGantt(page);
	gantt.load();
};

// Rendered natively (grid + absolutely-positioned blocks), not with a
// timeline library - this app's dependencies are deliberately kept to what
// bench installs by default (see the Phase 0 "no new dependencies"
// decision); vis-timeline isn't bundled with Frappe/ERPNext.
class ScheduleGantt {
	// Fixed width per day rather than stretching the whole window into
	// whatever space happens to be available: a 3-4 day window already
	// fills the page at this width, but picking a much wider range (e.g. a
	// month) makes the timeline genuinely wider than the viewport instead
	// of squeezing 30 columns into the same space until nothing is
	// legible - the timeline scrolls horizontally in that case, with the
	// Workstation label column pinned outside the scroll area.
	static DAY_WIDTH_PX = 120;

	constructor(page) {
		this.page = page;
		this.from_date = frappe.datetime.get_today();
		this.to_date = frappe.datetime.add_days(this.from_date, 3);
		this.$body = $('<div class="schedule-gantt"></div>').appendTo(page.body);
		this.$body.html(`
			<style>
				.schedule-gantt .gantt-container { display: flex; }
				.schedule-gantt .gantt-labels { flex: 0 0 160px; width: 160px; box-sizing: border-box; border-right: 1px solid var(--border-color); }
				.schedule-gantt .gantt-label-spacer { height: 20px; padding-bottom: 6px; margin-bottom: 4px; border-bottom: 1px solid var(--border-color); box-sizing: border-box; }
				.schedule-gantt .gantt-row-label { min-height: 48px; padding: 8px; font-weight: 600; font-size: 12px; box-sizing: border-box; display: flex; align-items: center; border-bottom: 1px solid var(--border-color); }
				.schedule-gantt .gantt-scroll { flex: 1; overflow-x: auto; }
				.schedule-gantt .gantt-timeline { position: relative; }
				.schedule-gantt .gantt-header-track { position: relative; height: 20px; padding-bottom: 6px; margin-bottom: 4px; border-bottom: 1px solid var(--border-color); font-size: 11px; color: var(--text-muted); }
				.schedule-gantt .gantt-header-day { position: absolute; top: 0; height: 100%; display: flex; align-items: center; justify-content: center; font-weight: 700; border-left: 1px solid var(--border-color); box-sizing: border-box; overflow: hidden; white-space: nowrap; }
				.schedule-gantt .gantt-track { position: relative; min-height: 48px; box-sizing: border-box; border-bottom: 1px solid var(--border-color); }
				.schedule-gantt .gantt-block { position: absolute; top: 6px; bottom: 6px; border-radius: 3px; font-size: 11px; color: #fff; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; padding: 2px 4px; cursor: grab; box-sizing: border-box; border: 2px solid var(--fg-color); }
				.schedule-gantt .gantt-block.Production { background: #2490ef; }
				.schedule-gantt .gantt-block.Changeover { background: #f5a623; }
				.schedule-gantt .gantt-block.overdue { background: #e24c4c; }
				.schedule-gantt .gantt-block.Production:hover { filter: brightness(1.15); box-shadow: 0 0 0 1px var(--fg-color), 0 1px 4px rgba(0, 0, 0, 0.35); }
				.schedule-gantt .gantt-block.dragging { opacity: 0.7; cursor: grabbing; }
				.schedule-gantt .gantt-now-line { position: absolute; top: 0; bottom: 0; width: 2px; background: #e24c4c; z-index: 2; pointer-events: none; }
				.schedule-gantt .gantt-empty { padding: 10px 0; }
			</style>
			<div class="gantt-controls" style="margin-bottom: 10px;">
				<label>${__("From")} <input type="date" class="gantt-from" /></label>
				<label style="margin-left: 10px;">${__("To")} <input type="date" class="gantt-to" /></label>
			</div>
			<p class="gantt-loading text-muted">${__("Loading schedule…")}</p>
			<p class="gantt-empty text-muted" style="display: none;">${__(
				"No scheduled slots in this date range."
			)}</p>
			<div class="gantt-container" style="display: none;">
				<div class="gantt-labels">
					<div class="gantt-label-spacer"></div>
				</div>
				<div class="gantt-scroll">
					<div class="gantt-timeline">
						<div class="gantt-header-track"></div>
					</div>
				</div>
			</div>
		`);
		this.$body
			.find(".gantt-from")
			.val(this.from_date)
			.on("change", (e) => {
				this.from_date = e.target.value;
				this.load();
			});
		this.$body
			.find(".gantt-to")
			.val(this.to_date)
			.on("change", (e) => {
				this.to_date = e.target.value;
				this.load();
			});
	}

	load() {
		frappe.call({
			method: "floorgraph.floorgraph.page.schedule_gantt.schedule_gantt.get_gantt_data",
			args: { from_date: this.from_date, to_date: this.to_date },
			callback: (r) => {
				if (!r.exc) this.render(r.message);
			},
		});
	}

	render(data) {
		this.window_start = frappe.datetime.str_to_obj(`${data.from_date} 00:00:00`);
		this.window_end = frappe.datetime.str_to_obj(`${data.to_date} 23:59:59`);
		this.window_minutes = (this.window_end - this.window_start) / 60000;

		// Day boundaries drive the header labels, the gridlines, and the
		// timeline's own fixed pixel width, all from one source, so they
		// stay aligned no matter how many days the window spans.
		const days = this.day_boundaries();
		const timeline_width = days.length * ScheduleGantt.DAY_WIDTH_PX;
		this.$body.find(".gantt-timeline").css("width", `${timeline_width}px`);

		this.render_header_days(days);
		const grid_background = this.day_gridline_background(days);

		const slotsByWorkstation = {};
		(data.slots || []).forEach((s) => {
			(slotsByWorkstation[s.workstation] = slotsByWorkstation[s.workstation] || []).push(s);
		});

		const $labels = this.$body.find(".gantt-labels");
		$labels.find(".gantt-row-label").remove();
		const $timeline = this.$body.find(".gantt-timeline");
		$timeline.find(".gantt-track").remove();

		(data.workstations || []).forEach((ws) => {
			$(`<div class="gantt-row-label">${frappe.utils.escape_html(ws.name)}</div>`).appendTo(
				$labels
			);
			const $track = $(
				`<div class="gantt-track" data-workstation="${frappe.utils.escape_html(
					ws.name
				)}" style="background-image: ${grid_background};"></div>`
			).appendTo($timeline);
			(slotsByWorkstation[ws.name] || []).forEach((slot) => this.render_block($track, slot));
		});

		this.render_now_line();

		this.$body.find(".gantt-empty").toggle(!(data.slots || []).length);

		this.$body.find(".gantt-loading").hide();
		this.$body.find(".gantt-container").show();
	}

	// A vertical line marking the current time, only drawn when "now" falls
	// inside the selected window - without it the grid gives no visual
	// anchor for where "today" actually is versus the rest of the range.
	render_now_line() {
		const $timeline = this.$body.find(".gantt-timeline");
		$timeline.find(".gantt-now-line").remove();
		const now = new Date();
		if (now < this.window_start || now > this.window_end) return;
		const left_pct = ((now - this.window_start) / 60000 / this.window_minutes) * 100;
		$(`<div class="gantt-now-line" style="left:${left_pct}%;"></div>`).appendTo($timeline);
	}

	// One entry per calendar day the window touches, clipped to the actual
	// window bounds (the first/last day may be partial if `from`/`to` don't
	// land on midnight).
	day_boundaries() {
		const days = [];
		let cursor = new Date(
			this.window_start.getFullYear(),
			this.window_start.getMonth(),
			this.window_start.getDate()
		);
		while (cursor < this.window_end) {
			const next = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 1);
			const day_start = cursor < this.window_start ? this.window_start : cursor;
			const day_end = next > this.window_end ? this.window_end : next;
			const left_pct = ((day_start - this.window_start) / 60000 / this.window_minutes) * 100;
			const width_pct = ((day_end - day_start) / 60000 / this.window_minutes) * 100;
			days.push({ date: cursor, left_pct, width_pct });
			cursor = next;
		}
		return days;
	}

	render_header_days(days) {
		const $track = this.$body.find(".gantt-header-track").empty();
		days.forEach((d) => {
			const label = frappe.datetime
				.str_to_user(frappe.datetime.obj_to_str(d.date))
				.split(" ")
				.slice(0, 2)
				.join(" ");
			$(
				`<div class="gantt-header-day" style="left:${d.left_pct}%; width:${
					d.width_pct
				}%;">${frappe.utils.escape_html(label)}</div>`
			).appendTo($track);
		});
	}

	// One hard-edged 1px line per day boundary (skipping the window's own
	// left edge) - a single CSS gradient reused on every row, computed once
	// per render rather than one DOM element per boundary per row.
	day_gridline_background(days) {
		const cuts = days.slice(1).map((d) => d.left_pct);
		if (!cuts.length) return "none";
		const stops = [];
		let prev = 0;
		cuts.forEach((pct) => {
			stops.push(
				`transparent ${prev}%`,
				`transparent calc(${pct}% - 1px)`,
				`var(--border-color) calc(${pct}% - 1px)`,
				`var(--border-color) ${pct}%`
			);
			prev = pct;
		});
		stops.push(`transparent ${prev}%`, `transparent 100%`);
		return `linear-gradient(to right, ${stops.join(", ")})`;
	}

	render_block($track, slot) {
		const start = frappe.datetime.str_to_obj(slot.start_time);
		const end = frappe.datetime.str_to_obj(slot.end_time);
		const left_pct = Math.max(
			0,
			((start - this.window_start) / 60000 / this.window_minutes) * 100
		);
		const width_pct = Math.min(
			100 - left_pct,
			((end - start) / 60000 / this.window_minutes) * 100
		);

		// Full detail (job card + item/work order) only in the hover tooltip -
		// a block is often too narrow to show much text at all, and a hard
		// mid-character cut with no ellipsis looked like broken rendering
		// rather than "there's more, hover to see it." The visible label
		// strips the job card's naming-series prefix down to just its
		// trailing number ("#1" instead of "PO-JOB00001") - the prefix is
		// identical across every job card so it told the viewer nothing,
		// while the number is exactly what actually distinguishes one block
		// from another, and now fits in even a narrow block instead of
		// always truncating to an uninformative "P…".
		const job_card_number = slot.job_card && slot.job_card.match(/(\d+)$/);
		const short_label =
			slot.slot_type === "Changeover"
				? __("Changeover")
				: job_card_number
				? `#${parseInt(job_card_number[1], 10)}`
				: slot.job_card;

		// Flagged in red when the slot's own window has already passed but
		// its Job Card is still open - the one signal a plant manager
		// actually needs at a glance ("is anything behind schedule"), and
		// the only one available without a dedicated status field on
		// Schedule Slot itself.
		const is_overdue =
			slot.slot_type === "Production" &&
			end < new Date() &&
			!["Completed", "Cancelled"].includes(slot.job_card_status);

		const tooltip =
			slot.slot_type === "Changeover"
				? __("Changeover")
				: `${slot.job_card} (${slot.production_item || slot.work_order})${
						is_overdue ? " - " + __("Overdue") : ""
				  }`;

		const $block = $(`
			<div class="gantt-block ${slot.slot_type}${
			is_overdue ? " overdue" : ""
		}" title="${frappe.utils.escape_html(tooltip)}"
				style="left:${left_pct}%; width:${width_pct}%;" data-slot="${slot.name}">
				${frappe.utils.escape_html(short_label)}
			</div>
		`).appendTo($track);

		if (slot.slot_type === "Production") {
			this.make_draggable($block, slot);
		}
	}

	make_draggable($block, slot) {
		const self = this;
		$block.on("mousedown", function (e) {
			e.preventDefault();
			const $track = $block.parent();
			const track_width = $track.width();
			const minutes_per_px = self.window_minutes / track_width;
			const start_x = e.pageX;
			// $block.css("left") returns the computed value in pixels, never
			// the percentage set inline (e.g. "left: 17.36%" on a 650px
			// track reads back as "112.812px"). Feeding that pixel number
			// into calc(${x}% + ...) sent the block to ~113% of track
			// width on the first pixel of movement - why it appeared to
			// vanish the instant a drag started.
			const original_left_px = parseFloat($block.css("left"));
			const original_left_pct = (original_left_px / track_width) * 100;

			$block.addClass("dragging");

			function on_move(ev) {
				const delta_px = ev.pageX - start_x;
				// Clamp to the track's own bounds so a fast/far drag can't
				// carry the block outside its row while the gesture is
				// still in progress.
				const raw_left_px = (original_left_pct / 100) * track_width + delta_px;
				const clamped_left_px = Math.max(0, Math.min(track_width, raw_left_px));
				$block.css("left", `${(clamped_left_px / track_width) * 100}%`);
			}

			function on_up(ev) {
				$(document).off("mousemove", on_move).off("mouseup", on_up);
				$block.removeClass("dragging");

				const delta_px = ev.pageX - start_x;
				let delta_minutes = Math.round((delta_px * minutes_per_px) / 15) * 15;
				if (delta_minutes === 0) {
					$block.css("left", `${original_left_pct}%`);
					// A plain click (no movement) opens the record instead of
					// doing nothing - the block otherwise looked clickable
					// but had no way to reach the underlying Schedule Slot,
					// or from there the Job Card/Work Order it belongs to.
					// New tab, not a route change or dialog, so it can't be
					// confused with - or interrupt - the drag gesture.
					window.open(frappe.utils.get_form_link("Schedule Slot", slot.name), "_blank");
					return;
				}

				const original_start = frappe.datetime.str_to_obj(slot.start_time);
				const new_start = new Date(original_start.getTime() + delta_minutes * 60000);
				$block.css("left", `${original_left_pct}%`); // revert visually - reassignment needs approval first

				self.request_reassignment(slot, new_start);
			}

			$(document).on("mousemove", on_move).on("mouseup", on_up);
		});
	}

	request_reassignment(slot, new_start) {
		frappe.call({
			method: "floorgraph.actions.engine.request_action_as_human",
			args: {
				action_name: "reassign_job_card",
				reference_doctype: "Schedule Slot",
				reference_name: slot.name,
				params: { new_start_time: frappe.datetime.get_datetime_as_string(new_start) },
			},
			callback: (r) => {
				if (!r.exc) {
					frappe.show_alert(
						{
							message: __(
								"Reassignment requested - pending System Manager approval."
							),
							indicator: "blue",
						},
						7
					);
				}
			},
		});
	}

	show_run_scheduling_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Run Scheduling"),
			fields: [
				{
					fieldname: "direction",
					fieldtype: "Select",
					label: __("Direction"),
					options: "Forward\nBackward",
					default: "Forward",
					reqd: 1,
				},
				{
					fieldname: "anchor_datetime",
					fieldtype: "Datetime",
					label: __("Anchor Datetime"),
					default: frappe.datetime.now_datetime(),
					reqd: 1,
					description: __(
						"Forward: start scheduling from this time. Backward: the deadline to schedule backward from."
					),
				},
				{
					fieldname: "work_orders",
					fieldtype: "Small Text",
					label: __("Work Orders (comma-separated, blank = all open)"),
				},
			],
			primary_action_label: __("Run"),
			primary_action: (values) => {
				const work_orders = values.work_orders
					? values.work_orders
							.split(",")
							.map((s) => s.trim())
							.filter(Boolean)
					: null;
				frappe.call({
					method: "floorgraph.scheduling.engine.api_run_scheduling",
					args: {
						direction: values.direction,
						anchor_datetime: values.anchor_datetime,
						work_orders,
					},
					freeze: true,
					callback: (r) => {
						if (!r.exc) {
							frappe.show_alert(
								{
									message: __("Scheduling Run {0} created.", [r.message]),
									indicator: "green",
								},
								7
							);
							dialog.hide();
							this.load();
						}
					},
				});
			},
		});
		dialog.show();
	}
}
