import frappe

ROLES = {"WhatsApp Manager", "WhatsApp User", "WhatsApp API User"}


def is_manager(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or "WhatsApp Manager" in frappe.get_roles(user)


def require_user():
    if frappe.session.user == "Guest" or not ROLES.intersection(frappe.get_roles()):
        frappe.throw("WhatsApp access is required.", frappe.PermissionError)


def require_manager():
    if not is_manager():
        frappe.throw("WhatsApp Manager access is required.", frappe.PermissionError)


def allowed_numbers(user=None):
    user = user or frappe.session.user
    if user == "Guest" or not ROLES.intersection(frappe.get_roles(user)):
        return []
    if is_manager(user):
        return frappe.get_all("WhatsApp Number", pluck="name")
    return frappe.get_all(
        "WhatsApp Number User", filters={"user": user, "parenttype": "WhatsApp Number"}, pluck="parent"
    )


def check_number(name, user=None):
    if name not in allowed_numbers(user):
        frappe.throw("You cannot use this sender.", frappe.PermissionError)


def number_permission(doc, user=None, ptype=None, **kwargs):
    user = user or frappe.session.user
    return is_manager(user) or (ptype in ("read", "select") and doc.name in allowed_numbers(user))


def sender_permission(doc, user=None, ptype=None, **kwargs):
    user = user or frappe.session.user
    return is_manager(user) or (ptype in ("read", "select") and doc.sender in allowed_numbers(user))


def template_permission(doc, user=None, ptype=None, **kwargs):
    user = user or frappe.session.user
    if is_manager(user):
        return True
    return ptype in ("read", "select") and bool(
        frappe.db.exists("WhatsApp Number", {"name": ["in", allowed_numbers(user)], "waba_id": doc.waba_id})
    )


def _in(values):
    return ",".join(frappe.db.escape(v) for v in values) or "''"


def number_query(user=None):
    return "" if is_manager(user) else f"`tabWhatsApp Number`.name in ({_in(allowed_numbers(user))})"


def message_query(user=None):
    return "" if is_manager(user) else f"`tabWhatsApp Message`.sender in ({_in(allowed_numbers(user))})"


def conversation_query(user=None):
    return "" if is_manager(user) else f"`tabWhatsApp Conversation`.sender in ({_in(allowed_numbers(user))})"


def template_query(user=None):
    return (
        ""
        if is_manager(user)
        else f"`tabWhatsApp Template`.waba_id in (select waba_id from `tabWhatsApp Number` where name in ({_in(allowed_numbers(user))}))"
    )
