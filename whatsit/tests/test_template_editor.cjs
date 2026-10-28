const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function openEditor() {
    let handlers, dialog, submitted;
    const buttons = {};
    const doc = {
        name: 'template-1', sender: 'sender-1', meta_template_id: '3001',
        category: 'UTILITY', header_type: 'NONE', body: 'Hello {{1}}',
        variables: [{component: 'BODY', parameter_index: 1, sample_value: 'Ahmed'}],
        buttons: [{type: 'QUICK_REPLY', text: 'Yes'}, {type: 'QUICK_REPLY', text: 'No'}],
    };
    const context = {
        __: value => value,
        whatsit: {call: async (method, args) => {submitted = {method, args};}},
        frappe: {
            user: {has_role: () => true},
            meta: {get_docfield: (doctype, key) => ({fieldname: key, fieldtype: ['variables', 'buttons'].includes(key) ? 'Table' : 'Data'})},
            ui: {
                form: {on: (doctype, value) => {handlers = value;}},
                Dialog: class {
                    constructor(options) {
                        Object.assign(this, options);
                        this.fields_dict = Object.fromEntries(options.fields.map(field => [field.fieldname, {
                            // Dialog tables use data, not a scalar default.
                            get_value: () => field.fieldtype === 'Table' ? (field.data || []) : field.default,
                        }]));
                        dialog = this;
                    }
                    show() {}
                    hide() {}
                },
            },
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../whatsit/doctype/whatsapp_template/whatsapp_template.js'), 'utf8'), context);
    handlers.refresh({
        doc, doctype: 'WhatsApp Template', is_new: () => false,
        set_df_property() {}, reload_doc() {},
        add_custom_button: (label, callback) => {buttons[label] = callback;},
    });
    buttons['Edit at Meta']();
    return {doc, dialog, getSubmission: () => submitted};
}

test('existing rows are shown and edits do not mutate the open form', () => {
    const {doc, dialog} = openEditor();
    const variables = dialog.fields_dict.variables.get_value();
    const buttons = dialog.fields_dict.buttons.get_value();
    assert.equal(variables.length, 1);
    assert.equal(buttons.length, 2);
    variables[0].sample_value = 'Changed';
    buttons.pop();
    assert.equal(doc.variables[0].sample_value, 'Ahmed');
    assert.equal(doc.buttons.length, 2);
});

test('adding a button submits the complete existing set', async () => {
    const {dialog, getSubmission} = openEditor();
    dialog.fields_dict.buttons.get_value().push({type: 'QUICK_REPLY', text: 'Later'});
    await dialog.primary_action({body: 'Hello {{1}}'});
    const {method, args} = getSubmission();
    assert.equal(method, 'api.templates.edit');
    assert.equal(args.changes.buttons.map(row => row.text).join(','), 'Yes,No,Later');
    assert.equal(args.changes.variables[0].sample_value, 'Ahmed');
});

test('intentional removal submits empty tables even when dialog values omit them', async () => {
    const {dialog, getSubmission} = openEditor();
    dialog.fields_dict.buttons.get_value().length = 0;
    dialog.fields_dict.variables.get_value().length = 0;
    await dialog.primary_action({body: 'Hello'});
    assert.equal(getSubmission().args.changes.buttons.length, 0);
    assert.equal(getSubmission().args.changes.variables.length, 0);
});
