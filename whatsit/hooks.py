app_name = "whatsit"
app_title = "Whatsit"
app_publisher = "DXBITZ Technology"
app_description = "WhatsApp messaging and inbox for Frappe"
app_email = "info@dxbitz.com"
app_license = "AGPL-3.0-or-later"
required_apps = ["frappe"]
before_install = "whatsit.install.ensure_roles"
before_migrate = "whatsit.install.ensure_roles"
after_install = "whatsit.install.after_migrate"
after_migrate = "whatsit.install.after_migrate"
app_include_css = ["/assets/whatsit/css/whatsapp.css"]
app_include_js = ["/assets/whatsit/js/message_dialog.js"]
scheduler_events = {
    "hourly": ["whatsit.jobs.sync.templates"],
    "daily": ["whatsit.jobs.cleanup.run"],
    "cron": {"*/5 * * * *": ["whatsit.jobs.retry.recover"]},
}
permission_query_conditions = {
    "WhatsApp Number": "whatsit.permissions.number_query",
    "WhatsApp Message": "whatsit.permissions.message_query",
    "WhatsApp Conversation": "whatsit.permissions.conversation_query",
    "WhatsApp Template": "whatsit.permissions.template_query",
}
has_permission = {
    "WhatsApp Number": "whatsit.permissions.number_permission",
    "WhatsApp Message": "whatsit.permissions.sender_permission",
    "WhatsApp Conversation": "whatsit.permissions.sender_permission",
    "WhatsApp Template": "whatsit.permissions.template_permission",
}
