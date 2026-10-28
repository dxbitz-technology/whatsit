from datetime import timedelta
import frappe
from frappe.utils import now_datetime


def recover():
    now = now_datetime()
    for name in frappe.get_all(
        "WhatsApp Message",
        filters={"status": "Queued", "next_attempt_at": ["<=", now]},
        pluck="name",
        limit=200,
    ):
        frappe.enqueue("whatsit.jobs.dispatch.send", message=name, enqueue_after_commit=True)
    for name in frappe.get_all(
        "WhatsApp Message",
        filters={
            "status": "Queued",
            "next_attempt_at": ["is", "not set"],
            "creation": ["<", now - timedelta(minutes=5)],
        },
        pluck="name",
        limit=200,
    ):
        frappe.enqueue("whatsit.jobs.dispatch.send", message=name, enqueue_after_commit=True)
    frappe.db.set_value(
        "WhatsApp Message",
        {"status": "Sending", "last_attempt_at": ["<", now - timedelta(minutes=15)]},
        {
            "status": "Unknown",
            "error_details": "Delivery is uncertain after a worker interruption. Check Meta before sending again.",
        },
    )
    for name in frappe.get_all(
        "WhatsApp Webhook Event",
        filters={
            "processing_status": ["in", ["Queued", "Failed"]],
            "attempt_count": ["<", 6],
            "next_attempt_at": ["<=", now],
        },
        pluck="name",
        limit=200,
    ):
        frappe.enqueue("whatsit.services.webhook_processor.process", event=name, enqueue_after_commit=True)
