import frappe
from whatsit.permissions import require_manager, require_user
from whatsit.services.number_service import resolve_sender
from whatsit.services import template_service


@frappe.whitelist(methods=["POST"])
def submit(template, from_number=None):
    require_manager()
    return template_service.submit(frappe.get_doc("WhatsApp Template", template), from_number)


@frappe.whitelist(methods=["POST"])
def edit(template, changes, from_number=None):
    require_manager()
    if isinstance(changes, str):
        changes = frappe.parse_json(changes)
    doc = frappe.get_doc("WhatsApp Template", template)
    if not doc.meta_template_id:
        frappe.throw("Save and submit this draft first.")
    if set(changes) - {
        "category",
        "header_type",
        "header_text",
        "header_sample_handle",
        "body",
        "footer",
        "variables",
        "buttons",
    }:
        frappe.throw("Only template content can be edited.")
    doc.update(changes)
    doc.flags.meta_edit = True
    template_service.validate_template(doc)
    return template_service.submit(doc, from_number)


@frappe.whitelist(methods=["POST"])
def sync(from_number):
    require_manager()
    return template_service.sync(resolve_sender(from_number))


@frappe.whitelist()
def approved(from_number):
    require_user()
    sender = resolve_sender(from_number)
    return frappe.get_list(
        "WhatsApp Template",
        filters={"waba_id": sender.waba_id, "status": "APPROVED", "sync_error": ["is", "not set"]},
        fields=["name", "template_name", "language", "body", "header_type"],
        limit_page_length=200,
    )


@frappe.whitelist()
def details(template, from_number):
    require_user()
    sender = resolve_sender(from_number)
    doc = frappe.get_doc("WhatsApp Template", template)
    if doc.waba_id != sender.waba_id or doc.status != "APPROVED" or doc.sync_error:
        frappe.throw("Choose an approved template for this sender.")
    return {
        "body": doc.body,
        "header_type": doc.header_type,
        "variables": [
            {
                "key": (f"button.{r.button_index}." if r.component == "BUTTON" else r.component.lower() + ".")
                + (r.parameter_name or str(r.parameter_index)),
                "sample": r.sample_value,
            }
            for r in doc.variables
        ],
    }
