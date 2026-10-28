from frappe.model.document import Document
from whatsit.services.template_service import validate_template


class WhatsAppTemplate(Document):
    def validate(self):
        validate_template(self)
