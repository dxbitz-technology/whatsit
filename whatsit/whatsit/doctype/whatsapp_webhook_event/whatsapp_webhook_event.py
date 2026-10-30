from frappe.model.document import Document
from whatsit.services.form_validation import json_object, non_negative


class WhatsAppWebhookEvent(Document):
    def validate(self):
        json_object(self, "payload")
        non_negative(self, "attempt_count")
