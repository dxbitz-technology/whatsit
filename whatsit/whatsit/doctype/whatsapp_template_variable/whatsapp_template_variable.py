from frappe.model.document import Document
from whatsit.services.form_validation import validate_variable


class WhatsAppTemplateVariable(Document):
    def validate(self):
        validate_variable(self)
