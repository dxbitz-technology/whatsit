import secrets
import frappe
from frappe.model.document import Document
from frappe.utils import get_url


class WhatsAppMetaApp(Document):
    def before_insert(self):
        self.route_key = secrets.token_urlsafe(32)

    def validate(self):
        from whatsit.services.form_validation import clean_text, numeric_id

        clean_text(self, "display_name")
        numeric_id(self, "meta_app_id")
        if not str(self.meta_app_id).isdigit():
            frappe.throw("Meta App ID must contain digits only.")
        if not self.is_new():
            self.route_key = self.get_doc_before_save().route_key
        self.webhook_url = get_url("/api/method/whatsit.api.webhook.receive?key=" + self.route_key)
