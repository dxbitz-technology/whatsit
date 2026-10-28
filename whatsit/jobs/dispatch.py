from datetime import timedelta
import frappe
from frappe.utils import now_datetime
from whatsit.services.meta_client import MetaClient, MetaError, record_log
from whatsit.services.number_service import resolve_sender
from whatsit.services.conversation_service import ensure_window
from whatsit.services.media_service import upload, get_file
from whatsit.permissions import check_number

MAX_RETRIES = 5


def send(message):
    row = frappe.db.get_value("WhatsApp Message", message, ["name", "status"], as_dict=True, for_update=True)
    if not row or row.status != "Queued":
        return
    doc = frappe.get_doc("WhatsApp Message", message)
    try:
        sender = resolve_sender(doc.sender, check_access=False)
        check_number(doc.sender, doc.requested_by)
        if doc.party_type or doc.reference_doctype:
            from whatsit.services.party_resolver import check_party

            previous = frappe.session.user
            try:
                frappe.set_user(doc.requested_by)
                if doc.party_type:
                    check_party(doc.party_type, doc.party)
                if doc.reference_doctype:
                    check_party(doc.reference_doctype, doc.reference_name)
            finally:
                frappe.set_user(previous)
        if doc.message_type != "Template":
            ensure_window(doc.sender, doc.remote_number)
        else:
            template = frappe.get_doc("WhatsApp Template", doc.template)
            if template.status != "APPROVED" or template.sync_error:
                frappe.throw("Template is no longer approved or supported.")
        if doc.media_file:
            previous = frappe.session.user
            try:
                frappe.set_user(doc.requested_by)
                get_file(doc.media_file)
            finally:
                frappe.set_user(previous)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        doc.db_set(
            {
                "status": "Failed",
                "error_code": "VALIDATION",
                "error_details": "Sender, recipient, attachment or reply window is no longer available.",
            }
        )
        return
    doc.db_set({"status": "Sending", "last_attempt_at": now_datetime(), "next_attempt_at": None})
    frappe.db.commit()
    try:
        payload = frappe.parse_json(doc.request_payload)
        if doc.media_file:
            media_id, mime, filename = upload(sender, doc.media_file, doc.name)
            doc.db_set("meta_media_id", media_id)
            media_type = "document" if mime == "application/pdf" else "image"
            media = {"id": media_id}
            if media_type == "document":
                media["filename"] = filename
            if doc.message_type == "Template":
                payload["template"]["components"].append(
                    {"type": "header", "parameters": [{"type": media_type, media_type: media}]}
                )
            else:
                if doc.text:
                    media["caption"] = doc.text
                payload[media_type] = media
        response = MetaClient(sender, doc.name).request(
            "POST", sender.meta_phone_number_id + "/messages", json=payload
        )
        meta_id = (response.get("messages") or [{}])[0].get("id")
        if not meta_id:
            raise MetaError("MESSAGE_ID_MISSING", ambiguous=True)
        # Lock so a concurrent receipt cannot be overwritten with an earlier state.
        current = frappe.db.get_value("WhatsApp Message", doc.name, "status", for_update=True)
        doc.db_set(
            {
                "status": current if current in ("Delivered", "Read", "Failed") else "Sent",
                "meta_message_id": meta_id,
                "sent_at": now_datetime(),
                "error_code": None,
                "error_details": None,
            }
        )
    except MetaError as exc:
        handle_failure(doc, exc)
    except Exception:
        # A worker interruption after accepting a request cannot safely be retried automatically.
        doc.db_set(
            {
                "status": "Unknown",
                "error_code": "UNEXPECTED",
                "error_details": "Delivery is uncertain. Check Meta before sending again.",
            }
        )
        record_log("dispatch", doc.sender, doc.name, error="Unexpected dispatch failure; delivery uncertain")
    frappe.db.commit()


def handle_failure(doc, exc):
    count = (doc.retry_count or 0) + 1
    retry = exc.transient and count <= MAX_RETRIES
    doc.db_set(
        {
            "status": "Unknown" if exc.ambiguous else ("Queued" if retry else "Failed"),
            "retry_count": count,
            "next_attempt_at": now_datetime() + timedelta(seconds=min(60 * 2**count, 3600))
            if retry
            else None,
            "error_code": exc.code,
            "error_details": "Delivery is uncertain. Check Meta before sending again."
            if exc.ambiguous
            else str(exc),
        }
    )
