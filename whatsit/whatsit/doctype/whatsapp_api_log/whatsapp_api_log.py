import frappe
from frappe.model.document import Document
from frappe.utils import cint


class WhatsAppAPILog(Document):
    def validate(self):
        status = cint(self.http_status)
        if status and not 100 <= status <= 599:
            frappe.throw("HTTP Status must be between 100 and 599.")
