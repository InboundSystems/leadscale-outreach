"""
Tracking module — pixel generation, open/reply/bounce recording, and inbox polling.
"""

from datetime import datetime, timezone

from .config import TRACKING_BASE_URL
from .db import get_conn, update_daily_stats


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [tracker] {msg}")


# ---------------------------------------------------------------------------
# Link / pixel generators
# ---------------------------------------------------------------------------

def generate_tracking_pixel(contact_id: int, step: int) -> str:
    """Return a 1x1 transparent tracking pixel img tag for the given contact + step."""
    url = f"{TRACKING_BASE_URL}/track/open/{contact_id}/{step}"
    return (
        f'<img src="{url}" width="1" height="1" style="display:none" alt="" />'
    )


def generate_unsubscribe_link(contact_id: int) -> str:
    """Return the unsubscribe URL for the given contact."""
    return f"{TRACKING_BASE_URL}/unsubscribe/{contact_id}"


# ---------------------------------------------------------------------------
# Event recorders
# ---------------------------------------------------------------------------

def record_open(contact_id: int, step: int):
    """
    Mark a contact as opened when their tracking pixel is hit.

    - Only counts the first open (idempotent on subsequent hits).
    - Stamps opened_at on the matching sent_log row for this step.
    - Increments daily_stats.opens for today.
    """
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now().strftime("%Y-%m-%d")  # local date

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT opened FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()

        if not existing:
            return

        if not existing["opened"]:
            conn.execute(
                "UPDATE contacts SET opened = 1 WHERE id = ?",
                (contact_id,),
            )
            update_daily_stats(today, "opens")
            _log(f"Open recorded — contact_id={contact_id} step={step}")

        # Always stamp the sent_log row for this step, if not already stamped
        conn.execute(
            """
            UPDATE sent_log SET opened_at = ?
            WHERE id = (
                SELECT id FROM sent_log
                WHERE contact_id = ? AND sequence_step = ? AND opened_at IS NULL
                ORDER BY sent_at DESC
                LIMIT 1
            )
            """,
            (now, contact_id, step),
        )


def record_reply(email_address: str):
    """
    Mark a contact as replied when a reply is detected in the inbox.

    Looks up the contact by email address, updates contacts + sent_log,
    and increments daily_stats.replies for today.
    """
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now().strftime("%Y-%m-%d")  # local date

    with get_conn() as conn:
        contact = conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email_address,)
        ).fetchone()

        if not contact:
            _log(f"Reply detected from unknown address: {email_address} — skipping.")
            return

        contact_id = contact["id"]

        conn.execute(
            """
            UPDATE contacts
            SET reply_received = 1, status = 'replied'
            WHERE id = ?
            """,
            (contact_id,),
        )
        conn.execute(
            """
            UPDATE sent_log SET replied_at = ?
            WHERE id = (
                SELECT id FROM sent_log
                WHERE contact_id = ? AND replied_at IS NULL
                ORDER BY sent_at DESC
                LIMIT 1
            )
            """,
            (now, contact_id),
        )

    update_daily_stats(today, "replies")
    _log(f"Reply recorded — email={email_address} contact_id={contact_id}")

    # Also mark any batch3 rows for this email so follow-ups are skipped
    from .db import mark_batch3_reply
    mark_batch3_reply(email_address)


def record_bounce(email_address: str):
    """
    Mark a contact as bounced and increment daily_stats.bounces for today.
    """
    today = datetime.now().strftime("%Y-%m-%d")  # local date

    with get_conn() as conn:
        contact = conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email_address,)
        ).fetchone()

        if not contact:
            _log(f"Bounce detected for unknown address: {email_address} — skipping.")
            return

        conn.execute(
            "UPDATE contacts SET status = 'bounced' WHERE id = ?",
            (contact["id"],),
        )

    update_daily_stats(today, "bounces")
    _log(f"Bounce recorded — email={email_address} contact_id={contact['id']}")

    from .db import mark_batch3_bounced
    mark_batch3_bounced(email_address)


def record_unsubscribe(contact_id: int):
    """Mark a contact as unsubscribed."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status = 'unsubscribed' WHERE id = ?",
            (contact_id,),
        )
    _log(f"Unsubscribe recorded — contact_id={contact_id}")


# ---------------------------------------------------------------------------
# Inbox polling
# ---------------------------------------------------------------------------

def poll_for_replies_and_bounces():
    """
    Check Gmail for replies and bounce notifications received in the last 2 hours.

    Intended to be called every 30 minutes while the app is running.
    Updates the database and prints a timestamped summary.
    """
    # Import here to avoid a circular import at module load time
    from .sender import check_inbox_for_replies, check_for_bounces

    _log("Polling inbox for replies and bounces...")

    # --- Replies ---
    replies = check_inbox_for_replies(since_hours=48)
    reply_count = 0
    for reply in replies:
        record_reply(reply["sender_email"])
        reply_count += 1
        _log(
            f"  Reply from {reply['sender_email']} | "
            f"subject: {reply['subject']!r} | received: {reply['received_at']}"
        )

    # --- Bounces ---
    bounced_addresses = check_for_bounces()
    bounce_count = 0
    for addr in bounced_addresses:
        record_bounce(addr)
        bounce_count += 1

    _log(
        f"Poll complete — replies found: {reply_count}, bounces found: {bounce_count}"
    )
    return {"replies_recorded": reply_count, "bounces_recorded": bounce_count}
