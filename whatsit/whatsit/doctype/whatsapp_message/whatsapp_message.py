import frappe
from frappe.model.document import Document


class WhatsAppMessage(Document):
    def validate(self):
        if not self.flags.internal_update:
            frappe.throw("Messages are created and updated through the messaging service.")

        from whatsit.services.form_validation import validate_message_fields

        validate_message_fields(self)
