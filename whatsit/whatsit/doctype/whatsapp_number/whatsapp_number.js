frappe.ui.form.on('WhatsApp Number', {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__('Open Inbox'), () => frappe.set_route('whatsapp-inbox'));
        if (frappe.user.has_role('WhatsApp Manager')) {
            frm.add_custom_button(__('Test Connection'), async () => {
                const result = await whatsit.call('services.number_service.test_connection', {name: frm.doc.name});
                frappe.msgprint(result.status === 'Connected' ? __('Connected') : result.error); frm.reload_doc();
            });
            frm.add_custom_button(__('Sync Templates'), async () => {
                const r = await whatsit.call('api.templates.sync', {from_number: frm.doc.name});
                frappe.msgprint(__('Synced {0} templates.', [r.count]) + (r.skipped ? ' ' + __('Skipped {0} unsupported templates. See API Logs.', [r.skipped]) : ''));
            });
            frm.add_custom_button(__('Send Test Message'), () => whatsit.open_compose({sender: frm.doc.name}));
        }
    }
});
