frappe.ui.form.on('WhatsApp Webhook Event', {refresh(frm) {
    if (!frm.is_new() && frm.doc.payload && frappe.user.has_role('WhatsApp Manager')) frm.add_custom_button(__('Replay'), async () => {await whatsit.call('api.webhook.replay', {event: frm.doc.name}); frm.reload_doc();});
}});
