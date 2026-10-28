import frappe
from frappe.model.document import Document


class WhatsAppMessage(Document):
    def validate(self):
        if not self.flags.internal_update:
            frappe.throw("Messages are created and updated through the messaging service.")
