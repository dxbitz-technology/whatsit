import frappe
from whatsit.permissions import require_user
from whatsit.services.party_resolver import numbers


@frappe.whitelist()
def get_numbers(party_type, party, search_text=""):
    require_user()
    return numbers(party_type, party, search_text)


@frappe.whitelist()
def get_party_types():
    require_user()
    from frappe.permissions import get_doctypes_with_read

    readable = set(get_doctypes_with_read())
    return [
        d.name
        for d in frappe.get_all("DocType", filters={"issingle": 0, "istable": 0}, fields=["name"])
        if d.name in readable and d.name not in ("User", "File")
    ]
