"""Validation shared by forms and server-side document operations."""

import json
import re
from urllib.parse import urlsplit

import frappe
from frappe.utils import cint


def clean_text(doc, *fields):
    for field in fields:
        value = doc.get(field)
        if isinstance(value, str):
            doc.set(field, value.strip())


def numeric_id(doc, field):
    clean_text(doc, field)
    value = doc.get(field)
    if value and not re.fullmatch(r"[0-9]{1,32}", str(value)):
        frappe.throw(f"{doc.meta.get_label(field)} must contain digits only, up to 32 digits.")


def linked_pair(doc, type_field, name_field):
    if bool(doc.get(type_field)) != bool(doc.get(name_field)):
        frappe.throw(f"Select both {doc.meta.get_label(type_field)} and {doc.meta.get_label(name_field)}.")


def json_object(doc, field):
    value = doc.get(field)
    if value in (None, ""):
        return
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        frappe.throw(f"{doc.meta.get_label(field)} must contain valid JSON.")
    if not isinstance(parsed, dict):
        frappe.throw(f"{doc.meta.get_label(field)} must be a JSON object.")


def non_negative(doc, *fields):
    for field in fields:
        if cint(doc.get(field)) < 0:
            frappe.throw(f"{doc.meta.get_label(field)} cannot be negative.")


def validate_variable(row):
    clean_text(row, "parameter_name")
    if cint(row.parameter_index) < 1:
        frappe.throw("Variable Position must be 1 or greater.")
    if row.parameter_name and not re.fullmatch(r"[a-z_][a-z0-9_]*", row.parameter_name):
        frappe.throw("Variable Name must use lowercase letters, numbers and underscores.")
    if row.component == "BUTTON" and cint(row.button_index) < 0:
        frappe.throw("Button Position cannot be negative.")
    if not str(row.sample_value or "").strip():
        frappe.throw("Enter a sample for each variable.")


def validate_button(row, require_sample=True):
    from whatsit.services.number_service import normalize_phone

    clean_text(row, "text", "url", "phone_number", "sample_value")
    if not row.text or len(row.text) > 25:
        frappe.throw("Button Text must contain 1 to 25 characters.")
    if row.type == "URL":
        value = row.url or ""
        try:
            url = urlsplit(value)
            valid = url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in value):
            frappe.throw("Enter a valid HTTPS button URL.")
        if require_sample and "{{1}}" in value and not row.sample_value:
            frappe.throw("Enter a sample for the URL button.")
    elif row.type == "PHONE_NUMBER":
        row.phone_number = normalize_phone(row.phone_number)


def validate_template_fields(doc):
    clean_text(doc, "template_name", "language", "waba_id", "header_sample_handle")
    numeric_id(doc, "waba_id")
    if not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", doc.language or ""):
        frappe.throw("Use a language code such as en or en_US.")
    if doc.header_type == "TEXT" and not (doc.header_text or "").strip():
        frappe.throw("Enter Header Text for a text header.")
    if not (doc.body or "").strip():
        frappe.throw("Enter the template Body.")
    seen = set()
    for row in doc.variables:
        validate_variable(row)
        key = (
            row.component,
            cint(row.button_index) if row.component == "BUTTON" else 0,
            cint(row.parameter_index),
        )
        if key in seen:
            frappe.throw("Each variable position can appear only once in its section.")
        seen.add(key)
    if len(doc.buttons) > 10:
        frappe.throw("A template supports up to 10 buttons.")
    for row in doc.buttons:
        validate_button(row, require_sample=not doc.flags.meta_sync)
    for row in doc.variables:
        if row.component == "BUTTON":
            index = cint(row.button_index)
            if index >= len(doc.buttons) or doc.buttons[index].type != "URL":
                frappe.throw("Button variables must refer to an existing URL button.")


def validate_message_fields(doc):
    from whatsit.services.number_service import normalize_phone

    doc.remote_number = normalize_phone(doc.remote_number)
    linked_pair(doc, "party_type", "party")
    linked_pair(doc, "reference_doctype", "reference_name")
    non_negative(doc, "retry_count")
    json_object(doc, "template_parameters_json")
    json_object(doc, "request_payload")
