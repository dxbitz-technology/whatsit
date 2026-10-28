import frappe
from frappe.model.document import Document


class WhatsAppConversation(Document):
    def validate(self):
        if not self.flags.internal_update:
            old = self.get_doc_before_save()
            if not old or any(
                self.get(f.fieldname) != old.get(f.fieldname)
                for f in self.meta.fields
                if f.fieldname != "status"
            ):
                frappe.throw("Conversation details are managed by the inbox.")
