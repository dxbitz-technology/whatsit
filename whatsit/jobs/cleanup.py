from datetime import timedelta
import frappe
from frappe.utils import now_datetime


def run():
    # Message and attachment retention belongs to the site's record policy.
    now = now_datetime()
    while names := frappe.get_all(
        "WhatsApp Webhook Event",
        filters={"received_at": ["<", now - timedelta(days=7)], "payload": ["is", "set"]},
        pluck="name",
        limit=1000,
    ):
        frappe.db.set_value(
            "WhatsApp Webhook Event",
            {"name": ["in", names]},
            {"payload": None, "next_attempt_at": None},
            update_modified=False,
        )
    frappe.db.delete(
        "WhatsApp Webhook Event",
        {"received_at": ["<", now - timedelta(days=30)], "processing_status": "Processed"},
    )
    frappe.db.delete("WhatsApp API Log", {"created_at": ["<", now - timedelta(days=30)]})
