import frappe
from whatsit.services.conversation_service import resolve_party


def execute():
    last_name = ""
    while rows := frappe.get_all(
        "WhatsApp Conversation",
        filters={"name": [">", last_name], "party": ["is", "not set"]},
        fields=["name", "sender", "remote_number"],
        order_by="name asc",
        limit=500,
    ):
        for row in rows:
            if association := resolve_party(row.sender, row.remote_number):
                frappe.db.set_value(
                    "WhatsApp Conversation",
                    row.name,
                    dict(zip(("party_type", "party"), association)),
                    update_modified=False,
                )
        last_name = rows[-1].name
