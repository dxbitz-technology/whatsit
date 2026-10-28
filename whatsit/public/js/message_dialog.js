frappe.provide('whatsit');

whatsit.call = async (method, args = {}) => (await frappe.call({method: 'whatsit.' + method, args})).message;
whatsit.escape = value => frappe.utils.escape_html(String(value || ''));

whatsit.Composer = class {
    constructor(parent, options = {}) {
        this.options = options;
        this.parent = $(parent).empty().addClass('wa-composer');
        this.fields = {};
        this.templates = [];
        this.recipient = null;
        this.request_key = null;
        this.ready = this.setup();
    }
    control(df) {
        const holder = $('<div>').appendTo(this.form);
        const control = frappe.ui.form.make_control({parent: holder, df, render_input: true});
        this.fields[df.fieldname] = control;
        return control;
    }
    value(name) { return this.fields[name]?.get_value(); }
    async setup() {
        this.form = $('<div class="wa-compose-fields">').appendTo(this.parent);
        this.control({fieldname: 'sender', label: __('From'), fieldtype: 'Select', reqd: 1,
            change: () => this.load_templates()});
        this.control({fieldname: 'party_type', label: __('Party Type'), fieldtype: 'Autocomplete', options: [],
            change: () => this.party_type_changed()});
        this.control({fieldname: 'party', label: __('Party'), fieldtype: 'Dynamic Link', options: 'party_type',
            get_options: () => this.value('party_type'), change: () => this.party_changed()});
        this.fields.party.get_options = () => this.value('party_type');
        this.control({fieldname: 'to', label: __('To'), fieldtype: 'Autocomplete', options: [], reqd: 1});
        this.control({fieldname: 'mode', label: __('Message Type'), fieldtype: 'Select', options: ['Text', 'Template', 'Image', 'PDF'], default: 'Text',
            change: () => this.mode_changed()});
        this.control({fieldname: 'template', label: __('Template'), fieldtype: 'Select', options: [],
            change: () => this.template_changed()});
        this.variables = $('<div class="wa-template-values">').appendTo(this.parent);
        this.message = this.control({fieldname: 'text', label: __('Message'), fieldtype: 'Small Text', change: () => this.preview()});
        this.fileRow = $('<div class="wa-attachment">').appendTo(this.parent);
        this.fileLabel = $('<span>').appendTo(this.fileRow);
        $('<button type="button" class="btn btn-default btn-sm">').text(__('Upload Attachment')).appendTo(this.fileRow).on('click', () => this.upload());
        this.previewBox = $('<div class="wa-preview">').appendTo(this.parent);
        this.sendButton = $('<button type="button" class="btn btn-primary wa-send">').text(__('Send')).appendTo(this.parent).on('click', () => this.send());
        const [senders, types] = await Promise.all([whatsit.call('api.conversations.senders'), whatsit.call('api.contacts.get_party_types')]);
        this.senders = senders;
        this.fields.sender.df.options = [{value: '', label: ''}, ...senders.map(s => ({value: s.name, label: s.title}))];
        this.fields.sender.refresh();
        this.fields.party_type.set_data(types);
        await this.fields.sender.set_value(this.options.sender || senders.find(s => s.is_default)?.name || '');
        if (this.options.party_type) await this.fields.party_type.set_value(this.options.party_type);
        if (this.options.party) await this.fields.party.set_value(this.options.party);
        if (this.options.to) await this.fields.to.set_value(this.options.to);
        if (this.options.lock_recipient) {
            ['sender', 'to', 'party_type', 'party'].forEach(k => {this.fields[k].df.read_only = 1; this.fields[k].refresh();});
        }
        await this.fields.mode.set_value(this.options.reply_open === false ? 'Template' : 'Text');
        this.mode_changed();
    }
    async party_type_changed() {
        await this.fields.party.set_value('');
        this.fields.party.refresh();
        await this.fields.to.set_value('');
        this.fields.to.set_data([]);
    }
    async party_changed() {
        const type = this.value('party_type'), party = this.value('party');
        await this.fields.to.set_value('');
        this.fields.to.set_data([]);
        if (!type || !party) return;
        const options = await whatsit.call('api.contacts.get_numbers', {party_type: type, party});
        if (this.value('party') !== party || this.value('party_type') !== type) return;
        this.fields.to.set_data(options.map(x => ({value: x.number, label: whatsit.escape(x.number), description: whatsit.escape(x.label)})));
        // Selection stays explicit when several phone numbers are available.
        if (options.length === 1) await this.fields.to.set_value(options[0].number);
    }
    async load_templates() {
        const sender = this.value('sender');
        this.templates = sender ? await whatsit.call('api.templates.approved', {from_number: sender}) : [];
        if (sender !== this.value('sender')) return;
        this.fields.template.df.options = [{value: '', label: ''}, ...this.templates.map(t => ({value: t.name, label: `${t.template_name} (${t.language})`}))];
        this.fields.template.refresh();
        await this.fields.template.set_value('');
        this.template_changed();
    }
    mode_changed() {
        const mode = this.value('mode') || 'Text';
        this.fields.template.$wrapper.toggle(mode === 'Template');
        this.fields.text.$wrapper.toggle(mode !== 'Template');
        this.variables.toggle(mode === 'Template');
        this.fileRow.toggle(['Image', 'PDF'].includes(mode) || (mode === 'Template' && ['IMAGE','DOCUMENT'].includes(this.template_details?.header_type)));
        this.preview();
    }
    async template_changed() {
        this.variables.empty(); this.variable_controls = {};
        this.template_details = null;
        if (this.value('template') && this.value('sender')) {
            const selected = this.value('template');
            const details = await whatsit.call('api.templates.details', {template: selected, from_number: this.value('sender')});
            if (selected !== this.value('template')) return;
            this.template_details = details;
            for (const row of details.variables) {
                this.variable_controls[row.key] = frappe.ui.form.make_control({parent: this.variables,
                    df: {fieldname: row.key.replaceAll('.', '_'), label: row.key.startsWith('button.') ? __('Button {0} Value {1}', [Number(row.key.split('.')[1]) + 1, row.key.split('.')[2]]) : row.key.replace('body.', 'Value ').replace('header.', 'Header '), fieldtype: 'Data', reqd: 1, change: () => this.preview()}, render_input: true});
            }
        }
        this.mode_changed();
    }
    upload() {
        new frappe.ui.FileUploader({allow_multiple: false, restrictions: {allowed_file_types: ['.jpg', '.jpeg', '.png', '.pdf']},
            make_attachments_public: false,
            on_success: file => {this.file = file; this.fileLabel.text(file.file_name);}});
    }
    preview() {
        let text = this.value('text') || '';
        if (this.value('mode') === 'Template') {
            text = this.template_details?.body || '';
            for (const [key, control] of Object.entries(this.variable_controls || {})) {
                if (key.startsWith('body.')) text = text.split('{{' + key.slice(5) + '}}').join(control.get_value() || '{{' + key.slice(5) + '}}');
            }
        }
        this.previewBox.text(text).toggle(Boolean(text));
    }
    async send() {
        if (this.sending) return;
        const mode = this.value('mode');
        const args = {from_number: this.value('sender'), to: this.value('to'), party_type: this.value('party_type') || null, party: this.value('party') || null,
            reference_doctype: this.options.reference_doctype || null, reference_name: this.options.reference_name || null};
        if (!args.from_number || !args.to) return frappe.msgprint(__('Select a sender and recipient.'));
        let method;
        if (mode === 'Template') {
            method = 'send_template'; args.template = this.value('template');
            if (!args.template) return frappe.msgprint(__('Select a template.'));
            args.variables = Object.fromEntries(Object.entries(this.variable_controls || {}).map(([k, c]) => [k, c.get_value()]));
            args.media_file = this.file?.name;
        } else if (mode === 'Text') {
            method = 'send_text'; args.text = this.value('text');
        } else {
            method = 'send_media'; args.file_name = this.file?.name; args.caption = this.value('text');
            if (!args.file_name) return frappe.msgprint(__('Upload an attachment.'));
            const filename = this.file.file_name.toLowerCase();
            if ((mode === 'PDF' && !filename.endsWith('.pdf')) || (mode === 'Image' && !/\.(jpe?g|png)$/.test(filename))) return frappe.msgprint(__('Choose an attachment matching the message type.'));
        }
        const signature = JSON.stringify(args);
        if (signature !== this.request_signature) {this.request_key = frappe.utils.get_random(32); this.request_signature = signature;}
        args.idempotency_key = this.request_key;
        this.sending = true; this.sendButton.prop('disabled', true);
        try {
            const result = await whatsit.call('api.messages.' + method, args);
            frappe.show_alert({message: __('Message queued'), indicator: 'green'});
            this.request_key = this.request_signature = null;
            this.options.on_send?.(result);
            await this.fields.text.set_value('');
            this.file = null; this.fileLabel.empty();
        } finally {this.sending = false; this.sendButton.prop('disabled', false);}
    }
};

whatsit.open_compose = options => {
    const dialog = new frappe.ui.Dialog({title: __('Send WhatsApp Message'), size: 'large', fields: [{fieldname: 'compose', fieldtype: 'HTML'}]});
    new whatsit.Composer(dialog.fields_dict.compose.$wrapper, {...options, on_send: result => {dialog.hide(); options?.on_send?.(result);}});
    dialog.show();
};
