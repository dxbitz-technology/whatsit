import frappe
from whatsit.services.meta_client import MetaClient, MetaError

LIMITS = {"image/jpeg": 5 * 1024 * 1024, "image/png": 5 * 1024 * 1024, "application/pdf": 100 * 1024 * 1024}


def detect_mime(content):
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    frappe.throw("Choose a JPEG, PNG or PDF file.")


def validate_content(content, expected=None):
    if isinstance(content, str):
        content = content.encode()
    mime = detect_mime(content)
    if expected and mime != expected:
        frappe.throw("The attachment content does not match its type.")
    if len(content) > LIMITS[mime]:
        frappe.throw("Images must be within 5 MB and PDFs within 100 MB.")
    return content, mime


def get_file(name, check_access=True):
    doc = frappe.get_doc("File", name)
    if check_access:
        doc.check_permission("read")
    if doc.is_folder or doc.is_remote_file or not doc.is_private:
        frappe.throw("Choose a private uploaded file.")
    # v16 tries legacy text encodings unless explicitly disabled. Media must remain binary.
    if int(frappe.__version__.split(".")[0]) >= 16:
        content = doc.get_content(encodings=[])
    else:
        content = doc.get_content()
    content, mime = validate_content(content)
    return doc, content, mime


def upload(sender, name, message=None):
    doc, content, mime = get_file(name, check_access=False)
    data = MetaClient(sender, message).request(
        "POST",
        sender.meta_phone_number_id + "/media",
        data={"messaging_product": "whatsapp", "type": mime},
        files={"file": (doc.file_name, content, mime)},
    )
    if not data.get("id"):
        raise MetaError("MEDIA_ID_MISSING")
    return data["id"], mime, doc.file_name


def retrieve(message):
    if message.media_file or not message.meta_media_id:
        return
    sender = frappe.get_doc("WhatsApp Number", message.sender)
    client = MetaClient(sender, message.name)
    data = client.request("GET", message.meta_media_id)
    mime = data.get("mime_type")
    if mime not in LIMITS or int(data.get("file_size", 0)) > LIMITS[mime]:
        raise MetaError("UNSUPPORTED_MEDIA")
    content = client.download(data["url"], LIMITS[mime])
    content, mime = validate_content(content, mime)
    extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[mime]
    file = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": message.name + extension,
            "content": content,
            "is_private": 1,
            "attached_to_doctype": "WhatsApp Message",
            "attached_to_name": message.name,
        }
    ).insert(ignore_permissions=True)
    message.db_set("media_file", file.name)
