from datetime import datetime, timedelta, timezone
import frappe
from frappe.utils import now_datetime, convert_utc_to_system_timezone
from whatsit.services.conversation_service import lock_sender, record
from whatsit.services.number_service import normalize_phone
from whatsit.services.media_service import retrieve
from whatsit.services.meta_client import MetaError

RANK = {"Queued": 0, "Sending": 1, "Unknown": 1, "Failed": 1, "Sent": 2, "Delivered": 3, "Read": 4}


def event_time(timestamp):
    try:
        utc = datetime.fromtimestamp(int(timestamp), timezone.utc).replace(tzinfo=None)
        # Reject timestamps in the future so they cannot extend the service window.
        utc = min(utc, datetime.now(timezone.utc).replace(tzinfo=None))
        return convert_utc_to_system_timezone(utc).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        frappe.throw("Webhook timestamp is invalid.")


def process(event):
    frappe.db.get_value("WhatsApp Webhook Event", event, "name", for_update=True)
    doc = frappe.get_doc("WhatsApp Webhook Event", event)
    if doc.processing_status == "Processed" or not doc.payload:
        return
    doc.db_set({"processing_status": "Processing", "attempt_count": doc.attempt_count + 1})
    frappe.db.savepoint("webhook_changes")
    try:
        payload = frappe.parse_json(doc.payload)
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if change.get("field") in (
                    "message_template_status_update",
                    "message_template_quality_update",
                ):
                    template_update(doc.meta_app, str(entry.get("id")), value)
                    continue
                phone_id = str(value.get("metadata", {}).get("phone_number_id", ""))
                sender_name = frappe.db.get_value(
                    "WhatsApp Number",
                    {
                        "meta_phone_number_id": phone_id,
                        "meta_app": doc.meta_app,
                        "waba_id": str(entry.get("id")),
                        "enabled": 1,
                    },
                    "name",
                )
                if not sender_name:
                    if value.get("messages") or value.get("statuses"):
                        raise MetaError("SENDER_MAPPING_MISSING", transient=True)
                    continue
                lock_sender(sender_name)
                frappe.db.set_value("WhatsApp Number", sender_name, "webhook_status", "Receiving")
                for item in value.get("messages", []):
                    incoming(sender_name, item)
                for item in value.get("statuses", []):
                    status_update(sender_name, item)
        doc.db_set(
            {
                "processing_status": "Processed",
                "processed_at": now_datetime(),
                "last_error": None,
                "next_attempt_at": None,
            }
        )
    except Exception as exc:
        frappe.db.rollback(save_point="webhook_changes")
        error = (
            str(exc)
            if isinstance(exc, MetaError)
            else "Webhook processing failed. Check sender mappings and payload."
        )
        doc.db_set(
            {
                "processing_status": "Failed",
                "last_error": error[:300],
                "next_attempt_at": now_datetime() + timedelta(seconds=min(60 * 2**doc.attempt_count, 3600)),
            }
        )


def incoming(sender, item):
    meta_id = item.get("id")
    if not meta_id:
        raise MetaError("MESSAGE_ID_MISSING")
    existing = frappe.db.get_value("WhatsApp Message", {"meta_message_id": meta_id}, "name")
    if existing:
        doc = frappe.get_doc("WhatsApp Message", existing)
        if doc.sender != sender:
            raise MetaError("MESSAGE_SENDER_MISMATCH")
        if doc.message_type in ("Image", "Document") and not doc.media_file:
            retrieve(doc)
        return
    remote = normalize_phone("+" + item["from"].lstrip("+"))
    kind = item.get("type")
    detail = item.get(kind, {})
    message_type = {"text": "Text", "image": "Image", "document": "Document"}.get(kind, "Text")
    text = detail.get("body") if kind == "text" else detail.get("caption")
    if kind not in ("text", "image", "document"):
        text = "[" + str(kind or "Unsupported message")[:60] + "]"
    timestamp = event_time(item.get("timestamp"))
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "direction": "Incoming",
            "sender": sender,
            "remote_number": remote,
            "message_type": message_type,
            "text": text,
            "meta_message_id": meta_id,
            "status": "Received",
            "received_at": timestamp,
            "meta_media_id": detail.get("id") if kind in ("image", "document") else None,
        }
    )
    doc.flags.internal_update = True
    doc.insert(ignore_permissions=True)
    record(doc, timestamp)
    if doc.meta_media_id:
        retrieve(doc)


def status_update(sender, item):
    name = frappe.db.get_value(
        "WhatsApp Message",
        {"meta_message_id": item.get("id"), "sender": sender, "direction": "Outgoing"},
        "name",
    )
    if not name:
        # A receipt can race with dispatch saving Meta's ID. Retain it for retry.
        raise MetaError("STATUS_MESSAGE_NOT_FOUND", transient=True)
    frappe.db.get_value("WhatsApp Message", name, "name", for_update=True)
    doc = frappe.get_doc("WhatsApp Message", name)
    status = str(item.get("status", "")).title()
    if status not in ("Sent", "Delivered", "Read", "Failed"):
        return
    timestamp = event_time(item.get("timestamp"))
    values = {}
    if status == "Failed":
        if doc.status not in ("Delivered", "Read"):
            values["status"] = "Failed"
            values["error_code"] = str((item.get("errors") or [{}])[0].get("code", "DELIVERY_FAILED"))
            values["error_details"] = "Meta could not deliver this message (" + values["error_code"] + ")."
    elif RANK.get(status, 0) > RANK.get(doc.status, 0):
        values["status"] = status
        values["error_code"] = values["error_details"] = None
    field = {"Sent": "sent_at", "Delivered": "delivered_at", "Read": "read_at"}.get(status)
    if field and not doc.get(field):
        values[field] = timestamp
    if values:
        doc.db_set(values)


def template_update(app, waba, value):
    # Only templates belonging to a WABA connected to this verified app can change.
    if not frappe.db.exists("WhatsApp Number", {"meta_app": app, "waba_id": waba}):
        raise MetaError("TEMPLATE_WABA_MAPPING_MISSING", transient=True)
    filters = {"waba_id": waba}
    if value.get("message_template_id"):
        filters["meta_template_id"] = str(value["message_template_id"])
    else:
        filters.update(
            {
                "template_name": value.get("message_template_name"),
                "language": value.get("message_template_language"),
            }
        )
    names = frappe.get_all("WhatsApp Template", filters=filters, pluck="name")
    if not names:
        raise MetaError("TEMPLATE_MAPPING_MISSING", transient=True)
    for name in names:
        values = {"last_synced_at": now_datetime()}
        if value.get("event"):
            values["status"] = value["event"]
        if value.get("reason"):
            values["rejection_reason"] = value["reason"]
        if value.get("new_quality_score"):
            values["quality_rating"] = value["new_quality_score"]
        frappe.db.set_value("WhatsApp Template", name, values)
