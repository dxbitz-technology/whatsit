import hashlib
import json
import re
import frappe
from frappe.utils import now_datetime
from whatsit.permissions import require_user
from whatsit.services.number_service import resolve_sender
from whatsit.services.party_resolver import resolve_recipient, check_party
from whatsit.services.conversation_service import ensure_window, find, lock_sender, record
from whatsit.services.media_service import get_file
from whatsit.services.template_service import send_components


def rate_limit():
    key = frappe.cache.make_key(
        "whatsapp-send:" + frappe.session.user + ":" + now_datetime().strftime("%Y%m%d%H%M")
    )
    count = frappe.cache.incr(key)
    if count == 1:
        frappe.cache.expire(key, 120)
    if count > 60:
        frappe.throw("Send limit reached. Try again in a minute.", frappe.RateLimitExceededError)


def result(doc):
    return {"message_id": doc.name, "status": doc.status, "meta_message_id": doc.meta_message_id}


def queue(
    kind,
    from_number=None,
    to=None,
    text=None,
    template=None,
    language=None,
    variables=None,
    media_file=None,
    party_type=None,
    party=None,
    reference_doctype=None,
    reference_name=None,
    idempotency_key=None,
):
    require_user()
    rate_limit()
    sender = resolve_sender(from_number)
    remote = resolve_recipient(to, party_type, party)
    if reference_doctype or reference_name:
        check_party(reference_doctype, reference_name)
    if not isinstance(text or "", str):
        frappe.throw("Message must be text.")
    if idempotency_key and len(idempotency_key) > 140:
        frappe.throw("Request Key must be within 140 characters.")
    params = frappe.parse_json(variables) if isinstance(variables, str) else (variables or {})
    canonical = {
        "sender": sender.name,
        "remote": remote,
        "kind": kind,
        "text": text,
        "template": template,
        "language": language,
        "variables": params,
        "media_file": media_file,
        "party_type": party_type,
        "party": party,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
    }
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    lock_sender(sender.name)
    if idempotency_key:
        existing = frappe.db.get_value("WhatsApp Message", {"idempotency_key": idempotency_key}, "name")
        if existing:
            doc = frappe.get_doc("WhatsApp Message", existing)
            if doc.requested_by != frappe.session.user or doc.request_hash != fingerprint:
                frappe.throw("Request Key has already been used for a different request.")
            return result(doc)
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": remote[1:]}
    template_doc = None
    if kind == "Text":
        if not text or len(text) > 4096:
            frappe.throw("Enter a message within 4096 characters.")
        ensure_window(sender.name, remote)
        payload.update({"type": "text", "text": {"body": text}})
    elif kind == "Template":
        template_name = (
            template
            if frappe.db.exists("WhatsApp Template", template)
            else frappe.db.get_value(
                "WhatsApp Template",
                {"waba_id": sender.waba_id, "template_name": template, "language": language or "en"},
                "name",
            )
        )
        if not template_name:
            frappe.throw("Template not found for this sender and language.")
        template_doc = frappe.get_doc("WhatsApp Template", template_name)
        if (
            template_doc.status != "APPROVED"
            or template_doc.waba_id != sender.waba_id
            or (language and template_doc.language != language)
        ):
            frappe.throw("Choose an approved template for this sender and language.")
        # Unsupported imported template components must never be silently omitted.
        remote_schema = frappe.parse_json(template_doc.meta_response or "{}")
        for component in remote_schema.get("components", []):
            if component.get("type") == "BUTTONS" and any(
                b.get("type") not in ("QUICK_REPLY", "URL", "PHONE_NUMBER")
                for b in component.get("buttons", [])
            ):
                frappe.throw("This template contains buttons that are not supported here.")
        payload.update(
            {
                "type": "template",
                "template": {
                    "name": template_doc.template_name,
                    "language": {"code": template_doc.language},
                    "components": send_components(template_doc, params),
                },
            }
        )
        if template_doc.header_type in ("IMAGE", "DOCUMENT") and not media_file:
            frappe.throw("Choose an attachment for the template header.")
    else:
        ensure_window(sender.name, remote)
        if not media_file:
            frappe.throw("Choose an attachment.")
    if media_file:
        file, content, mime = get_file(media_file)
        if kind == "Template":
            expected = "application/pdf" if template_doc.header_type == "DOCUMENT" else "image/"
            if template_doc.header_type not in ("IMAGE", "DOCUMENT") or not mime.startswith(expected):
                frappe.throw("The attachment does not match the template header.")
        else:
            if len(text or "") > 1024:
                frappe.throw("Caption must be within 1024 characters.")
            kind = "Document" if mime == "application/pdf" else "Image"
            payload["type"] = kind.lower()
    if template_doc:
        body_values = {
            r.parameter_name or str(r.parameter_index): params.get(
                "body." + (r.parameter_name or str(r.parameter_index)),
                params.get(r.parameter_name or str(r.parameter_index)),
            )
            for r in template_doc.variables
            if r.component == "BODY"
        }
        text = re.sub(
            r"\{\{([^}]+)\}\}",
            lambda match: str(body_values.get(match.group(1), match.group(0))),
            template_doc.body,
        )
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "direction": "Outgoing",
            "sender": sender.name,
            "remote_number": remote,
            "party_type": party_type,
            "party": party,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "message_type": kind,
            "text": text,
            "template": template_doc.name if template_doc else None,
            "template_parameters_json": frappe.as_json(params),
            "media_file": media_file,
            "conversation": find(sender.name, remote),
            "status": "Queued",
            "idempotency_key": idempotency_key or None,
            "request_hash": fingerprint,
            "requested_by": frappe.session.user,
            "request_payload": frappe.as_json(payload),
        }
    )
    doc.flags.internal_update = True
    doc.insert(ignore_permissions=True)
    if media_file:
        # Link a private attachment record to this message so all permitted operators can read it.
        attachment = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": file.file_name,
                "content": content,
                "is_private": 1,
                "attached_to_doctype": "WhatsApp Message",
                "attached_to_name": doc.name,
            }
        ).insert(ignore_permissions=True)
        doc.db_set("media_file", attachment.name)
    record(doc)
    frappe.enqueue("whatsit.jobs.dispatch.send", message=doc.name, enqueue_after_commit=True)
    return result(doc)
