import frappe
from frappe.utils import now_datetime, get_datetime
from whatsit.permissions import require_user, allowed_numbers, check_number

MESSAGE_FIELDS = [
    "name",
    "direction",
    "sender",
    "remote_number",
    "party",
    "message_type",
    "text",
    "response_payload",
    "media_file",
    "status",
    "error_details",
    "creation",
    "received_at",
    "sent_at",
    "template",
]


@frappe.whitelist()
def senders():
    require_user()
    names = allowed_numbers()
    return frappe.get_all(
        "WhatsApp Number",
        filters={"name": ["in", names], "enabled": 1},
        fields=["name", "title", "is_default", "waba_id"],
    )


def visible_senders(sender=None):
    require_user()
    if sender:
        check_number(sender)
        return [sender]
    return allowed_numbers()


@frappe.whitelist()
def list_items(tab="Conversations", sender=None, search="", start=0):
    senders = visible_senders(sender)
    if not senders:
        return []
    if tab not in ("Conversations", "Outbox"):
        frappe.throw("Unknown inbox tab.")
    start = max(int(start), 0)
    slots = ",".join("%(sender_" + str(i) + ")s" for i in range(len(senders)))
    params = {"search": "%" + (search or "")[:140] + "%", "start": start}
    params.update({"sender_" + str(i): value for i, value in enumerate(senders)})
    if tab == "Conversations":
        return frappe.db.sql(
            f"""select c.name, c.sender, c.remote_number, c.party, c.last_message_at,
            c.last_message_preview, c.unread_count, c.status, c.service_window_expires_at
            from `tabWhatsApp Conversation` c where c.sender in ({slots})
            and (c.remote_number like %(search)s or c.party like %(search)s or c.last_message_preview like %(search)s
              or exists (select 1 from `tabWhatsApp Message` m where m.sender=c.sender and m.remote_number=c.remote_number and m.text like %(search)s))
            order by c.last_message_at desc limit 50 offset %(start)s""",
            params,
            as_dict=True,
        )
    return frappe.db.sql(
        f"""select m.name, m.sender, m.remote_number, m.party, m.creation as last_message_at,
        m.text as last_message_preview, m.status, m.media_file, m.message_type
        from `tabWhatsApp Message` m where m.direction='Outgoing' and m.sender in ({slots})
        and not exists (select 1 from `tabWhatsApp Conversation` c where c.sender=m.sender and c.remote_number=m.remote_number)
        and (m.remote_number like %(search)s or m.party like %(search)s or m.text like %(search)s)
        order by m.creation desc limit 50 offset %(start)s""",
        params,
        as_dict=True,
    )


@frappe.whitelist()
def thread(sender, remote_number, before=None):
    visible_senders(sender)
    filters = {"sender": sender, "remote_number": remote_number}
    if before:
        filters["creation"] = ["<", before]
    messages = frappe.get_all(
        "WhatsApp Message",
        filters=filters,
        fields=MESSAGE_FIELDS,
        order_by="creation desc, name desc",
        limit=50,
    )
    for row in messages:
        row["attachment"] = attachment(row.media_file) if row.media_file else None
    conversation = frappe.db.get_value(
        "WhatsApp Conversation",
        {"sender": sender, "remote_number": remote_number},
        ["name", "service_window_expires_at", "status"],
        as_dict=True,
    )
    return {
        "messages": list(reversed(messages)),
        "conversation": conversation,
        "reply_open": bool(
            conversation
            and conversation.service_window_expires_at
            and get_datetime(conversation.service_window_expires_at) > now_datetime()
        ),
    }


def attachment(name):
    file = frappe.get_doc("File", name)
    file.check_permission("read")
    return {"name": name, "file_name": file.file_name, "url": file.file_url}


@frappe.whitelist(methods=["POST"])
def mark_read(conversation):
    require_user()
    doc = frappe.get_doc("WhatsApp Conversation", conversation)
    check_number(doc.sender)
    doc.db_set("unread_count", 0)


@frappe.whitelist(methods=["POST"])
def set_status(conversation, status):
    require_user()
    doc = frappe.get_doc("WhatsApp Conversation", conversation)
    check_number(doc.sender)
    if status not in ("Open", "Closed"):
        frappe.throw("Choose Open or Closed.")
    doc.db_set("status", status)
