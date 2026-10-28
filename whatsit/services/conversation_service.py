from datetime import timedelta
import frappe
from frappe.utils import get_datetime, now_datetime


def lock_sender(sender):
    frappe.db.get_value("WhatsApp Number", sender, "name", for_update=True)


def find(sender, remote):
    return frappe.db.get_value("WhatsApp Conversation", {"sender": sender, "remote_number": remote}, "name")


def ensure_window(sender, remote):
    name = find(sender, remote)
    expiry = frappe.db.get_value("WhatsApp Conversation", name, "service_window_expires_at") if name else None
    if not expiry or get_datetime(expiry) <= now_datetime():
        frappe.throw("Reply window closed. Send an approved template.")


def record(message, incoming_at=None):
    lock_sender(message.sender)
    name = find(message.sender, message.remote_number)
    if not name and message.direction != "Incoming":
        return
    if name:
        doc = frappe.get_doc("WhatsApp Conversation", name)
    else:
        doc = frappe.get_doc(
            {
                "doctype": "WhatsApp Conversation",
                "sender": message.sender,
                "remote_number": message.remote_number,
                "party_type": message.party_type,
                "party": message.party,
                "status": "Open",
                "unread_count": 0,
            }
        )
    doc.flags.internal_update = True
    timestamp = get_datetime(incoming_at or message.creation or now_datetime())
    if not doc.last_message_at or timestamp >= get_datetime(doc.last_message_at):
        doc.last_message_at = timestamp
        doc.last_message_preview = (message.text or message.message_type)[:200]
    if message.direction == "Incoming":
        if not doc.last_incoming_at or timestamp > get_datetime(doc.last_incoming_at):
            doc.last_incoming_at = timestamp
            doc.service_window_expires_at = timestamp + timedelta(hours=24)
        doc.unread_count = (doc.unread_count or 0) + 1
        doc.status = "Open"
    doc.save(ignore_permissions=True)
    # One message store: attach historical outgoing records without copying them.
    frappe.db.set_value(
        "WhatsApp Message",
        {"sender": message.sender, "remote_number": message.remote_number},
        "conversation",
        doc.name,
        update_modified=False,
    )
    return doc.name
