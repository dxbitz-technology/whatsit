frappe.pages['whatsapp-inbox'].on_page_load = function(wrapper) {
    wrapper.inbox = new WhatsAppInbox(wrapper);
};
frappe.pages['whatsapp-inbox'].on_page_show = function(wrapper) { wrapper.inbox?.refresh(); };

class WhatsAppInbox {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({parent: wrapper, title: __('WhatsApp Inbox'), single_column: true});
        this.wrapper = wrapper;
        this.tab = 'Conversations'; this.start = 0; this.items = [];
        this.page.set_primary_action(__('Compose'), () => whatsit.open_compose({on_send: () => this.refresh()}), 'add');
        this.page.add_menu_item(__('Numbers'), () => frappe.set_route('List', 'WhatsApp Number'));
        this.page.add_menu_item(__('Templates'), () => frappe.set_route('List', 'WhatsApp Template'));
        this.sender = this.page.add_field({fieldname: 'sender', label: __('Sender'), fieldtype: 'Select', options: [{value: '', label: __('All Senders')}], change: () => {this.selected = null; this.refresh();}});
        this.search = this.page.add_field({fieldname: 'search', label: __('Search'), fieldtype: 'Data', change: frappe.utils.debounce(() => this.refresh(), 300)});
        const shell = $('<div class="wa-inbox">').appendTo(this.page.main);
        this.sidebar = $('<aside class="wa-sidebar">').appendTo(shell);
        const tabs = $('<div class="wa-tabs" role="tablist">').appendTo(this.sidebar);
        this.tabButtons = {};
        for (const tab of ['Conversations', 'Outbox']) {
            this.tabButtons[tab] = $('<button type="button" role="tab">').text(__(tab)).appendTo(tabs).on('click', () => {this.tab = tab; this.selected = null; this.refresh();});
        }
        this.list = $('<div class="wa-thread-list">').appendTo(this.sidebar);
        this.detail = $('<section class="wa-detail">').appendTo(shell);
        this.empty();
        this.ready = this.setup();
        this.timer = setInterval(() => {
            if ($(wrapper).is(':visible') && !document.hidden) this.refresh(true);
        }, 10000);
    }
    async setup() {
        const senders = await whatsit.call('api.conversations.senders');
        this.sender.df.options = [{value: '', label: __('All Senders')}, ...senders.map(s => ({value: s.name, label: s.title}))];
        this.sender.refresh();
        await this.refresh();
    }
    empty() {this.detail.empty().append($('<div class="wa-empty">').append($('<h3>').text(__('Your WhatsApp Inbox'))).append($('<p>').text(__('Choose a conversation or compose a message.'))));}
    async refresh(quiet = false) {
        const token = this.refreshToken = (this.refreshToken || 0) + 1;
        const rows = await whatsit.call('api.conversations.list_items', {tab: this.tab, sender: this.sender.get_value(), search: this.search.get_value()});
        if (token !== this.refreshToken) return;
        this.items = rows; this.start = rows.length;
        this.render_list();
        if (this.selected) await this.open(this.selected, quiet);
        else this.empty();
    }
    render_list() {
        this.list.empty();
        Object.entries(this.tabButtons).forEach(([key, b]) => b.toggleClass('active', key === this.tab).attr('aria-selected', String(key === this.tab)));
        if (!this.items.length) $('<div class="wa-list-empty">').text(this.tab === 'Outbox' ? __('No outbound messages waiting for a reply.') : __('No conversations yet.')).appendTo(this.list);
        for (const row of this.items) {
            const item = $('<button type="button" class="wa-thread">').toggleClass('selected', this.selected?.name === row.name).appendTo(this.list);
            $('<span class="wa-avatar">').text((row.party || row.remote_number).slice(-2)).appendTo(item);
            const content = $('<span class="wa-thread-summary">').appendTo(item);
            $('<strong>').text(row.party || row.remote_number).appendTo(content);
            $('<small>').text(row.last_message_preview || row.message_type || '').appendTo(content);
            const meta = $('<span class="wa-thread-meta">').appendTo(item);
            $('<time>').html(frappe.datetime.comment_when(row.last_message_at, true)).appendTo(meta);
            if (row.unread_count) $('<span class="wa-unread">').text(row.unread_count).appendTo(meta);
            else $('<small>').text(__(row.status || '')).appendTo(meta);
            item.on('click', () => {this.selected = row; this.render_list(); this.open(row);});
        }
        if (this.items.length && this.items.length % 50 === 0) $('<button class="btn btn-default btn-sm wa-more">').text(__('Load More')).appendTo(this.list).on('click', async () => {
            const more = await whatsit.call('api.conversations.list_items', {tab: this.tab, sender: this.sender.get_value(), search: this.search.get_value(), start: this.items.length});
            this.items.push(...more); this.render_list();
        });
    }
    async open(row, quiet = false) {
        const data = await whatsit.call('api.conversations.thread', {sender: row.sender, remote_number: row.remote_number});
        if (this.selected?.name !== row.name) return;
        const signature = JSON.stringify(data);
        if (quiet && this.threadSignature === signature) return;
        this.threadSignature = signature;
        if (quiet && this.composerOpen) return;
        this.detail.empty();
        const header = $('<div class="wa-thread-header">').appendTo(this.detail);
        const heading = $('<div>').appendTo(header);
        $('<h4>').text(row.party || row.remote_number).appendTo(heading);
        $('<small>').text(row.remote_number + ' · ' + row.sender).appendTo(heading);
        if (data.conversation) {
            $('<button class="btn btn-default btn-sm">').text(data.conversation.status === 'Closed' ? __('Reopen') : __('Close')).appendTo(header).on('click', async () => {
                await whatsit.call('api.conversations.set_status', {conversation: data.conversation.name, status: data.conversation.status === 'Closed' ? 'Open' : 'Closed'}); this.open(row);
            });
            await whatsit.call('api.conversations.mark_read', {conversation: data.conversation.name});
        }
        const messages = $('<div class="wa-messages" aria-live="polite">').appendTo(this.detail);
        const render = rows => rows.forEach(m => {
            const bubble = $('<article class="wa-bubble">').addClass(m.direction === 'Outgoing' ? 'outgoing' : 'incoming');
            if (m.attachment) {
                if (m.message_type === 'Image') $('<a target="_blank" rel="noopener">').attr('href', m.attachment.url).append($('<img loading="lazy">').attr('src', m.attachment.url).attr('alt', m.attachment.file_name)).appendTo(bubble);
                else $('<a target="_blank" rel="noopener" class="wa-pdf">').attr('href', m.attachment.url).text('PDF · ' + m.attachment.file_name).appendTo(bubble);
            }
            if (m.message_type === 'Template') $('<small class="wa-template-label">').text(__('Template') + ' · ' + (m.template || '')).appendTo(bubble);
            if (m.text) $('<div class="wa-message-text">').text(m.text).appendTo(bubble);
            $('<footer>').text(frappe.datetime.str_to_user(m.received_at || m.creation) + ' · ' + __(m.status)).appendTo(bubble);
            if (m.error_details) $('<small class="text-danger">').text(m.error_details).appendTo(bubble);
            bubble.appendTo(messages);
        });
        if (data.messages.length === 50) {
            let before = data.messages[0].creation;
            $('<button class="btn btn-default btn-sm wa-more">').text(__('Earlier Messages')).appendTo(messages).on('click', async function() {
                const earlier = await whatsit.call('api.conversations.thread', {sender: row.sender, remote_number: row.remote_number, before});
                // Render to a temporary section and move the bubbles before the current history.
                const current = messages.children('.wa-bubble').detach();
                render(earlier.messages); messages.append(current);
                if (earlier.messages.length) before = earlier.messages[0].creation;
                if (earlier.messages.length < 50) $(this).remove();
            });
        }
        render(data.messages);
        messages.scrollTop(messages[0].scrollHeight);
        const footer = $('<div class="wa-reply-bar">').appendTo(this.detail);
        $('<span>').addClass(data.reply_open ? 'wa-window-open' : 'wa-window-closed').text(data.reply_open ? __('Reply window open') : __('Reply window closed. Choose an approved template.')).appendTo(footer);
        $('<button class="btn btn-primary">').text(data.reply_open ? __('Reply') : __('Send Template')).appendTo(footer).on('click', () => whatsit.open_compose({sender: row.sender, to: row.remote_number, lock_recipient: true, reply_open: data.reply_open, on_send: () => this.refresh()}));
    }
}
