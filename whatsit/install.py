import frappe

ROLES = ("WhatsApp Manager", "WhatsApp User", "WhatsApp API User")


def ensure_roles():
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
                ignore_permissions=True
            )


def after_migrate():
    frappe.db.add_unique("WhatsApp Conversation", ["sender", "remote_number"], "unique_sender_remote")
    frappe.db.add_unique(
        "WhatsApp Template", ["waba_id", "template_name", "language"], "unique_waba_template"
    )
    frappe.db.add_index("WhatsApp Message", ["sender", "remote_number", "creation"])
    frappe.db.add_index("WhatsApp Message", ["status", "next_attempt_at"])
