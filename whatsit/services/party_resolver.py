import frappe
from whatsit.services.number_service import normalize_phone


def check_party(party_type, party):
    if not party_type or not party:
        frappe.throw("Select both Party Type and Party.")
    meta = frappe.get_meta(party_type)
    if meta.issingle or meta.istable or party_type in ("User", "File"):
        frappe.throw("This party type is not supported.")
    doc = frappe.get_doc(party_type, party)
    doc.check_permission("read")
    return doc


def numbers(party_type, party, search_text=""):
    doc = check_party(party_type, party)
    found = {}

    def add(value, source):
        if not value:
            return
        try:
            value = normalize_phone(value)
        except frappe.ValidationError:
            frappe.clear_messages()
            return
        if search_text.lower() in (value + " " + source).lower():
            found.setdefault(value, {"number": value, "label": source})

    for field in doc.meta.fields:
        if field.permlevel and not doc.has_permlevel_access_to(field.fieldname, permission_type="read"):
            continue
        if field.options == "Phone" or field.fieldname in (
            "phone",
            "phone_number",
            "mobile",
            "mobile_no",
            "mobile_number",
            "contact_mobile",
            "contact_phone",
        ):
            add(doc.get(field.fieldname), field.label)
    contacts = frappe.get_all(
        "Dynamic Link",
        filters={"parenttype": "Contact", "link_doctype": party_type, "link_name": party},
        pluck="parent",
    )
    if party_type == "Contact":
        contacts.append(party)
    for name in set(contacts):
        contact = frappe.get_doc("Contact", name)
        if not contact.has_permission("read"):
            continue
        for row in contact.phone_nos:
            add(row.phone, f"{contact.full_name or contact.name}: Phone")
    return list(found.values())


def resolve_recipient(to=None, party_type=None, party=None):
    options = numbers(party_type, party) if party_type or party else []
    if to:
        return normalize_phone(to)
    if len(options) != 1:
        frappe.throw(
            "Select a recipient number. The party must have exactly one number to send without a selection."
        )
    return options[0]["number"]
