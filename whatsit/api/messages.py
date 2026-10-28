import frappe
from whatsit.services.message_service import queue
from whatsit.permissions import require_user, check_number


@frappe.whitelist(methods=["POST"])
def send_text(
    from_number=None,
    to=None,
    text=None,
    party_type=None,
    party=None,
    reference_doctype=None,
    reference_name=None,
    idempotency_key=None,
):
    return queue(
        "Text",
        from_number,
        to,
        text=text,
        party_type=party_type,
        party=party,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
def send_template(
    from_number=None,
    to=None,
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
    return queue(
        "Template",
        from_number,
        to,
        template=template,
        language=language,
        variables=variables,
        media_file=media_file,
        party_type=party_type,
        party=party,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
def send_media(
    from_number=None,
    to=None,
    file_name=None,
    caption=None,
    party_type=None,
    party=None,
    reference_doctype=None,
    reference_name=None,
    idempotency_key=None,
):
    return queue(
        "Media",
        from_number,
        to,
        text=caption,
        media_file=file_name,
        party_type=party_type,
        party=party,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist()
def get_status(message_id):
    require_user()
    doc = frappe.get_doc("WhatsApp Message", message_id)
    check_number(doc.sender)
    from whatsit.services.message_service import result

    return dict(result(doc), error=doc.error_details)
