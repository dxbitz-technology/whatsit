import hashlib
import hmac
import json
import frappe
from frappe.utils import now_datetime
from werkzeug.wrappers import Response


def verify_signature(secret, raw, signature):
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def receive(key=None, **kwargs):
    if not key or len(key) > 100:
        frappe.throw("Webhook not found.", frappe.DoesNotExistError)
    name = frappe.db.get_value("WhatsApp Meta App", {"route_key": key, "enabled": 1}, "name")
    if not name:
        frappe.throw("Webhook not found.", frappe.DoesNotExistError)
    app = frappe.get_doc("WhatsApp Meta App", name)
    if frappe.request.method == "GET":
        mode = frappe.request.args.get("hub.mode", "")
        token = frappe.request.args.get("hub.verify_token", "")
        if mode != "subscribe" or not hmac.compare_digest(token, app.get_password("verify_token")):
            frappe.throw("Webhook verification failed.", frappe.PermissionError)
        app.db_set("webhook_verified_at", now_datetime())
        frappe.db.commit()
        return Response(frappe.request.args.get("hub.challenge", ""), mimetype="text/plain")
    raw = frappe.request.get_data(cache=True)
    if len(raw) > 2 * 1024 * 1024:
        frappe.throw("Webhook payload is too large.")
    if not verify_signature(
        app.get_password("app_secret"), raw, frappe.request.headers.get("X-Hub-Signature-256", "")
    ):
        frappe.throw("Webhook signature is invalid.", frappe.PermissionError)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        frappe.throw("Invalid webhook payload.")
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        frappe.throw("Invalid webhook object.")
    fingerprint = hashlib.sha256((app.name + ":").encode() + raw).hexdigest()
    # Serialize event creation per app. The unique fingerprint is a second guard.
    frappe.db.get_value("WhatsApp Meta App", app.name, "name", for_update=True)
    existing = frappe.db.get_value("WhatsApp Webhook Event", {"event_key": fingerprint}, "name")
    if not existing:
        event = frappe.get_doc(
            {
                "doctype": "WhatsApp Webhook Event",
                "received_at": now_datetime(),
                "meta_app": app.name,
                "event_key": fingerprint,
                "event_type": "whatsapp_business_account",
                "payload": frappe.as_json(payload),
                "processing_status": "Queued",
                "next_attempt_at": now_datetime(),
            }
        ).insert(ignore_permissions=True)
        frappe.enqueue(
            "whatsit.services.webhook_processor.process", event=event.name, enqueue_after_commit=True
        )
    app.db_set("last_webhook_at", now_datetime())
    return {"received": True}


@frappe.whitelist(methods=["POST"])
def replay(event):
    from whatsit.permissions import require_manager

    require_manager()
    doc = frappe.get_doc("WhatsApp Webhook Event", event)
    if not doc.payload:
        frappe.throw("This payload has expired.")
    doc.db_set({"processing_status": "Queued", "attempt_count": 0, "next_attempt_at": now_datetime()})
    frappe.enqueue("whatsit.services.webhook_processor.process", event=doc.name, enqueue_after_commit=True)
    return {"queued": True}
