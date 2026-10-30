from frappe.model.document import Document
from whatsit.services.form_validation import validate_button


class WhatsAppTemplateButton(Document):
    def validate(self):
        validate_button(self)
