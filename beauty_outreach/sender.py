"""
Gmail sender — OAuth2 authentication, send with rate-limiting and window
enforcement, inbox reply checking, and bounce detection.
"""

import base64
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import (
    CREDENTIALS_PATH,
    TOKEN_PATH,
    DAILY_SEND_LIMIT,
    SEND_WINDOW_START,
    SEND_WINDOW_END,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
)
from .db import get_conn, get_daily_send_count, update_daily_stats

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [sender] {msg}")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate_gmail() -> Credentials:
    """Run OAuth2 flow on first call; load cached token on subsequent calls."""
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        _log(f"Loaded credentials from {TOKEN_PATH}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            _log("Token expired — refreshing...")
            creds.refresh(Request())
            _log("Token refreshed successfully.")
        else:
            _log(f"No valid token found — starting OAuth2 browser flow using {CREDENTIALS_PATH}")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
            _log("OAuth2 flow complete.")

        with open(TOKEN_PATH, "w") as fh:
            fh.write(creds.to_json())
        _log(f"Token saved to {TOKEN_PATH}")

    return creds


def _build_service():
    creds = authenticate_gmail()
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
    skip_window_check: bool = False,
) -> str:
    """
    Send an email via Gmail API.

    Enforces:
    - Send-window check (SEND_WINDOW_START - SEND_WINDOW_END local hours)
      unless skip_window_check=True (used for warmup sends to own addresses)
    - Daily send limit (DAILY_SEND_LIMIT)
    - Random delay of MIN_DELAY_SECONDS - MAX_DELAY_SECONDS before each send

    Logs the send to sent_log and increments daily_stats.
    Returns the Gmail message ID on success.
    Raises RuntimeError for window / limit violations.
    Raises googleapiclient.errors.HttpError on API failures.
    """
    # --- send-window check ---
    if not skip_window_check:
        now_local = datetime.now()
        hour = now_local.hour
        if not (SEND_WINDOW_START <= hour < SEND_WINDOW_END):
            raise RuntimeError(
                f"Outside send window - current hour is {hour:02d}:xx, "
                f"window is {SEND_WINDOW_START:02d}:00-{SEND_WINDOW_END:02d}:00 local time."
            )

    # --- daily limit check ---
    today = datetime.now().strftime("%Y-%m-%d")  # local date — avoids UTC midnight crossing issues
    already_sent = get_daily_send_count(today)
    if already_sent >= DAILY_SEND_LIMIT:
        raise RuntimeError(
            f"Daily send limit reached — {already_sent}/{DAILY_SEND_LIMIT} emails sent today."
        )

    # --- random delay ---
    delay = random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    _log(f"Waiting {delay}s before sending to {to} ...")
    time.sleep(delay)

    # --- build MIME message ---
    message = MIMEMultipart("alternative")
    message["To"] = to
    message["Subject"] = subject

    if body_text:
        message.attach(MIMEText(body_text, "plain"))
    message.attach(MIMEText(body_html, "html"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    # --- send ---
    _log(f"Sending to {to} | subject: {subject!r}")
    service = _build_service()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    gmail_message_id = result.get("id", "")
    sent_at = datetime.now().isoformat()  # local time

    # --- log to DB ---
    with get_conn() as conn:
        # Find the contact by email to populate contact_id in sent_log
        contact = conn.execute(
            "SELECT id, sequence_step FROM contacts WHERE email = ?", (to,)
        ).fetchone()

        contact_id = contact["id"] if contact else None
        step = contact["sequence_step"] if contact else 0

        conn.execute(
            """
            INSERT INTO sent_log (contact_id, sequence_step, sent_at, subject)
            VALUES (?, ?, ?, ?)
            """,
            (contact_id, step, sent_at, subject),
        )

    update_daily_stats(today, "emails_sent")
    _log(f"Sent OK — Gmail message ID: {gmail_message_id} | daily total now: {already_sent + 1}/{DAILY_SEND_LIMIT}")

    return gmail_message_id


# ---------------------------------------------------------------------------
# Inbox reply checking
# ---------------------------------------------------------------------------

def check_inbox_for_replies(since_hours: int = 24) -> list[dict]:
    """
    Search Gmail inbox for replies to our sent emails in the last N hours.

    Returns a list of dicts:
        {sender_email, subject, received_at, thread_id}
    """
    _log(f"Checking inbox for replies in the last {since_hours}h ...")

    service = _build_service()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    # Gmail query: messages in INBOX newer than cutoff, excluding our own sends
    query = f"in:inbox after:{int(cutoff.timestamp())}"

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=100)
        .execute()
    )
    messages = results.get("messages", [])
    _log(f"Found {len(messages)} candidate inbox message(s).")

    replies = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="metadata",
                 metadataHeaders=["From", "Subject", "Date", "In-Reply-To", "References"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}

        # Only treat as a reply if it has In-Reply-To or References header
        if not headers.get("In-Reply-To") and not headers.get("References"):
            continue

        sender_raw = headers.get("From", "")
        # Extract bare email address from "Name <email>" format
        match = re.search(r"<(.+?)>", sender_raw)
        sender_email = match.group(1) if match else sender_raw.strip()

        date_str = headers.get("Date", "")
        try:
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            received_at = date_str

        replies.append({
            "sender_email": sender_email,
            "subject": headers.get("Subject", ""),
            "received_at": received_at,
            "thread_id": msg.get("threadId", ""),
        })

    _log(f"Identified {len(replies)} reply message(s).")
    return replies


# ---------------------------------------------------------------------------
# Bounce detection
# ---------------------------------------------------------------------------

def check_for_bounces() -> list[str]:
    """
    Search Gmail for delivery-failure / bounce emails from mailer-daemon or
    postmaster. Returns a list of bounced recipient email addresses.
    """
    _log("Checking for bounce/delivery-failure emails ...")

    service = _build_service()
    query = "from:(mailer-daemon OR postmaster) subject:(delivery OR bounce OR failure OR undeliverable)"

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=50)
        .execute()
    )
    messages = results.get("messages", [])
    _log(f"Found {len(messages)} potential bounce message(s).")

    bounced_addresses: list[str] = []

    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )

        # Try to extract the failed recipient from the snippet or body
        snippet = msg.get("snippet", "")

        # Common patterns: "to <email>", "recipient <email>", bare email in snippet
        found = re.findall(
            r"(?:to|recipient|address)[\s:<]+([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
            snippet,
            flags=re.IGNORECASE,
        )
        if not found:
            # Fallback: grab any email-looking string from the snippet
            found = re.findall(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                snippet,
            )

        for addr in found:
            if addr not in bounced_addresses:
                bounced_addresses.append(addr)
                _log(f"Bounce detected for: {addr}")

    _log(f"Bounce check complete — {len(bounced_addresses)} unique bounced address(es) found.")
    return bounced_addresses
