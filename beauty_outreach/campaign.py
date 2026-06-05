"""
Campaign sequencer — initial outreach + 3 follow-up steps.

Edit the three follow-up templates below before going live, then call
run_campaign_session() daily from main.py or a scheduler.
"""

from datetime import datetime, timezone, timedelta

from .config import DAILY_SEND_LIMIT, FOLLOWUP_DELAYS
from .db import get_conn, get_daily_send_count
from .sender import send_email
from .tracker import generate_tracking_pixel, generate_unsubscribe_link
from .warmup import is_warmup_complete

# Campaign ramp-up: (days_duration, limit) — last entry applies forever
CAMPAIGN_RAMP = [
    (3, 30),    # Days 1-3:   30/day
    (3, 35),    # Days 4-6:   35/day
    (1, 40),    # Day 7:      40/day
    (1, 45),    # Day 8:      45/day
    (1, 50),    # Day 9:      50/day
    (1, 55),    # Day 10:     55/day
    (1, 60),    # Day 11:     60/day
    (1, 65),    # Day 12:     65/day
    (1, 70),    # Day 13:     70/day
    (1, 75),    # Day 14:     75/day
    (None, 80), # Day 15+:    80/day
]


def get_campaign_daily_limit() -> int:
    """
    Returns today's send limit based on how many days since the first
    campaign email was sent (ramps 30 -> 35 -> 40 over 6 days).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(sent_at) AS first_sent FROM sent_log WHERE sequence_step >= 1"
        ).fetchone()

    if not row or not row["first_sent"]:
        return CAMPAIGN_RAMP[0][1]  # Not started yet — return first tier

    first_date = datetime.fromisoformat(row["first_sent"]).date()
    elapsed = (datetime.now().date() - first_date).days

    cumulative = 0
    for duration, limit in CAMPAIGN_RAMP:
        if duration is None:
            return limit
        cumulative += duration
        if elapsed < cumulative:
            return limit

    return DAILY_SEND_LIMIT

# ---------------------------------------------------------------------------
# Follow-up templates — EDIT THESE BEFORE GOING LIVE
# ---------------------------------------------------------------------------

# EDIT THIS BEFORE GOING LIVE
FOLLOWUP_1_SUBJECT = "Re: quick one"
FOLLOWUP_1_BODY = """
Hey [first_name],

Just wanted to bump this up in case it got buried - completely understand if the timing isn't right.

I work with beauty businesses across Brisbane and the Gold Coast helping them get in front of more of the right clients online - whether that's through social media, paid ads, or just making sure the right people can actually find them.

Worth a quick 15-minute chat to see if there's anything useful I can offer?

- Sam

P.S. If now's not a good time I completely understand - just let me know and I won't follow up again.
"""

# EDIT THIS BEFORE GOING LIVE
FOLLOWUP_2_SUBJECT = "Re: still open to chatting"
FOLLOWUP_2_BODY = """
Hey [first_name],

I know you're probably flat out running the business day to day - just didn't want to disappear without checking in one more time.

A lot of the salons and studios I work with said the same thing before we spoke: new clients were coming mostly through word of mouth, and online just wasn't pulling its weight. Usually takes one conversation to figure out whether there's a real opportunity there.

Happy to keep it low-key - no pitch, just a quick chat.

- Sam
"""

# EDIT THIS BEFORE GOING LIVE
FOLLOWUP_3_SUBJECT = "Re: closing the loop"
FOLLOWUP_3_BODY = """
Hey [first_name],

Last one from me - I promise.

If the timing just isn't right or you're not looking to grow your client base right now, completely understood. I won't keep nudging.

But if anything changes down the track, you're welcome to reach out at sam@leadscalesystems.net - happy to help whenever it suits.

Wishing you and the team a great one.

- Sam
"""

# Maps sequence_step (after initial send) to (delay_days, subject, body)
_FOLLOWUP_MAP = {
    1: (FOLLOWUP_DELAYS[0], FOLLOWUP_1_SUBJECT, FOLLOWUP_1_BODY),
    2: (FOLLOWUP_DELAYS[1], FOLLOWUP_2_SUBJECT, FOLLOWUP_2_BODY),
    3: (FOLLOWUP_DELAYS[2], FOLLOWUP_3_SUBJECT, FOLLOWUP_3_BODY),
}


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [campaign] {msg}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_first_name(business_name: str) -> str:
    """Return the first word of business_name, capitalised."""
    if not business_name or not business_name.strip():
        return "there"
    return business_name.strip().split()[0].capitalize()


def _plain_to_html(text: str) -> str:
    return text.replace("\n", "<br>")


def _days_since(timestamp_str: str | None) -> float:
    if not timestamp_str:
        return float("inf")
    last = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 86400


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def get_contacts_due_for_initial() -> list:
    """Return queued contacts that have never been sent an email, oldest first."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, business_name, email, email_subject, unique_email_body, created_at
            FROM contacts
            WHERE status = 'queued' AND sequence_step = 0
            ORDER BY created_at ASC
            """
        ).fetchall()


def get_contacts_due_for_followup() -> list:
    """
    Return contacts ready for their next follow-up, oldest last-sent first.

    sequence_step=1 → 3+ days since last send → send follow-up 1
    sequence_step=2 → 7+ days since last send → send follow-up 2
    sequence_step=3 → 14+ days since last send → send follow-up 3
    """
    due = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, business_name, email, email_subject, unique_email_body,
                   sequence_step, last_sent_at
            FROM contacts
            WHERE status = 'sent'
              AND reply_received = 0
              AND sequence_step IN (1, 2, 3)
            ORDER BY last_sent_at ASC
            """
        ).fetchall()

    for row in rows:
        delay_days, _, _ = _FOLLOWUP_MAP[row["sequence_step"]]
        if _days_since(row["last_sent_at"]) >= delay_days:
            due.append(row)

    return due


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email_for_contact(contact, step: int) -> dict:
    """
    Build {to, subject, body_html, body_text} for a given contact and sequence step.

    step=1  → initial email (contact's own subject + body)
    step=2  → follow-up 1 template
    step=3  → follow-up 2 template
    step=4  → follow-up 3 template

    HTML body includes a tracking pixel and an unsubscribe footer.
    """
    first_name = get_first_name(contact["business_name"])
    contact_id = contact["id"]

    if step == 1:
        subject = contact["email_subject"]
        body_text = contact["unique_email_body"]
    else:
        _, subj_tpl, body_tpl = _FOLLOWUP_MAP[step - 1]
        subject = subj_tpl.replace("[first_name]", first_name)
        body_text = body_tpl.replace("[first_name]", first_name)

    unsubscribe_url = generate_unsubscribe_link(contact_id)
    pixel_tag = generate_tracking_pixel(contact_id, step)
    unsubscribe_footer = (
        f"<p style='font-size:11px;color:#999;'>To unsubscribe from future emails, "
        f"<a href='{unsubscribe_url}'>click here</a>.</p>"
    )

    body_html = (
        f"<html><body>"
        f"{_plain_to_html(body_text)}"
        f"{unsubscribe_footer}"
        f"{pixel_tag}"
        f"</body></html>"
    )

    return {
        "to": contact["email"],
        "subject": subject,
        "body_html": body_html,
        "body_text": body_text,
    }


# ---------------------------------------------------------------------------
# DB write-back
# ---------------------------------------------------------------------------

def mark_email_sent(contact_id: int, step: int, subject: str):
    """Update contacts row and insert a sent_log entry."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE contacts
            SET sequence_step = ?, last_sent_at = ?, status = 'sent'
            WHERE id = ?
            """,
            (step, now_iso, contact_id),
        )
        conn.execute(
            """
            INSERT INTO sent_log (contact_id, sequence_step, sent_at, subject)
            VALUES (?, ?, ?, ?)
            """,
            (contact_id, step, now_iso, subject),
        )


# ---------------------------------------------------------------------------
# Main session runner
# ---------------------------------------------------------------------------

def run_campaign_session() -> dict:
    """
    Send today's campaign emails: follow-ups first, then initial emails.

    Guards:
    - Warm-up must be complete before any outreach sends.
    - Respects DAILY_SEND_LIMIT across all send types.
    - Stops gracefully on RuntimeError from sender (window / limit violations).

    Returns:
        {initial_sent, followups_sent, total_sent, remaining_in_queue, skipped_reason}
    """
    skipped_reason = None

    if not is_warmup_complete():
        msg = (
            "Warm-up not yet complete — campaign sends are disabled until Day 22. "
            "Run run_warmup_session() to continue building sender reputation."
        )
        _log(f"WARNING: {msg}")
        remaining = len(get_contacts_due_for_initial()) + len(get_contacts_due_for_followup())
        return {
            "initial_sent": 0,
            "followups_sent": 0,
            "total_sent": 0,
            "remaining_in_queue": remaining,
            "skipped_reason": msg,
        }

    today = datetime.now().strftime("%Y-%m-%d")  # local date
    already_sent = get_daily_send_count(today)
    daily_limit = get_campaign_daily_limit()

    if already_sent >= daily_limit:
        msg = f"Daily send limit reached ({already_sent}/{daily_limit}) — no campaign emails sent."
        _log(msg)
        remaining = len(get_contacts_due_for_initial()) + len(get_contacts_due_for_followup())
        return {
            "initial_sent": 0,
            "followups_sent": 0,
            "total_sent": 0,
            "remaining_in_queue": remaining,
            "skipped_reason": msg,
        }

    remaining_slots = daily_limit - already_sent
    _log(f"Starting campaign session. Slots available today: {remaining_slots}/{daily_limit} (ramp day limit)")

    followups_sent = 0
    initial_sent = 0

    # --- Priority 1: follow-ups ---
    followup_contacts = get_contacts_due_for_followup()
    _log(f"Follow-ups due: {len(followup_contacts)}")

    for contact in followup_contacts:
        if remaining_slots <= 0:
            break

        next_step = contact["sequence_step"] + 1  # step stored is last completed; next to send
        email = build_email_for_contact(contact, next_step)

        try:
            send_email(
                to=email["to"],
                subject=email["subject"],
                body_html=email["body_html"],
                body_text=email["body_text"],
            )
        except RuntimeError as exc:
            _log(f"Stopping follow-ups early: {exc}")
            skipped_reason = str(exc)
            break
        except Exception as exc:
            _log(f"Error sending follow-up to {contact['email']}: {exc}")
            continue

        mark_email_sent(contact["id"], next_step, email["subject"])
        followups_sent += 1
        remaining_slots -= 1
        _log(f"Follow-up step {next_step} sent -> {contact['email']} | slots left: {remaining_slots}")

    # --- Priority 2: initial emails ---
    if remaining_slots > 0 and skipped_reason is None:
        initial_contacts = get_contacts_due_for_initial()
        _log(f"Initial emails queued: {len(initial_contacts)} | slots remaining: {remaining_slots}")

        for contact in initial_contacts:
            if remaining_slots <= 0:
                break

            email = build_email_for_contact(contact, 1)

            try:
                send_email(
                    to=email["to"],
                    subject=email["subject"],
                    body_html=email["body_html"],
                    body_text=email["body_text"],
                )
            except RuntimeError as exc:
                _log(f"Stopping initial sends early: {exc}")
                skipped_reason = str(exc)
                break
            except Exception as exc:
                _log(f"Error sending initial to {contact['email']}: {exc}")
                continue

            mark_email_sent(contact["id"], 1, email["subject"])
            initial_sent += 1
            remaining_slots -= 1
            _log(f"Initial email sent -> {contact['email']} | slots left: {remaining_slots}")

    total_sent = followups_sent + initial_sent
    remaining_in_queue = (
        len(get_contacts_due_for_initial()) + len(get_contacts_due_for_followup())
    )

    _log(
        f"Session complete. initial={initial_sent} followups={followups_sent} "
        f"total={total_sent} queue_remaining={remaining_in_queue}"
    )

    return {
        "initial_sent": initial_sent,
        "followups_sent": followups_sent,
        "total_sent": total_sent,
        "remaining_in_queue": remaining_in_queue,
        "skipped_reason": skipped_reason,
    }
