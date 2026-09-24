app_name = "floorgraph"
app_title = "Floorgraph"
app_publisher = "Sidharth P V"
app_description = "Governed action-graph MES layer for ERPNext"
app_email = "sidharth.thamban@gmail.com"
app_license = "mit"
app_logo_url = "/assets/floorgraph/images/floorgraph-logo.svg"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "floorgraph",
# 		"logo": "/assets/floorgraph/logo.png",
# 		"title": "Floorgraph",
# 		"route": "/floorgraph",
# 		"has_permission": "floorgraph.api.permission.has_app_permission"
# 	}
# ]
add_to_apps_screen = [
     {
             "name": "floorgraph",
             "logo": "/assets/floorgraph/logo.png",
             "title": "Floorgraph",
             "route": "/desk/shop-floor",
     }
]
# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/floorgraph/css/floorgraph.css"
# app_include_js = "/assets/floorgraph/js/floorgraph.js"

# include js, css files in header of web template
# web_include_css = "/assets/floorgraph/css/floorgraph.css"
# web_include_js = "/assets/floorgraph/js/floorgraph.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "floorgraph/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "floorgraph/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "floorgraph.utils.jinja_methods",
# 	"filters": "floorgraph.utils.jinja_filters"
# }

# Installation
# ------------

# Same seed data needs to run on both install and migrate: a fresh CI site
# may only run `bench install-app`, not `bench migrate`, so relying on
# after_install alone would leave seed data missing in that path.
_SEED_HOOKS = [
	"floorgraph.setup.install_actions.ensure_seed_actions",
	"floorgraph.setup.install_downtime.ensure_seed_downtime_reasons",
	"floorgraph.setup.install_custom_fields.ensure_workstation_asset_field",
]

# before_install = "floorgraph.install.before_install"
after_install = _SEED_HOOKS

# Migration Hooks
# ----------------
after_migrate = _SEED_HOOKS

# Uninstallation
# ------------

# before_uninstall = "floorgraph.uninstall.before_uninstall"
# after_uninstall = "floorgraph.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "floorgraph.utils.before_app_install"
# after_app_install = "floorgraph.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "floorgraph.utils.before_app_uninstall"
# after_app_uninstall = "floorgraph.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "floorgraph.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"floorgraph.oee.engine.run_daily_oee_job",
	],
	"hourly": [
		"floorgraph.agent.engine.evaluate_agent_rules",
	],
}

# scheduler_events = {
# 	"all": [
# 		"floorgraph.tasks.all"
# 	],
# 	"daily": [
# 		"floorgraph.tasks.daily"
# 	],
# 	"hourly": [
# 		"floorgraph.tasks.hourly"
# 	],
# 	"weekly": [
# 		"floorgraph.tasks.weekly"
# 	],
# 	"monthly": [
# 		"floorgraph.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "floorgraph.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "floorgraph.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "floorgraph.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["floorgraph.utils.before_request"]
# after_request = ["floorgraph.utils.after_request"]

# Job Events
# ----------
# before_job = ["floorgraph.utils.before_job"]
# after_job = ["floorgraph.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"floorgraph.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
