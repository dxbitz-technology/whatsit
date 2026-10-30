import json
import re
import frappe
from frappe.utils import now_datetime
from whatsit.services.number_service import resolve_sender
from whatsit.services.meta_client import MetaClient, record_log

EDITABLE = (
    "template_name",
    "language",
    "category",
    "header_type",
    "header_text",
    "header_sample_handle",
    "body",
    "footer",
    "variables",
    "buttons",
)


def validate_template(doc):
    from whatsit.services.form_validation import validate_template_fields

    validate_template_fields(doc)
    if not re.fullmatch(r"[a-z0-9_]+", doc.template_name or ""):
        frappe.throw("Use lowercase letters, numbers and underscores for Template Name.")
    if doc.sender and frappe.db.get_value("WhatsApp Number", doc.sender, "waba_id") != doc.waba_id:
        frappe.throw("The sender belongs to another business account.")
    if len(doc.body or "") > 1024 or len(doc.footer or "") > 60 or len(doc.header_text or "") > 60:
        frappe.throw("Body supports 1024 characters. Header and Footer support 60 each.")
    old = doc.get_doc_before_save()
    if old and old.meta_template_id and not doc.flags.meta_sync:
        changed = any(doc.get(k) != old.get(k) for k in EDITABLE if k not in ("variables", "buttons"))
        changed |= any(
            [r.as_dict().get("sample_value") for r in doc.get(k)]
            != [r.as_dict().get("sample_value") for r in old.get(k)]
            or [
                tuple(
                    r.get(x)
                    for x in (
                        "type",
                        "text",
                        "url",
                        "phone_number",
                        "component",
                        "parameter_index",
                        "parameter_name",
                        "button_index",
                    )
                )
                for r in doc.get(k)
            ]
            != [
                tuple(
                    r.get(x)
                    for x in (
                        "type",
                        "text",
                        "url",
                        "phone_number",
                        "component",
                        "parameter_index",
                        "parameter_name",
                        "button_index",
                    )
                )
                for r in old.get(k)
            ]
            for k in ("variables", "buttons")
        )
        if changed and not doc.flags.meta_edit:
            frappe.throw("Use Edit at Meta for a submitted template, or create a new draft.")
    if not doc.flags.meta_sync:
        expected = old.status if old else "DRAFT"
        if doc.status != expected:
            frappe.throw("Template status is managed by Meta.")
    if doc.category == "AUTHENTICATION" and not doc.meta_template_id:
        frappe.throw("Create authentication templates in WhatsApp Manager, then sync them here.")


def creation_components(doc):
    result = []
    for component, text in [("HEADER", doc.header_text), ("BODY", doc.body)]:
        if component == "HEADER" and doc.header_type == "NONE":
            continue
        row = {"type": component}
        if component == "HEADER":
            row["format"] = doc.header_type
        if component == "HEADER" and doc.header_type in ("IMAGE", "DOCUMENT"):
            if not doc.header_sample_handle:
                frappe.throw("Enter a Media Sample Handle from Meta for this header.")
            row["example"] = {"header_handle": [doc.header_sample_handle]}
        else:
            row["text"] = text
            placeholders = re.findall(r"\{\{(\d+)\}\}", text or "")
            if placeholders:
                indices = sorted({int(x) for x in placeholders})
                if indices != list(range(1, max(indices) + 1)):
                    frappe.throw("Template positions must start at 1 without gaps.")
                values = {
                    v.parameter_index: v.sample_value for v in doc.variables if v.component == component
                }
                if any(not values.get(i) for i in indices):
                    frappe.throw("Add a sample for every template position.")
                samples = [values[i] for i in indices]
                row["example"] = {"body_text": [samples]} if component == "BODY" else {"header_text": samples}
        result.append(row)
    if doc.footer:
        result.append({"type": "FOOTER", "text": doc.footer})
    buttons = []
    if len(doc.buttons) > 10:
        frappe.throw("A template supports up to 10 buttons.")
    for button in doc.buttons:
        item = {"type": button.type, "text": button.text}
        if button.type == "URL":
            if not (button.url or "").startswith("https://"):
                frappe.throw("Enter an HTTPS button URL.")
            item["url"] = button.url
            if "{{1}}" in button.url:
                if not button.sample_value:
                    frappe.throw("Add a sample for the URL button.")
                item["example"] = [button.sample_value]
        elif button.type == "PHONE_NUMBER":
            from whatsit.services.number_service import normalize_phone

            item["phone_number"] = normalize_phone(button.phone_number)
        buttons.append(item)
    if buttons:
        result.append({"type": "BUTTONS", "buttons": buttons})
    return result


def sender_for_template(doc, selected=None):
    name = (
        selected
        or doc.sender
        or frappe.db.get_value("WhatsApp Number", {"waba_id": doc.waba_id, "enabled": 1}, "name")
    )
    sender = resolve_sender(name)
    if sender.waba_id != doc.waba_id:
        frappe.throw("The template belongs to another business account.")
    return sender


def submit(doc, selected=None):
    sender = sender_for_template(doc, selected)
    payload = {
        "name": doc.template_name,
        "language": doc.language,
        "category": doc.category,
        "components": creation_components(doc),
    }
    path = sender.waba_id + "/message_templates"
    if doc.meta_template_id:
        path = doc.meta_template_id
        payload.pop("name")
        payload.pop("language")
    result = MetaClient(sender).request("POST", path, json=payload)
    doc.flags.meta_sync = True
    doc.meta_template_id = result.get("id") or doc.meta_template_id
    doc.status = result.get("status") or "PENDING"
    doc.meta_response = frappe.as_json(result)
    doc.last_submitted_at = now_datetime()
    doc.save()
    return {"name": doc.name, "status": doc.status}


def apply_remote(sender, item):
    name = frappe.db.get_value(
        "WhatsApp Template",
        {"waba_id": sender.waba_id, "template_name": item["name"], "language": item["language"]},
        "name",
    )
    doc = frappe.get_doc("WhatsApp Template", name) if name else frappe.new_doc("WhatsApp Template")
    doc.flags.meta_sync = True
    doc.update(
        {
            "waba_id": sender.waba_id,
            "sender": sender.name,
            "template_name": item["name"],
            "language": item["language"],
            "category": item.get("category", "UTILITY"),
            "meta_template_id": item["id"],
            "status": item["status"],
            "rejection_reason": item.get("rejected_reason") or "",
            "quality_rating": str((item.get("quality_score") or {}).get("score") or ""),
            "last_synced_at": now_datetime(),
            "meta_response": frappe.as_json(item),
            "sync_error": None,
        }
    )
    doc.header_type = "NONE"
    doc.header_text = doc.footer = ""
    doc.set("variables", [])
    doc.set("buttons", [])
    for c in item.get("components", []):
        kind = c["type"]
        if kind == "HEADER":
            doc.header_type = c.get("format", "TEXT")
            doc.header_text = c.get("text", "")
        if kind == "BODY":
            doc.body = c.get("text", "")
        if kind == "FOOTER":
            doc.footer = c.get("text", "")
        if kind in ("BODY", "HEADER"):
            for idx in sorted({int(x) for x in re.findall(r"\{\{(\d+)\}\}", c.get("text", ""))}):
                doc.append("variables", {"component": kind, "parameter_index": idx, "sample_value": "Sample"})
            # Named parameters are imported and used for sends, with names preserved.
            for idx, key in enumerate(
                dict.fromkeys(re.findall(r"\{\{([a-z_][a-z0-9_]*)\}\}", c.get("text", ""))), 1
            ):
                doc.append(
                    "variables",
                    {
                        "component": kind,
                        "parameter_index": idx,
                        "parameter_name": key,
                        "sample_value": "Sample",
                    },
                )
        if kind == "BUTTONS":
            for idx, button in enumerate(c.get("buttons", [])):
                if button["type"] not in ("QUICK_REPLY", "URL", "PHONE_NUMBER"):
                    continue
                doc.append(
                    "buttons",
                    {
                        "type": button["type"],
                        "text": button["text"],
                        "url": button.get("url"),
                        "phone_number": button.get("phone_number"),
                    },
                )
                if "{{1}}" in button.get("url", ""):
                    doc.append(
                        "variables",
                        {
                            "component": "BUTTON",
                            "button_index": idx,
                            "parameter_index": 1,
                            "sample_value": "Sample",
                        },
                    )
    doc.save(ignore_permissions=True)
    return doc.name


def sync(sender):
    client = MetaClient(sender)
    params = {
        "fields": "id,name,language,status,category,components,rejected_reason,quality_score",
        "limit": 100,
    }
    count = skipped = 0
    while True:
        result = client.request("GET", sender.waba_id + "/message_templates", params=params)
        for item in result.get("data", []):
            frappe.db.savepoint("template_import")
            try:
                apply_remote(sender, item)
            except frappe.ValidationError:
                frappe.db.rollback(save_point="template_import")
                frappe.clear_messages()
                error = "Template could not be imported. Its format or content is not supported here."
                existing = frappe.db.get_value(
                    "WhatsApp Template",
                    {"waba_id": sender.waba_id, "template_name": item["name"], "language": item["language"]},
                    "name",
                )
                if existing:
                    frappe.db.set_value(
                        "WhatsApp Template",
                        existing,
                        {
                            "status": "UNSUPPORTED",
                            "sync_error": error,
                            "meta_response": frappe.as_json(item),
                            "last_synced_at": now_datetime(),
                        },
                    )
                record_log("template_sync", sender.name, response={"id": item.get("id")}, error=error)
                skipped += 1
                continue
            count += 1
        cursor = result.get("paging", {}).get("cursors", {}).get("after")
        if not result.get("paging", {}).get("next") or not cursor:
            break
        params["after"] = cursor
    return {"count": count, "skipped": skipped}


def send_components(doc, values):
    if isinstance(values, str):
        values = json.loads(values)
    values = values or {}
    if not isinstance(values, dict):
        frappe.throw("Template values must be an object.")
    components = []
    groups = {}
    for row in sorted(doc.variables, key=lambda r: (r.component, r.button_index or 0, r.parameter_index)):
        key = row.parameter_name or str(row.parameter_index)
        qualified = (
            f"{row.component.lower()}.{row.button_index}.{key}"
            if row.component == "BUTTON"
            else f"{row.component.lower()}.{key}"
        )
        value = values.get(qualified, values.get(key))
        if value is None or str(value) == "":
            frappe.throw("Enter a value for " + qualified + ".")
        item = {"type": "text", "text": str(value)}
        if row.parameter_name:
            item["parameter_name"] = row.parameter_name
        group = (row.component, row.button_index or 0)
        groups.setdefault(group, []).append(item)
    for (component, index), parameters in groups.items():
        row = {"type": component.lower(), "parameters": parameters}
        if component == "BUTTON":
            row.update({"sub_type": "url", "index": str(index)})
        components.append(row)
    return components
