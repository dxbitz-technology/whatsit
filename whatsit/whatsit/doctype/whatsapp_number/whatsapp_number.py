from frappe.model.document import Document
from whatsit.services.number_service import validate_number


class WhatsAppNumber(Document):
    def validate(self):
        validate_number(self)
