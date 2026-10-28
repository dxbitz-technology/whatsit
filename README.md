# Whatsit

WhatsApp messaging for Frappe v15 and v16. Connect directly to Meta WhatsApp Cloud API, manage templates, and handle messages in Desk. ERPNext is not required.

## Install

Run these commands from a bench with the matching Frappe version:

```sh
bench get-app https://github.com/dxbitz-technology/whatsit.git
bench --site your-site install-app whatsit
bench --site your-site migrate
bench build --app whatsit
bench --site your-site enable-scheduler
bench restart
```

For development, run `bench start` instead of `bench restart`. A worker and scheduler must be running. Use HTTPS for a public webhook. Keep the site's encryption key in your backups.

To update:

```sh
bench update --apps whatsit
```

To test on a separate test site:

```sh
bench --site test-site set-config allow_tests true
bench --site test-site run-tests --app whatsit
```

Uninstalling removes the app's records. Back up first:

```sh
bench --site your-site backup --with-files
bench --site your-site uninstall-app whatsit
```

## Set up

1. Give the setup user the **WhatsApp Manager** role.
2. Open **WhatsApp Meta App**. Enter the Meta app ID, app secret and a verify token you choose. Save and copy its Webhook URL.
3. Set that URL and verify token in the Meta app's WhatsApp webhook settings. Subscribe the app to the WABA and subscribe to `messages`, `message_template_status_update`, and `message_template_quality_update` where available. Verification time and the last signed receipt appear separately on the app form.
4. Create a **WhatsApp Number** with the registered E.164 number, Meta phone number ID, business account ID, linked Meta app and access token. Select a supported Graph API version. The initial value is `v26.0`; use a version available to your Meta app.
5. Run **Test Connection**. This checks the token and phone identity. It does not verify the webhook subscription or prove that messages can be delivered.
6. Add each operator or API user to **Allowed Users** on the sender. Managers can use all numbers. Set one enabled number as Default Sender if needed. Clear the old default before choosing another.
7. Run **Sync Templates**, or create a draft and **Submit to Meta**. Wait for approval before sending. **Send Test Message** opens Compose for an explicit recipient selection.
8. Open **WhatsApp Inbox** from the WhatsApp workspace.

Use a system user access token with the required WhatsApp messaging and management permissions, access to the WABA, and an appropriate lifetime. Never put the token in a client script. Meta business verification, phone registration, recipient opt-in, template policy, charges and account limits still apply.

## Inbox and compose

The inbox has two main tabs:

- **Conversations** contains every sender and recipient pair that has received a message. It retains the complete history after the reply window closes. Incoming messages reopen a manually closed conversation.
- **Outbox** contains outbound messages for pairs that have no conversation yet. On the first incoming message, the same records appear in the conversation. Nothing is copied.

Filter by sender or search by party, recipient or message. Open a thread to see delivery states, images and private PDFs. Closing a conversation is an operator action and does not change Meta's reply window.

Text and media require an incoming message within the previous 24 hours. Outside that window, use an approved template. The server checks eligibility when queuing and again when dispatching.

Compose supports manual international numbers and permission-checked party selection. Phone choices come from common phone fields and linked Contact phone rows. Values are normalized and deduplicated. Multiple choices require an explicit selection. Local numbers without a country code are omitted. Changing the party clears the recipient.

Attachments must be private Frappe File records containing JPEG, PNG or PDF data. Images support up to 5 MB; PDFs support up to 100 MB. Your site's upload limit may be lower. File content, permissions, MIME type and size are checked. Private files are uploaded to Meta; their URLs are never published to Meta.

Other forms can open the reusable dialog without adding global buttons:

```js
whatsit.open_compose({
    sender: "WA-00001",
    party_type: "Customer",
    party: frm.doc.customer,
    reference_doctype: frm.doctype,
    reference_name: frm.doc.name
});
```

## Sending from another app

```python
from whatsit.api.messages import send_text, send_template, send_media

result = send_template(
    from_number="WA-00001",
    to="+971501234567",
    template="invoice_ready",
    language="en",
    variables={"1": "Ahmed", "2": "INV-001"},
    idempotency_key="invoice-INV-001-notice",
)

# Text and media need an open reply window.
result = send_text(from_number="WA-00001", to="+971501234567", text="Hello")
result = send_media(
    from_number="WA-00001",
    to="+971501234567",
    file_name="FILE-0001",
    caption="Your invoice",
)
```

All send methods accept `party_type`, `party`, `reference_doctype`, `reference_name` and `idempotency_key`. An omitted sender uses the enabled default only. An explicit disabled or unauthorized sender fails. An omitted `to` resolves the party only if exactly one valid phone number exists. Referenced documents must be readable.

`send_template` accepts the internal template ID or a template name plus language within the sender's WABA. `media_file` selects a private File for an image or document header. Positional body values use keys such as `"1"`. Qualified keys such as `"body.1"`, `"header.1"`, or `"button.0.1"` distinguish sections. Imported named parameters use their Meta parameter names.

A send returns:

```json
{"message_id": "internal-id", "status": "Queued", "meta_message_id": null}
```

The record is persisted in the caller's transaction and dispatch starts after commit. In a bench console, commit the transaction to release the queued job. Call `get_status(message_id)` or read the permitted message later for the Meta ID and delivery state. Use the same Request Key to retry the same operation. Reusing a key with different arguments or a different user fails. Each user can queue up to 60 messages per minute; Meta limits also apply.

The same methods are available through Frappe's authenticated HTTP API. Use POST and a Frappe API user with the WhatsApp API User role and Allowed Users access on the sender:

```sh
curl -X POST 'https://your-site/api/method/whatsit.api.messages.send_template' \
  -H 'Authorization: token YOUR_API_KEY:YOUR_API_SECRET' \
  -H 'Content-Type: application/json' \
  --data '{"from_number":"WA-00001","to":"+971501234567","template":"invoice_ready","language":"en","variables":{"1":"Ahmed","2":"INV-001"},"idempotency_key":"invoice-INV-001-notice"}'
```

## Templates

Templates belong to a WABA. Senders on the same WABA share them. Draft creation supports body text, text/image/document headers, footer text, quick replies, URL buttons and phone buttons. Add the required sample values in Variables. For media headers, obtain a resumable-upload sample handle through Meta's upload tooling and enter it in Media Sample Handle. This is different from a message media ID.

Submitted templates cannot be edited locally. **Edit at Meta** submits changes as a Meta lifecycle operation and returns the record to the resulting status. Renaming or changing language requires a new draft. Rejection reasons, quality and status can arrive by webhook or hourly reconciliation. The editor does not promise Meta approval.

Create authentication templates in Meta's WhatsApp Manager, then sync them here. Templates containing unsupported button types are rejected by the send service. Flow, carousel, location and other interactive template formats are outside this MVP. Named parameters can be sent after import; the draft editor uses positional parameters.

## Webhook security and recovery

Every Meta App gets a random 256-bit route key in its callback URL. GET verification compares that app's verify token. POST first resolves the route key, then validates `X-Hub-Signature-256` against the raw body with that app's secret. Only afterward does processing read phone IDs. A phone number must match both the verified app and the WABA in the event. A signature valid for one app cannot update a number belonging to another app.

Signed payloads are stored in manager-only Webhook Events and processed in the background. Exact payload fingerprints deduplicate events; Meta message IDs deduplicate incoming messages. Replaying an event is safe. Delivery receipts do not regress Read to Delivered. Receipts arriving before the dispatch job saves its Meta ID are retried.

Transient request failures use bounded exponential backoff, up to five retries. Validation and permission failures are permanent. A network interruption after a send may mean Meta accepted it. Such a message becomes **Unknown** and is not resent automatically. Review Meta before sending again. Worker claims interrupted for 15 minutes are also marked Unknown. Queue recovery runs every five minutes.

Manager-only API Logs contain operation names, result IDs, HTTP status and error codes. They do not contain tokens, message bodies, recipient numbers or temporary media URLs. Webhook payloads may contain recipient information and message text; payloads expire after seven days. Processed event records and API Logs expire after 30 days. Failed events remain as diagnostics after their payload expires. Messages and their private attachments are retained until your site administrator removes them under the site's record policy.

Only webhook verification and receipt allow guests. Inbox users cannot read app secrets, decrypted access tokens, diagnostic payloads, or conversations and attachments for unassigned senders. Native list queries and document reads apply the same sender rules as the public APIs. Existing Frappe record shares, custom permission rules and administrator access remain under the site's control.

## Records and endpoints

DocTypes:

- WhatsApp Meta App
- WhatsApp Number
- WhatsApp Number User, a child table for sender access
- WhatsApp Template
- WhatsApp Template Variable, a child table
- WhatsApp Template Button, a child table
- WhatsApp Conversation
- WhatsApp Message
- WhatsApp Webhook Event
- WhatsApp API Log

Desk pages: `whatsapp-inbox` and `whatsapp-compose`. Workspace: WhatsApp. Frappe module: Whatsit.

Whitelisted methods, prefixed with `whatsit.`:

| Module | Methods |
| --- | --- |
| `api.messages` | `send_text`, `send_template`, `send_media`, `get_status` |
| `api.contacts` | `get_numbers`, `get_party_types` |
| `api.templates` | `submit`, `edit`, `sync`, `approved`, `details` |
| `api.conversations` | `senders`, `list_items`, `thread`, `mark_read`, `set_status` |
| `api.webhook` | `receive`, `replay` |
| `services.number_service` | `test_connection` |

`submit`, `edit`, `sync`, `replay`, and `test_connection` require WhatsApp Manager. Send, mark-read, close, replay, and template mutations require POST. Read endpoints also accept GET. No class overrides, global document hooks, or global form buttons are installed.

## Validation

The automated suite covers real Frappe document creation and permissions with mocked Meta requests. It checks default selection, shared WABAs, template approval and sync, signed multi-app webhook routing, private attachments, party resolution, conversation grouping, reply windows, idempotency, status ordering and retry behavior. CI runs the suite on clean Frappe v15 and v16 MariaDB sites, without ERPNext.

Local verification: 34 tests passed on both clean Frappe v15 and v16 MariaDB sites without ERPNext. Install and migrate passed on both versions. Inbox and compose pages were checked in browsers on both versions. The v16 conversation view and reply dialog were also checked.

Live Meta delivery has not been verified without credentials. Before use, complete this checklist with your own Meta test number and permitted test recipient:

- Test the phone identity and confirm the configured number matches Meta.
- Verify each app-specific callback and deliver a signed event to each app. Confirm a forged signature fails.
- Create and submit a template, then confirm Pending, Approved and a rejected template reason through sync and webhook events.
- Send an approved template through both Python and HTTP. Confirm the eventual Meta message ID.
- Reply from the recipient, verify prior outbound history appears in Conversations, then send text, JPEG, PNG and PDF in both directions.
- Confirm delivery/read receipts, closed-window rejection, private file access, and isolation between two assigned sender numbers.
- Confirm workers, scheduled sync, retry recovery and cleanup run on your deployment.

Meta references: [Cloud API examples](https://github.com/fbsamples/whatsapp-api-examples), [message templates](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates), [media](https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media), and [webhooks](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks).

## License

Copyright 2026 DXBITZ Technology and contributors. Licensed under GNU AGPL version 3 or, at your option, any later version. See [LICENSE](LICENSE).
