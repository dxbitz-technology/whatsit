import json

import hashlib
import hmac
from datetime import timedelta
from unittest.mock import patch, Mock
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from whatsit.api.messages import send_text, send_template, send_media
from whatsit.api import conversations, templates
from whatsit.api.webhook import verify_signature
from whatsit.permissions import check_number
from whatsit.services.number_service import normalize_phone, resolve_sender
from whatsit.services import webhook_processor
from whatsit.services.meta_client import MetaError
from whatsit.jobs.dispatch import send, handle_failure


class TestMessaging(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.queue_patch = patch("frappe.enqueue")
        self.enqueued = self.queue_patch.start()
        self.addCleanup(self.queue_patch.stop)
        self.rate_patch = patch("whatsit.services.message_service.rate_limit")
        self.rate_patch.start()
        self.addCleanup(self.rate_patch.stop)
        suffix = frappe.generate_hash(length=8)
        self.app = frappe.get_doc(
            {
                "doctype": "WhatsApp Meta App",
                "display_name": "Test " + suffix,
                "meta_app_id": str(int(suffix, 36))[:15],
                "app_secret": "test-secret",
                "verify_token": "verify-secret",
                "enabled": 1,
            }
        ).insert()
        self.number = self.make_number("+971501234567", "1001", True)
        self.second = self.make_number("+971501234568", "1002")
        self.template = frappe.get_doc(
            {
                "doctype": "WhatsApp Template",
                "waba_id": "2001",
                "sender": self.number.name,
                "template_name": "invoice_" + suffix,
                "language": "en",
                "category": "UTILITY",
                "body": "Hello {{1}}",
                "variables": [{"component": "BODY", "parameter_index": 1, "sample_value": "Ahmed"}],
            }
        ).insert()
        self.template.db_set("status", "APPROVED")
        self.template.reload()
        self.addCleanup(frappe.set_user, "Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        # Dispatch deliberately commits its claim. Explicit cleanup keeps repeat runs isolated.
        message_names = frappe.get_all(
            "WhatsApp Message", filters={"sender": ["in", [self.number.name, self.second.name]]}, pluck="name"
        )
        for attachment in frappe.get_all(
            "File",
            filters={"attached_to_doctype": "WhatsApp Message", "attached_to_name": ["in", message_names]},
            pluck="name",
        ):
            frappe.db.set_value("WhatsApp Message", {"media_file": attachment}, "media_file", None)
            frappe.delete_doc("File", attachment, force=True)
        for dt in [
            "WhatsApp API Log",
            "WhatsApp Message",
            "WhatsApp Conversation",
            "WhatsApp Template",
            "WhatsApp Webhook Event",
        ]:
            if dt == "WhatsApp Webhook Event":
                frappe.db.delete(dt, {"meta_app": self.app.name})
            elif dt == "WhatsApp Template":
                frappe.db.delete("WhatsApp Template Variable", {"parent": self.template.name})
                frappe.db.delete(dt, {"waba_id": "2001"})
            else:
                frappe.db.delete(dt, {"sender": ["in", [self.number.name, self.second.name]]})
        for number in [self.number, self.second]:
            frappe.db.delete("WhatsApp Number User", {"parent": number.name})
            frappe.db.delete("WhatsApp Number", {"name": number.name})
        frappe.delete_doc("WhatsApp Meta App", self.app.name, force=True)
        frappe.db.commit()
        super().tearDown()

    def pdf(self):
        from io import BytesIO
        from pypdf import PdfWriter

        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        return stream.getvalue()

    def make_number(self, phone, phone_id, default=False):
        return frappe.get_doc(
            {
                "doctype": "WhatsApp Number",
                "display_name": "Support",
                "phone_number": phone,
                "meta_phone_number_id": phone_id,
                "waba_id": "2001",
                "meta_app": self.app.name,
                "access_token": "test-token",
                "enabled": 1,
                "is_default": int(default),
                "api_version": "v26.0",
            }
        ).insert()

    def inbound(self, sender=None, meta_id="incoming-1", timestamp=None):
        webhook_processor.incoming(
            sender or self.number.name,
            {
                "id": meta_id,
                "from": "971509876543",
                "timestamp": str(timestamp or int(now_datetime().timestamp())),
                "type": "text",
                "text": {"body": "Hello"},
            },
        )

    def queued_template(self, **kwargs):
        return send_template(
            from_number=self.number.name,
            to="+971509876543",
            template=self.template.name,
            variables={"1": "Ahmed"},
            **kwargs,
        )

    def test_normalize(self):
        self.assertEqual(normalize_phone("+971 50 123 4567"), "+971501234567")
        with self.assertRaises(frappe.ValidationError):
            normalize_phone("0501234567")

    def test_default_and_explicit_sender(self):
        self.assertEqual(resolve_sender().name, self.number.name)
        self.assertEqual(resolve_sender(self.second.name).name, self.second.name)
        self.number.db_set("enabled", 0)
        with self.assertRaises(frappe.ValidationError):
            resolve_sender(self.number.name)

    def test_default_unique(self):
        self.second.is_default = 1
        with self.assertRaises(frappe.ValidationError):
            self.second.save()

    def test_multiple_apps_have_distinct_routes(self):
        second = frappe.get_doc(
            {
                "doctype": "WhatsApp Meta App",
                "display_name": "Other App",
                "meta_app_id": "9001",
                "app_secret": "other-secret",
                "verify_token": "other-verify",
            }
        ).insert()
        self.assertNotEqual(second.route_key, self.app.route_key)
        signature = "sha256=" + hmac.new(b"test-secret", b"payload", hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(self.app.get_password("app_secret"), b"payload", signature))
        self.assertFalse(verify_signature(second.get_password("app_secret"), b"payload", signature))
        frappe.delete_doc("WhatsApp Meta App", second.name)

    def test_signature_rejects_forgery(self):
        self.assertFalse(verify_signature("secret", b"payload", "sha256=bad"))
        self.assertFalse(verify_signature("secret", b"payload", ""))

    def test_service_window(self):
        with self.assertRaises(frappe.ValidationError):
            send_text(self.number.name, "+971509876543", "Hello")
        self.inbound()
        result = send_text(self.number.name, "+971509876543", "Reply")
        self.assertEqual(result["status"], "Queued")
        c = frappe.db.get_value("WhatsApp Conversation", {"sender": self.number.name}, "name")
        frappe.db.set_value(
            "WhatsApp Conversation", c, "service_window_expires_at", now_datetime() - timedelta(seconds=1)
        )
        with self.assertRaises(frappe.ValidationError):
            send_text(self.number.name, "+971509876543", "Late reply")
        self.assertEqual(self.queued_template()["status"], "Queued")

    def test_unapproved_template(self):
        self.template.db_set("status", "PENDING")
        with self.assertRaises(frappe.ValidationError):
            self.queued_template()

    def test_waba_shared_template(self):
        result = send_template(
            self.second.name, "+971509876543", self.template.template_name, "en", {"1": "Ahmed"}
        )
        self.assertEqual(
            frappe.db.get_value("WhatsApp Message", result["message_id"], "sender"), self.second.name
        )

    def test_template_variables_required(self):
        with self.assertRaises(frappe.ValidationError):
            send_template(self.number.name, "+971509876543", self.template.name)

    def test_idempotency(self):
        a = self.queued_template(idempotency_key="request-1")
        b = self.queued_template(idempotency_key="request-1")
        self.assertEqual(a["message_id"], b["message_id"])
        with self.assertRaises(frappe.ValidationError):
            send_template(
                self.number.name,
                "+971509876544",
                self.template.name,
                variables={"1": "Ahmed"},
                idempotency_key="request-1",
            )

    def test_outbox_moves_without_copying(self):
        outgoing = self.queued_template()
        self.assertEqual(len(conversations.list_items("Outbox", self.number.name)), 1)
        self.inbound()
        self.assertEqual(conversations.list_items("Outbox", self.number.name), [])
        rows = conversations.thread(self.number.name, "+971509876543")["messages"]
        self.assertEqual(len(rows), 2)
        self.assertIn(outgoing["message_id"], [m.name for m in rows])

    def test_inbound_deduplication(self):
        self.inbound()
        self.inbound()
        self.assertEqual(frappe.db.count("WhatsApp Message", {"sender": self.number.name}), 1)
        self.assertEqual(
            frappe.db.get_value("WhatsApp Conversation", {"sender": self.number.name}, "unread_count"), 1
        )

    def test_inbound_does_not_regress_window(self):
        self.inbound()
        c = frappe.db.get_value("WhatsApp Conversation", {"sender": self.number.name}, "name")
        previous = frappe.db.get_value("WhatsApp Conversation", c, "last_incoming_at")
        from datetime import datetime, timezone

        self.inbound(meta_id="older", timestamp=int(datetime.now(timezone.utc).timestamp()) - 7200)
        self.assertEqual(frappe.db.get_value("WhatsApp Conversation", c, "last_incoming_at"), previous)

    def test_delivery_order_and_duplicate(self):
        result = self.queued_template()
        frappe.db.set_value(
            "WhatsApp Message", result["message_id"], {"meta_message_id": "wamid.1", "status": "Sent"}
        )

        def status(value):
            webhook_processor.status_update(
                self.number.name, {"id": "wamid.1", "status": value, "timestamp": "1793188800"}
            )

        status("failed")
        status("sent")
        self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "status"), "Failed")
        status("read")
        status("delivered")
        status("read")
        status("failed")
        self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "status"), "Read")

    def test_missing_status_is_retriable(self):
        with self.assertRaises(MetaError):
            webhook_processor.status_update(self.number.name, {"id": "unknown", "status": "read"})

    def test_dispatch(self):
        result = self.queued_template()
        with patch("whatsit.services.meta_client.requests.request") as request:
            response = Mock(ok=True, status_code=200)
            response.json.return_value = {"messages": [{"id": "wamid.outgoing"}]}
            request.return_value = response
            send(result["message_id"])
        doc = frappe.get_doc("WhatsApp Message", result["message_id"])
        self.assertEqual(doc.status, "Sent")
        self.assertEqual(doc.meta_message_id, "wamid.outgoing")
        self.assertNotIn("test-token", frappe.as_json(frappe.get_all("WhatsApp API Log", fields=["*"])))

    def test_dispatch_rechecks_approval(self):
        result = self.queued_template()
        self.template.db_set("status", "REJECTED")
        with patch("whatsit.services.meta_client.requests.request") as request:
            send(result["message_id"])
            request.assert_not_called()
        self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "status"), "Failed")

    def test_retry_bounded_and_ambiguous(self):
        result = self.queued_template()
        doc = frappe.get_doc("WhatsApp Message", result["message_id"])
        handle_failure(doc, MetaError("429", transient=True))
        doc.reload()
        self.assertEqual(doc.status, "Queued")
        self.assertIsNotNone(doc.next_attempt_at)
        doc.retry_count = 5
        handle_failure(doc, MetaError("429", transient=True))
        doc.reload()
        self.assertEqual(doc.status, "Failed")
        handle_failure(doc, MetaError("NETWORK", ambiguous=True))
        doc.reload()
        self.assertEqual(doc.status, "Unknown")

    def test_template_submit_and_sync(self):
        self.template.db_set("status", "DRAFT")
        with patch(
            "whatsit.services.meta_client.MetaClient.request",
            return_value={"id": "3001", "status": "PENDING"},
        ) as call:
            templates.submit(self.template.name, self.number.name)
            self.assertEqual(
                call.call_args.kwargs["json"]["components"][0]["example"], {"body_text": [["Ahmed"]]}
            )
        with patch(
            "whatsit.services.meta_client.MetaClient.request",
            return_value={
                "data": [
                    {
                        "id": "3001",
                        "name": self.template.template_name,
                        "language": "en",
                        "status": "APPROVED",
                        "category": "UTILITY",
                        "components": [{"type": "BODY", "text": "Hello {{1}}"}],
                    }
                ]
            },
        ):
            templates.sync(self.second.name)
        self.assertEqual(frappe.db.get_value("WhatsApp Template", self.template.name, "status"), "APPROVED")

    def test_submitted_template_immutable(self):
        self.template.db_set("meta_template_id", "3001")
        self.template.reload()
        self.template.body = "Changed"
        with self.assertRaises(frappe.ValidationError):
            self.template.save()

    def test_unauthorized_and_guest(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            self.queued_template()
        with self.assertRaises(frappe.PermissionError):
            conversations.thread(self.number.name, "+971509876543")
        with self.assertRaises(frappe.PermissionError):
            check_number(self.number.name)

    def test_media_content_and_file_permissions(self):
        from whatsit.services.media_service import validate_content, get_file

        with self.assertRaises(frappe.ValidationError):
            validate_content(b"<html>file.pdf</html>")
        with self.assertRaises(frappe.ValidationError):
            validate_content(b"%PDF-test", "image/png")
        with self.assertRaises(frappe.ValidationError):
            validate_content(b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024))
        file = frappe.get_doc(
            {"doctype": "File", "file_name": "invoice.pdf", "is_private": 1, "content": self.pdf()}
        ).insert()
        self.assertEqual(get_file(file.name)[2], "application/pdf")
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            get_file(file.name)
        frappe.set_user("Administrator")
        frappe.delete_doc("File", file.name)

    def test_media_send_upload(self):
        self.inbound()
        file = frappe.get_doc(
            {"doctype": "File", "file_name": "invoice.pdf", "is_private": 1, "content": self.pdf()}
        ).insert()
        result = send_media(self.number.name, "+971509876543", file.name, "Invoice")
        with (
            patch("whatsit.jobs.dispatch.upload", return_value=("media1", "application/pdf", "invoice.pdf")),
            patch(
                "whatsit.services.meta_client.MetaClient.request",
                return_value={"messages": [{"id": "wamid.pdf"}]},
            ),
        ):
            send(result["message_id"])
        self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "status"), "Sent")
        frappe.db.set_value("WhatsApp Message", result["message_id"], "media_file", None)
        frappe.delete_doc("File", file.name)

    def test_template_webhook_waba_routing(self):
        self.template.db_set("meta_template_id", "3001")
        webhook_processor.template_update(
            self.app.name,
            "2001",
            {"message_template_id": "3001", "event": "REJECTED", "reason": "Invalid format"},
        )
        self.assertEqual(frappe.db.get_value("WhatsApp Template", self.template.name, "status"), "REJECTED")
        with self.assertRaises(MetaError):
            webhook_processor.template_update(
                self.app.name, "other", {"message_template_id": "3001", "event": "APPROVED"}
            )

    def make_user(self, role="WhatsApp User"):
        name = "wa-" + frappe.generate_hash(length=8) + "@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": name,
                "first_name": "Inbox User",
                "send_welcome_email": 0,
                "roles": [{"role": role}],
            }
        ).insert()
        self.addCleanup(self.delete_user, user.name)
        return user.name

    def delete_user(self, name):
        frappe.set_user("Administrator")
        frappe.db.delete("WhatsApp Number User", {"user": name})
        frappe.delete_doc("User", name, force=True)

    def test_per_number_user_access_and_secret_fields(self):
        user = self.make_user()
        self.number.append("allowed_users", {"user": user})
        self.number.save()
        frappe.set_user(user)
        self.assertEqual([x.name for x in conversations.senders()], [self.number.name])
        with self.assertRaises(frappe.PermissionError):
            conversations.thread(self.second.name, "+971509876543")
        self.assertFalse(frappe.has_permission("WhatsApp Meta App", "read", doc=self.app))
        self.assertFalse(frappe.has_permission("WhatsApp API Log", "read"))
        self.assertFalse(frappe.has_permission("WhatsApp Webhook Event", "read"))
        visible = frappe.get_list("WhatsApp Number", pluck="name")
        self.assertIn(self.number.name, visible)
        self.assertNotIn(self.second.name, visible)
        doc = frappe.get_doc("WhatsApp Number", self.number.name)
        self.assertTrue(doc.has_permission("read"))
        self.assertFalse(frappe.get_doc("WhatsApp Number", self.second.name).has_permission("read"))
        doc.apply_fieldlevel_read_permissions()
        self.assertFalse(doc.get("access_token"))
        self.assertFalse(doc.get("allowed_users"))
        self.assertEqual(self.queued_template()["status"], "Queued")

    def test_party_permission_and_ambiguous_numbers(self):
        from whatsit.services.party_resolver import resolve_recipient
        from whatsit.api.contacts import get_numbers

        contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": "Party Test",
                "phone_nos": [
                    {"phone": "+971501234567"},
                    {"phone": "+971501234568"},
                    {"phone": "+971 50 123 4567"},
                ],
            }
        ).insert()
        self.addCleanup(frappe.delete_doc, "Contact", contact.name, force=True)
        numbers = get_numbers("Contact", contact.name)
        self.assertEqual(len(numbers), 2)
        with self.assertRaises(frappe.ValidationError):
            resolve_recipient(None, "Contact", contact.name)
        self.assertEqual(resolve_recipient("+971501234568", "Contact", contact.name), "+971501234568")
        user = self.make_user("WhatsApp API User")
        frappe.set_user(user)
        with self.assertRaises(frappe.PermissionError):
            get_numbers("Contact", contact.name)

    def test_webhook_receipt_signature_and_app_mapping(self):
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        from whatsit.api.webhook import receive

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "2001",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": self.number.meta_phone_number_id},
                                "messages": [
                                    {
                                        "id": "webhook-message-1",
                                        "from": "971509876543",
                                        "timestamp": "1793188800",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        original = getattr(frappe.local, "request", None)
        self.addCleanup(setattr, frappe.local, "request", original)
        frappe.local.request = Request(
            EnvironBuilder(
                method="POST",
                data=body,
                content_type="application/json",
                headers={"X-Hub-Signature-256": signature},
            ).get_environ()
        )
        receive(self.app.route_key)
        receive(self.app.route_key)
        events = frappe.get_all("WhatsApp Webhook Event", filters={"meta_app": self.app.name}, pluck="name")
        self.assertEqual(len(events), 1)
        webhook_processor.process(events[0])
        self.assertEqual(
            frappe.db.get_value("WhatsApp Webhook Event", events[0], "processing_status"), "Processed"
        )
        webhook_processor.process(events[0])
        self.assertEqual(frappe.db.count("WhatsApp Message", {"meta_message_id": "webhook-message-1"}), 1)
        frappe.local.request = Request(
            EnvironBuilder(
                method="POST",
                data=body,
                content_type="application/json",
                headers={"X-Hub-Signature-256": "sha256=bad"},
            ).get_environ()
        )
        with self.assertRaises(frappe.PermissionError):
            receive(self.app.route_key)

    def test_wrong_app_cannot_route_sender(self):
        other = frappe.get_doc(
            {
                "doctype": "WhatsApp Meta App",
                "display_name": "Other App",
                "meta_app_id": "9001",
                "app_secret": "other-secret",
                "verify_token": "verify",
            }
        ).insert()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "2001",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": self.number.meta_phone_number_id},
                                "messages": [
                                    {
                                        "id": "wrong-app",
                                        "from": "971509876543",
                                        "timestamp": "1793188800",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        event = frappe.get_doc(
            {
                "doctype": "WhatsApp Webhook Event",
                "meta_app": other.name,
                "event_key": "wrong-app-key",
                "payload": json.dumps(payload),
                "received_at": now_datetime(),
                "processing_status": "Queued",
            }
        ).insert()
        webhook_processor.process(event.name)
        self.assertEqual(
            frappe.db.get_value("WhatsApp Webhook Event", event.name, "processing_status"), "Failed"
        )
        self.assertFalse(frappe.db.exists("WhatsApp Message", {"meta_message_id": "wrong-app"}))
        frappe.delete_doc("WhatsApp Webhook Event", event.name)
        frappe.delete_doc("WhatsApp Meta App", other.name)

    def test_incoming_private_media(self):
        with (
            patch(
                "whatsit.services.meta_client.MetaClient.request",
                return_value={
                    "url": "https://lookaside.fbsbx.com/media",
                    "mime_type": "application/pdf",
                    "file_size": len(self.pdf()),
                },
            ),
            patch("whatsit.services.meta_client.MetaClient.download", return_value=self.pdf()),
        ):
            webhook_processor.incoming(
                self.number.name,
                {
                    "id": "incoming-pdf",
                    "from": "971509876543",
                    "timestamp": "1793188800",
                    "type": "document",
                    "document": {"id": "12345", "caption": "Document"},
                },
            )
        name = frappe.db.get_value("WhatsApp Message", {"meta_message_id": "incoming-pdf"}, "media_file")
        file = frappe.get_doc("File", name)
        self.assertTrue(file.is_private)
        self.assertEqual(file.attached_to_doctype, "WhatsApp Message")
        user = self.make_user()
        frappe.set_user(user)
        self.assertFalse(file.has_permission("read"))
        frappe.set_user("Administrator")
        frappe.db.set_value("WhatsApp Message", {"meta_message_id": "incoming-pdf"}, "media_file", None)
        frappe.delete_doc("File", file.name)

    def test_webhook_get_challenge(self):
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        from whatsit.api.webhook import receive

        original = getattr(frappe.local, "request", None)
        self.addCleanup(setattr, frappe.local, "request", original)
        frappe.local.request = Request(
            EnvironBuilder(
                method="GET",
                query_string={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "verify-secret",
                    "hub.challenge": "123456",
                },
            ).get_environ()
        )
        response = receive(self.app.route_key)
        self.assertEqual(response.get_data(as_text=True), "123456")
        self.assertTrue(frappe.db.get_value("WhatsApp Meta App", self.app.name, "webhook_verified_at"))
        frappe.local.request = Request(
            EnvironBuilder(
                method="GET",
                query_string={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong",
                    "hub.challenge": "123456",
                },
            ).get_environ()
        )
        with self.assertRaises(frappe.PermissionError):
            receive(self.app.route_key)

    def test_image_sends_and_incoming(self):
        from io import BytesIO
        from PIL import Image

        self.inbound()
        for format, mime, ext in [("JPEG", "image/jpeg", ".jpg"), ("PNG", "image/png", ".png")]:
            stream = BytesIO()
            Image.new("RGB", (8, 8), color="blue").save(stream, format=format)
            content = stream.getvalue()
            file = frappe.get_doc(
                {"doctype": "File", "file_name": "image" + ext, "is_private": 1, "content": content}
            ).insert()
            result = send_media(self.number.name, "+971509876543", file.name, "Image")
            with (
                patch("whatsit.jobs.dispatch.upload", return_value=("media-image", mime, "image" + ext)),
                patch(
                    "whatsit.services.meta_client.MetaClient.request",
                    return_value={"messages": [{"id": "wamid." + ext}]},
                ),
            ):
                send(result["message_id"])
            self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "status"), "Sent")
            with (
                patch(
                    "whatsit.services.meta_client.MetaClient.request",
                    return_value={
                        "url": "https://lookaside.fbsbx.com/media",
                        "mime_type": mime,
                        "file_size": len(content),
                    },
                ),
                patch("whatsit.services.meta_client.MetaClient.download", return_value=content),
            ):
                webhook_processor.incoming(
                    self.number.name,
                    {
                        "id": "incoming-" + ext,
                        "from": "971509876543",
                        "timestamp": "1793188800",
                        "type": "image",
                        "image": {"id": "12345"},
                    },
                )
            incoming_file = frappe.db.get_value(
                "WhatsApp Message", {"meta_message_id": "incoming-" + ext}, "media_file"
            )
            self.assertTrue(frappe.db.get_value("File", incoming_file, "is_private"))
            frappe.db.set_value(
                "WhatsApp Message", {"sender": self.number.name, "media_file": file.name}, "media_file", None
            )
            frappe.db.set_value(
                "WhatsApp Message",
                {"sender": self.number.name, "media_file": incoming_file},
                "media_file",
                None,
            )
            for name in set([file.name, incoming_file]):
                if frappe.db.exists("File", name):
                    frappe.delete_doc("File", name)

    def test_approved_template_snapshot(self):
        result = self.queued_template()
        self.assertEqual(frappe.db.get_value("WhatsApp Message", result["message_id"], "text"), "Hello Ahmed")
        doc = frappe.get_doc("WhatsApp Message", result["message_id"])
        doc.text = "Changed"
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_rate_limit(self):
        self.rate_patch.stop()
        from whatsit.services.message_service import rate_limit

        key = frappe.cache.make_key(
            "whatsapp-send:" + frappe.session.user + ":" + now_datetime().strftime("%Y%m%d%H%M")
        )
        frappe.cache.delete(key)
        try:
            for i in range(60):
                rate_limit()
            with self.assertRaises(frappe.RateLimitExceededError):
                rate_limit()
        finally:
            frappe.cache.delete(key)

    def test_connection_checks_identity(self):
        from whatsit.services.number_service import test_connection

        with patch(
            "whatsit.services.meta_client.MetaClient.request",
            return_value={"id": self.number.meta_phone_number_id, "display_phone_number": "+971 50 123 4567"},
        ):
            self.assertEqual(test_connection(self.number.name)["status"], "Connected")
        with patch(
            "whatsit.services.meta_client.MetaClient.request",
            return_value={"id": self.number.meta_phone_number_id, "display_phone_number": "+971 50 123 4568"},
        ):
            self.assertEqual(test_connection(self.number.name)["status"], "Failed")
        self.assertEqual(
            frappe.db.get_value("WhatsApp Number", self.number.name, "connection_status"), "Failed"
        )

    def test_outgoing_attachment_shared_with_sender_operators(self):
        self.inbound()
        file = frappe.get_doc(
            {"doctype": "File", "file_name": "operator-invoice.pdf", "is_private": 1, "content": self.pdf()}
        ).insert()
        result = send_media(self.number.name, "+971509876543", file.name, "Invoice")
        linked = frappe.db.get_value("WhatsApp Message", result["message_id"], "media_file")
        self.assertNotEqual(linked, file.name)
        self.assertEqual(frappe.db.get_value("File", linked, "attached_to_name"), result["message_id"])
        user = self.make_user()
        self.number.append("allowed_users", {"user": user})
        self.number.save()
        frappe.set_user(user)
        thread = conversations.thread(self.number.name, "+971509876543")
        self.assertTrue(next(m for m in thread["messages"] if m.name == result["message_id"]).attachment)
        frappe.set_user("Administrator")
        frappe.delete_doc("File", file.name)

    def test_signed_webhook_worker_can_run_as_guest(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "2001",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": self.number.meta_phone_number_id},
                                "messages": [
                                    {
                                        "id": "guest-worker-pdf",
                                        "from": "971509876543",
                                        "timestamp": "1793188800",
                                        "type": "document",
                                        "document": {"id": "12345"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        event = frappe.get_doc(
            {
                "doctype": "WhatsApp Webhook Event",
                "meta_app": self.app.name,
                "event_key": "guest-worker",
                "payload": json.dumps(payload),
                "received_at": now_datetime(),
                "processing_status": "Queued",
            }
        ).insert()
        pdf = self.pdf()
        frappe.set_user("Guest")
        with (
            patch(
                "whatsit.services.meta_client.MetaClient.request",
                return_value={
                    "url": "https://lookaside.fbsbx.com/media",
                    "mime_type": "application/pdf",
                    "file_size": len(pdf),
                },
            ),
            patch("whatsit.services.meta_client.MetaClient.download", return_value=pdf),
        ):
            webhook_processor.process(event.name)
        self.assertEqual(
            frappe.db.get_value("WhatsApp Webhook Event", event.name, "processing_status"), "Processed"
        )
        file_name = frappe.db.get_value(
            "WhatsApp Message", {"meta_message_id": "guest-worker-pdf"}, "media_file"
        )
        self.assertTrue(file_name)
        self.assertFalse(frappe.get_doc("File", file_name).has_permission("read"))
