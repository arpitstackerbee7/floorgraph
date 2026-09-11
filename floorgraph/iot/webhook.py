"""Generic authenticated webhook: any registered Machine Event Source can
request any Action it's been explicitly granted (Machine Event Source's own
allowed_actions table - deny-by-default, mirrors Action.allowed_roles),
identified by an HMAC-SHA256 signature over the raw request body rather than
a Frappe session (devices have no session).

POST /api/method/floorgraph.iot.webhook.ingest_event
Header: X-Floorgraph-Signature: hex(HMAC-SHA256(secret, raw_body))
Body (JSON): {source_name, device_event_id, event_type, action, params?,
              reference_doctype?, reference_name?}
"""

import hashlib
import hmac

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from floorgraph.actions.engine import request_action_as_sensor

REQUIRED_FIELDS = ("source_name", "device_event_id", "event_type", "action")

# Not a real secret - used only so an unknown/disabled source's HMAC
# computation costs the same as a real one's (see _verify_source). Any fixed
# value works; it's never compared against anything a caller controls.
_DUMMY_SECRET = "0" * 32


# Reviewed: devices have no Frappe session, so allow_guest=True is required;
# the real authentication boundary is the HMAC-SHA256 signature verified in
# _verify_source() before any DB write, plus each source's own
# allowed_actions allowlist (see _check_sensor_allowed_action). See this
# module's docstring for the full design.
#
# rate_limit is IP-based (devices have no session/user to key on) and applied
# before parse_json/HMAC - a guest-writable endpoint needs its own cap
# independent of whatever the reverse proxy happens to enforce. 600/min is
# generous for real per-device sensor traffic (10/sec) while still bounding a
# flood; tune down if real deployments show it's too high.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: security.guest-whitelisted-method
@rate_limit(limit=600, seconds=60, methods=["POST"])
def ingest_event() -> dict:
	return _ingest_event(frappe.request.get_data(), frappe.get_request_header("X-Floorgraph-Signature"))


def _ingest_event(raw_body, signature):
	"""Pulled out of the whitelisted `ingest_event()` so tests can drive the
	real HMAC-over-raw-bytes logic without needing a live HTTP request."""
	try:
		# frappe.parse_json() only decodes when given a str - raw_body is
		# bytes (frappe.request.get_data() in production, a raw encoded
		# fixture in tests), so decode first or it silently no-ops and
		# `payload` comes back as the undecoded bytes object.
		payload = frappe.parse_json(raw_body.decode("utf-8"))
	except Exception:
		_reject_malformed()

	# A presence/None check, not a truthiness check: device_event_id is a
	# device-assigned id that may legitimately be 0 (or any other falsy-but-
	# valid value) - `payload.get(f)` alone would wrongly reject that as
	# malformed. Empty string is still rejected: none of these fields is ever
	# meaningfully blank.
	if not isinstance(payload, dict) or any(payload.get(f) in (None, "") for f in REQUIRED_FIELDS):
		_reject_malformed()

	source = _verify_source(payload.get("source_name"), signature, raw_body)

	existing = frappe.db.exists(
		"Machine Event Log",
		{"machine_event_source": source.name, "device_event_id": payload["device_event_id"]},
	)
	if existing:
		# Idempotent no-op: devices retry. Same result as the first delivery,
		# without re-requesting (and possibly re-executing) the action.
		return {"status": "duplicate", "machine_event_log": existing}

	log = frappe.get_doc(
		{
			"doctype": "Machine Event Log",
			"machine_event_source": source.name,
			"device_event_id": payload["device_event_id"],
			"event_type": payload["event_type"],
			"action": payload["action"],
			"params": frappe.as_json(payload.get("params") or {}),
			"reference_doctype": payload.get("reference_doctype"),
			"reference_name": payload.get("reference_name"),
			"received_on": frappe.utils.now_datetime(),
		}
	)
	log.insert(ignore_permissions=True)

	try:
		_check_sensor_allowed_action(source, payload["action"])
		action_log_name = request_action_as_sensor(
			payload["action"],
			params=payload.get("params") or {},
			reference_doctype=payload.get("reference_doctype"),
			reference_name=payload.get("reference_name"),
		)
		log.db_set("resulting_action_log", action_log_name)
	except Exception:
		# Same philosophy as Action Log's own _execute(): an authenticated
		# but invalid request (not in this source's allowlist, unknown
		# action, bad reference) is an audited failure on the event log, not
		# a 500 that might make the device retry forever.
		log.db_set("error", frappe.get_traceback())

	return {"status": "accepted", "machine_event_log": log.name}


def _verify_source(source_name, signature, raw_body):
	# One generic rejection for "no such source", "source disabled", and
	# "bad signature" - deliberately not distinguishing them in the
	# response, so a caller can't use the error to enumerate valid
	# source_names or brute-force a secret. The response alone isn't enough,
	# though: a real source's get_password("secret") (a decrypt) + hmac.new
	# cost real time an unknown/disabled source used to skip entirely, so
	# those cases returned measurably faster - timing itself was the leak,
	# regardless of the response body. Always paying that cost (against a
	# dummy secret when there's no real source) removes the signal; only
	# branch on the actual outcome once everything's been computed.
	def reject():
		frappe.throw(_("Invalid source or signature"), frappe.AuthenticationError)

	exists = bool(source_name) and frappe.db.exists("Machine Event Source", source_name)
	source = frappe.get_doc("Machine Event Source", source_name) if exists else None
	secret = source.get_password("secret") if source else _DUMMY_SECRET
	expected_signature = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
	signature_matches = bool(signature) and hmac.compare_digest(expected_signature, signature)

	if not exists or not source.enabled or not signature_matches:
		reject()

	return source


def _check_sensor_allowed_action(source, action_name):
	allowed = {row.action for row in source.allowed_actions}
	if action_name not in allowed:
		frappe.throw(
			_("{0} is not permitted to request {1}").format(source.name, action_name),
			frappe.PermissionError,
		)


def _reject_malformed():
	frappe.throw(_("Invalid event payload"), frappe.ValidationError)
