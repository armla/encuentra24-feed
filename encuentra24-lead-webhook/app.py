"""
Encuentra24 Lead Webhook Receiver
The Agency Costa Rica

Receives POST webhooks from Encuentra24, parses the lead payload,
logs it to a Google Sheet, and sends an email notification to the team.

Environment variables required:
  WEBHOOK_SECRET      - Optional shared secret for request validation
  SHEET_ID            - Google Sheets spreadsheet ID for lead logging
  NOTIFY_EMAILS       - Comma-separated list of team email addresses
  SENDER_EMAIL        - Gmail address used to send notifications (must be authed via gws)
"""

import os
import json
import subprocess
import logging
from datetime import datetime
from flask import Flask, request, jsonify, abort

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "")          # optional
SHEET_ID        = os.getenv("SHEET_ID", "")                # required
NOTIFY_EMAILS   = [e.strip() for e in os.getenv("NOTIFY_EMAILS", "").split(",") if e.strip()]
SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "me")


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_lead(payload: dict) -> dict:
    """Flatten the Encuentra24 webhook payload into a clean lead dict."""
    contact = payload.get("contact") or {}
    ad      = payload.get("addetails") or {}
    extra   = payload.get("leadadditionaldata") or {}

    return {
        "received_at":   datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "created_at":    payload.get("createdat", ""),
        "lead_id":       payload.get("id", ""),
        "ad_id":         payload.get("adid", ""),
        "source_id":     payload.get("sourceid", ""),
        "title":         payload.get("title", ""),
        "message":       payload.get("message", ""),
        # Contact
        "contact_name":  contact.get("name", ""),
        "contact_email": contact.get("email", ""),
        "contact_phone":  "'" + contact.get("phone", "") if contact.get("phone") else "",
        # Ad details
        "ad_title":      ad.get("title", ""),
        "ad_category":   ad.get("category", ""),
        "ad_price":      ad.get("price", ""),
        "ad_currency":   ad.get("currency", ""),
        # Extra data (serialised for storage)
        "extra_data":    json.dumps(extra, ensure_ascii=False) if extra else "",
        # Raw payload for audit
        "raw_payload":   json.dumps(payload, ensure_ascii=False),
    }


def append_to_sheet(lead: dict) -> bool:
    """Append one lead row to the configured Google Sheet via gws CLI."""
    if not SHEET_ID:
        log.warning("SHEET_ID not set — skipping Sheets logging.")
        return False

    # Column order must match the header row in the sheet
    row_values = [
        lead["received_at"],
        lead["created_at"],
        lead["lead_id"],
        lead["ad_id"],
        lead["source_id"],
        lead["title"],
        lead["message"],
        lead["contact_name"],
        lead["contact_email"],
        lead["contact_phone"],
        lead["ad_title"],
        lead["ad_category"],
        str(lead["ad_price"]),
        lead["ad_currency"],
        lead["extra_data"],
        lead["raw_payload"],
    ]

    body = {
        "values": [row_values]
    }

    cmd = [
        "gws", "sheets", "spreadsheets", "values", "append",
        "--params", json.dumps({
            "spreadsheetId": SHEET_ID,
            "range": "Leads!A1",
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
        }),
        "--json", json.dumps(body),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            log.info("Lead %s appended to Sheet %s", lead["lead_id"], SHEET_ID)
            return True
        else:
            log.error("gws append failed: %s", result.stderr)
            return False
    except Exception as exc:
        log.error("gws append exception: %s", exc)
        return False


def send_email_notification(lead: dict) -> bool:
    """Send a formatted lead notification email via gws Gmail."""
    if not NOTIFY_EMAILS:
        log.warning("NOTIFY_EMAILS not set — skipping email notification.")
        return False

    subject = (
        f"[Encuentra24 Lead] {lead['contact_name'] or 'Unknown'} — "
        f"{lead['ad_title'] or lead['title']}"
    )

    body_lines = [
        "A new lead has been received from Encuentra24.",
        "",
        "── CONTACT ──────────────────────────────────",
        f"  Name    : {lead['contact_name'] or '(not provided)'}",
        f"  Email   : {lead['contact_email'] or '(not provided)'}",
        f"  Phone   : {lead['contact_phone'] or '(not provided)'}",
        "",
        "── MESSAGE ──────────────────────────────────",
        f"  {lead['message'] or '(no message)'}",
        "",
        "── AD DETAILS ───────────────────────────────",
        f"  Ad Title  : {lead['ad_title']}",
        f"  Category  : {lead['ad_category']}",
        f"  Price     : {lead['ad_price']} {lead['ad_currency']}",
        f"  Ad ID     : {lead['ad_id']}",
        f"  Source ID : {lead['source_id']}",
        "",
        "── METADATA ─────────────────────────────────",
        f"  Lead ID    : {lead['lead_id']}",
        f"  Created At : {lead['created_at']}",
        f"  Received   : {lead['received_at']}",
        "",
        "─────────────────────────────────────────────",
        "The Agency Costa Rica — Automated Lead System",
    ]

    body_text = "\n".join(body_lines)

    # Build RFC-2822 raw message
    import base64
    from email.mime.text import MIMEText

    for recipient in NOTIFY_EMAILS:
        msg = MIMEText(body_text, "plain", "utf-8")
        msg["To"]      = recipient
        msg["From"]    = SENDER_EMAIL
        msg["Subject"] = subject
        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        send_body = json.dumps({"raw": raw_b64})
        cmd = [
            "gws", "gmail", "users", "messages", "send",
            "--params", json.dumps({"userId": "me"}),
            "--json", send_body,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                log.info("Notification sent to %s", recipient)
            else:
                log.error("Gmail send failed for %s: %s", recipient, result.stderr)
        except Exception as exc:
            log.error("Gmail send exception for %s: %s", recipient, exc)

    return True


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "encuentra24-webhook"}), 200


@app.route("/webhook/encuentra24", methods=["POST"])
def receive_lead():
    # Optional shared-secret validation
    if WEBHOOK_SECRET:
        provided = request.headers.get("X-Webhook-Secret", "")
        if provided != WEBHOOK_SECRET:
            log.warning("Rejected webhook — invalid secret from %s", request.remote_addr)
            abort(403)

    # Parse JSON body
    if not request.is_json:
        log.warning("Non-JSON payload received")
        return jsonify({"error": "Content-Type must be application/json"}), 400

    payload = request.get_json(force=True, silent=True)
    if not payload:
        return jsonify({"error": "Empty or malformed JSON body"}), 400

    log.info("Received lead payload: lead_id=%s", payload.get("id", "?"))

    # Parse and process
    lead = parse_lead(payload)

    sheet_ok = append_to_sheet(lead)
    email_ok = send_email_notification(lead)

    return jsonify({
        "status":      "received",
        "lead_id":     lead["lead_id"],
        "sheet_logged": sheet_ok,
        "email_sent":   email_ok,
    }), 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
