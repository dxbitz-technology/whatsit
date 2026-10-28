import frappe
from whatsit.services.template_service import sync
from whatsit.services.meta_client import MetaError, record_log


def templates():
    seen = set()
    for row in frappe.get_all(
        "WhatsApp Number", filters={"enabled": 1}, fields=["name", "waba_id", "meta_app"]
    ):
        if row.waba_id in seen or not frappe.db.get_value("WhatsApp Meta App", row.meta_app, "enabled"):
            continue
        seen.add(row.waba_id)
        try:
            sync(frappe.get_doc("WhatsApp Number", row.name))
        except MetaError as exc:
            record_log("template_sync", row.name, error=str(exc))
