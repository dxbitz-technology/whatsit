frappe.ui.form.on('WhatsApp Template', {
    refresh(frm) {
        if (frm.is_new() || !frappe.user.has_role('WhatsApp Manager')) return;
        frm.add_custom_button(__('Sync Status'), async () => {
            const sender = frm.doc.sender;
            if (!sender) return frappe.msgprint(__('Select a sender first.'));
            const result = await whatsit.call('api.templates.sync', {from_number: sender});
            if (result.skipped) frappe.msgprint(__('Skipped {0} unsupported templates. See API Logs.', [result.skipped]));
            frm.reload_doc();
        });
        frm.add_custom_button(__('Send Test Message'), () => whatsit.open_compose({sender: frm.doc.sender}));
        if (!frm.doc.meta_template_id) frm.add_custom_button(__('Submit to Meta'), async () => {
            if (frm.is_dirty()) await frm.save();
            await whatsit.call('api.templates.submit', {template: frm.doc.name, from_number: frm.doc.sender}); frm.reload_doc();
        });
        else {
            const editable = ['category','header_type','header_text','header_sample_handle','body','footer','variables','buttons'];
            editable.concat(['template_name','language','waba_id']).forEach(key => frm.set_df_property(key, 'read_only', 1));
            frm.add_custom_button(__('Edit at Meta'), () => {
                const layout = [
                    {fieldname: 'editor_header', fieldtype: 'Section Break', label: __('Template')},
                    'category', 'header_type',
                    {fieldname: 'editor_header_column', fieldtype: 'Column Break'},
                    'header_text', 'header_sample_handle',
                    {fieldname: 'editor_message', fieldtype: 'Section Break', label: __('Message')},
                    'body', 'footer',
                    {fieldname: 'editor_variables', fieldtype: 'Section Break', label: __('Variables')},
                    'variables',
                    {fieldname: 'editor_buttons', fieldtype: 'Section Break', label: __('Buttons')},
                    'buttons',
                ];
                const fields = layout.map(key => {
                    if (typeof key !== 'string') return key;
                    const field = {...frappe.meta.get_docfield(frm.doctype, key, frm.doc.name), read_only: 0};
                    if (field.fieldtype === 'Table') {
                        delete field.default;
                        field.fields = frappe.meta.get_docfields(field.options).map(df => ({...df}));
                        field.data = JSON.parse(JSON.stringify(frm.doc[key] || []));
                    } else field.default = frm.doc[key];
                    return field;
                });
                const dialog = new frappe.ui.Dialog({title: __('Edit Template at Meta'), size: 'large', fields,
                    primary_action_label: __('Submit Changes'), primary_action: async values => {
                        // Empty tables are omitted by get_values; include intentional deletions.
                        for (const key of ['variables', 'buttons']) values[key] = dialog.fields_dict[key].get_value();
                        await whatsit.call('api.templates.edit', {template: frm.doc.name, changes: values, from_number: frm.doc.sender}); dialog.hide(); frm.reload_doc();
                    }});
                dialog.show();
            });
        }
    },
    async sender(frm) {
        if (frm.doc.sender && !frm.doc.meta_template_id) {
            const result = await frappe.db.get_value('WhatsApp Number', frm.doc.sender, 'waba_id');
            frm.set_value('waba_id', result.message.waba_id);
        }
    }
});
