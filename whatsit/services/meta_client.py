import re
from urllib.parse import urlparse
import requests
import frappe
from frappe.utils import now_datetime

TRANSIENT_CODES = {1, 2, 4, 17, 32, 613, 130429, 131000, 131016, 131048, 131056}


class MetaError(Exception):
    def __init__(self, code, transient=False, ambiguous=False):
        self.code = str(code)
        self.transient = transient
        self.ambiguous = ambiguous
        super().__init__("Meta request failed (" + self.code + ").")


def record_log(operation, sender=None, message=None, http_status=None, response=None, error=None):
    # Do not store headers, tokens, message text, phone numbers or media URLs.
    response = response or {}
    summary = {k: response[k] for k in ("id", "success") if k in response}
    if response.get("error"):
        summary["error_code"] = response["error"].get("code")
        summary["error_subcode"] = response["error"].get("error_subcode")
    frappe.get_doc(
        {
            "doctype": "WhatsApp API Log",
            "operation": operation[:140],
            "sender": sender,
            "message": message,
            "http_status": http_status,
            "response_summary": frappe.as_json(summary),
            "error": str(error or "")[:300],
            "created_at": now_datetime(),
        }
    ).insert(ignore_permissions=True)


class MetaClient:
    def __init__(self, sender, message=None):
        self.sender = sender
        self.message = message
        self.token = sender.get_password("access_token")
        self.base = f"https://graph.facebook.com/{sender.api_version}/"

    def request(self, method, path, **kwargs):
        if not re.fullmatch(r"[0-9]+(?:/[a-z_]+)?", path):
            frappe.throw("Invalid Meta endpoint.")
        operation = f"{method} {path.split('/')[-1] if '/' in path else 'identity'}"
        try:
            response = requests.request(
                method,
                self.base + path,
                headers={"Authorization": "Bearer " + self.token},
                timeout=(10, 45),
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            ambiguous = (
                method == "POST"
                and path.endswith("/messages")
                and not isinstance(exc, requests.ConnectTimeout)
            )
            record_log(
                operation,
                self.sender.name,
                self.message,
                error="Network error; delivery uncertain" if ambiguous else "Network error",
            )
            raise MetaError("NETWORK", transient=not ambiguous, ambiguous=ambiguous) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        record_log(operation, self.sender.name, self.message, response.status_code, data)
        if not response.ok or data.get("error") or 300 <= response.status_code < 400:
            err = data.get("error") or {}
            raise MetaError(
                err.get("code", response.status_code),
                response.status_code in (429, 500, 502, 503, 504)
                or err.get("code") in TRANSIENT_CODES
                or bool(err.get("is_transient")),
            )
        return data

    def download(self, url, max_bytes):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ("lookaside.fbsbx.com", "graph.facebook.com"):
            raise MetaError("MEDIA_HOST")
        try:
            with requests.get(
                url,
                headers={"Authorization": "Bearer " + self.token},
                timeout=(10, 45),
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise MetaError(response.status_code, response.status_code in (429, 500, 502, 503, 504))
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise MetaError("MEDIA_TOO_LARGE")
                return bytes(content)
        except requests.RequestException:
            raise MetaError("MEDIA_NETWORK", transient=True) from None
