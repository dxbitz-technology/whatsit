frappe.pages['whatsapp-compose'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __('WhatsApp Compose'), single_column: true});
    new whatsit.Composer($('<div class="wa-compose-page">').appendTo(page.main));
};
