from datetime import timedelta
import frappe
from frappe.utils import now_datetime


def run():
    # Message and attachment retention belongs to the site's record policy.
    now = now_datetime()
    for row in frappe.get_all(
        "WhatsApp Webhook Event",
        filters={"received_at": ["<", now - timedelta(days=7)]},
        pluck="name",
        limit=1000,
    ):
        frappe.db.set_value("WhatsApp Webhook Event", row, "payload", None, update_modified=False)
    frappe.db.delete(
        "WhatsApp Webhook Event",
        {"received_at": ["<", now - timedelta(days=30)], "processing_status": "Processed"},
    )
    frappe.db.delete("WhatsApp API Log", {"created_at": ["<", now - timedelta(days=30)]})
