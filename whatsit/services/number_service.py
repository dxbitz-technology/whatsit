import re
import frappe
import phonenumbers
from frappe.utils import now_datetime
from whatsit.permissions import check_number, require_user, require_manager


def normalize_phone(value):
    value = str(value or "").strip()
    if not value.startswith("+"):
        frappe.throw("Use a phone number with + and the country code.")
    try:
        phone = phonenumbers.parse(value, None)
        if not phonenumbers.is_possible_number(phone):
            raise ValueError()
        return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)
    except (ValueError, phonenumbers.NumberParseException):
        frappe.throw("Enter a valid phone number with its country code.")


def resolve_sender(name=None, check_access=True):
    if check_access:
        require_user()
    if not name:
        name = frappe.db.get_value("WhatsApp Number", {"enabled": 1, "is_default": 1}, "name")
        if not name:
            frappe.throw("Select a sender or set an enabled default sender.")
    if check_access:
        check_number(name)
    doc = frappe.get_doc("WhatsApp Number", name)
    if not doc.enabled or not frappe.db.get_value("WhatsApp Meta App", doc.meta_app, "enabled"):
        frappe.throw("This sender or its Meta app is disabled.")
    return doc


def validate_number(doc):
    doc.phone_number = normalize_phone(doc.phone_number)
    for key in ("meta_phone_number_id", "waba_id"):
        if not str(doc.get(key) or "").isdigit():
            frappe.throw("Meta phone and business account IDs must contain digits only.")
    if not re.fullmatch(r"v\d{2}\.0", doc.api_version or ""):
        frappe.throw("Use an API version such as v26.0.")
    doc.title = f"{doc.display_name} ({doc.phone_number})"
    doc.default_slot = "default" if doc.enabled and doc.is_default else None
    if not doc.enabled:
        doc.is_default = 0
    if doc.default_slot and frappe.db.exists(
        "WhatsApp Number", {"default_slot": "default", "name": ["!=", doc.name or ""]}
    ):
        frappe.throw("Another sender is already the default. Clear its Default Sender first.")
    # A unique nullable column is the database guard for concurrent default changes.
    users = [x.user for x in doc.allowed_users]
    if len(set(users)) != len(users):
        frappe.throw("Each user can be listed only once.")


@frappe.whitelist(methods=["POST"])
def test_connection(name):
    require_manager()
    doc = resolve_sender(name)
    from whatsit.services.meta_client import MetaClient, MetaError

    try:
        result = MetaClient(doc).request(
            "GET", doc.meta_phone_number_id, params={"fields": "id,display_phone_number,verified_name"}
        )
        if str(result.get("id")) != doc.meta_phone_number_id:
            frappe.throw("Meta returned a different phone identity.")
        remote = "+" + re.sub(r"\D", "", result.get("display_phone_number", ""))
        if normalize_phone(remote) != doc.phone_number:
            frappe.throw("The configured phone number does not match Meta.")
        doc.db_set({"connection_status": "Connected", "last_tested_at": now_datetime()})
        return {"status": "Connected", "phone_number": remote}
    except frappe.ValidationError:
        doc.db_set({"connection_status": "Failed", "last_tested_at": now_datetime()})
        return {"status": "Failed", "error": "The configured phone identity does not match Meta."}
    except MetaError:
        doc.db_set({"connection_status": "Failed", "last_tested_at": now_datetime()})
        return {"status": "Failed", "error": "Meta rejected the connection. Check API Logs."}
