"""
Batch 3 campaign runner — date-based schedule imported from Excel.

Each day, sends every email whose scheduled_date matches today.
Follow-up and Final Email steps use the shared templates from campaign.py.
"""

from datetime import datetime, timezone

from .config import DAILY_SEND_LIMIT
from .db import (
    get_daily_send_count,
    get_batch3_emails_for_date,
    was_batch3_initial_sent,
    mark_batch3_sent,
    mark_batch3_skipped,
    mark_batch3_bounced,
)
from .sender import send_email
from .tracker import generate_tracking_pixel, generate_unsubscribe_link
from .campaign import (
    FOLLOWUP_1_SUBJECT, FOLLOWUP_1_BODY,
    FOLLOWUP_2_SUBJECT, FOLLOWUP_2_BODY,
    FOLLOWUP_3_SUBJECT, FOLLOWUP_3_BODY,
    get_first_name,
)

STEP_TEMPLATE_MAP = {
    'Follow-up #1': (FOLLOWUP_1_SUBJECT, FOLLOWUP_1_BODY),
    'Follow-up #2': (FOLLOWUP_2_SUBJECT, FOLLOWUP_2_BODY),
    'Final Email':  (FOLLOWUP_3_SUBJECT, FOLLOWUP_3_BODY),
}


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [batch3] {msg}")


def _plain_to_html(text: str) -> str:
    return text.replace("\n", "<br>")


def build_batch3_email(contact) -> dict:
    step = contact['email_step']
    first_name = get_first_name(contact['business_name'])
    # Offset ID so batch3 tracking pixels don't collide with existing contacts
    pixel_id = contact['id'] + 100000

    if step == 'Initial Email':
        subject = contact['email_subject']
        body_text = contact['email_body']
    else:
        subj_tpl, body_tpl = STEP_TEMPLATE_MAP[step]
        subject = subj_tpl.replace('[first_name]', first_name)
        body_text = body_tpl.replace('[first_name]', first_name)

    unsubscribe_url = generate_unsubscribe_link(pixel_id)
    pixel_tag = generate_tracking_pixel(pixel_id, 1)
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
        'to': contact['email'],
        'subject': subject,
        'body_html': body_html,
        'body_text': body_text,
    }


def run_batch3_session() -> dict:
    today = datetime.now().strftime('%Y-%m-%d')
    day_of_week = datetime.now().strftime('%A')

    if day_of_week in ('Saturday', 'Sunday'):
        msg = f"Weekend — no sends on {day_of_week}."
        _log(msg)
        return {'sent': 0, 'skipped': 0, 'errors': 0, 'skipped_reason': msg}

    already_sent = get_daily_send_count(today)
    remaining_slots = DAILY_SEND_LIMIT - already_sent

    if remaining_slots <= 0:
        msg = f"Daily limit reached ({already_sent}/{DAILY_SEND_LIMIT})."
        _log(msg)
        return {'sent': 0, 'skipped': 0, 'errors': 0, 'skipped_reason': msg}

    scheduled = get_batch3_emails_for_date(today)
    _log(f"Scheduled today: {len(scheduled)} | slots available: {remaining_slots}/{DAILY_SEND_LIMIT}")

    sent = skipped = errors = 0
    skipped_reason = None

    for contact in scheduled:
        if remaining_slots <= 0:
            break

        step = contact['email_step']

        # Skip if reply already received from this lead
        if contact['reply_received']:
            mark_batch3_skipped(contact['id'])
            skipped += 1
            _log(f"Skipped (replied) → {contact['email']}")
            continue

        # For follow-ups/final: skip if initial was never sent (e.g. bounced)
        if step != 'Initial Email':
            if not was_batch3_initial_sent(contact['email']):
                mark_batch3_skipped(contact['id'])
                skipped += 1
                _log(f"Skipped (initial not sent) → {contact['email']} [{step}]")
                continue

        email = build_batch3_email(contact)

        try:
            send_email(
                to=email['to'],
                subject=email['subject'],
                body_html=email['body_html'],
                body_text=email['body_text'],
            )
        except RuntimeError as exc:
            _log(f"Stopping early: {exc}")
            skipped_reason = str(exc)
            break
        except Exception as exc:
            _log(f"Error sending to {contact['email']}: {exc}")
            mark_batch3_bounced(contact['email'])
            errors += 1
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        mark_batch3_sent(contact['id'], now_iso)
        sent += 1
        remaining_slots -= 1
        _log(f"{step} sent → {contact['email']} | slots left: {remaining_slots}")

    _log(f"Session complete. sent={sent} skipped={skipped} errors={errors}")
    return {
        'sent': sent,
        'skipped': skipped,
        'errors': errors,
        'skipped_reason': skipped_reason,
    }


def batch3_status_report() -> dict:
    from .db import get_conn, init_batch3_schedule
    init_batch3_schedule()
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM batch3_schedule").fetchone()[0]
        scheduled = conn.execute(
            "SELECT COUNT(*) FROM batch3_schedule WHERE status = 'scheduled'"
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM batch3_schedule WHERE status = 'sent'"
        ).fetchone()[0]
        skipped = conn.execute(
            "SELECT COUNT(*) FROM batch3_schedule WHERE status = 'skipped'"
        ).fetchone()[0]
        bounced = conn.execute(
            "SELECT COUNT(*) FROM batch3_schedule WHERE status = 'bounced'"
        ).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(DISTINCT email) FROM batch3_schedule WHERE reply_received = 1"
        ).fetchone()[0]
    return {
        'total': total,
        'scheduled': scheduled,
        'sent': sent,
        'skipped': skipped,
        'bounced': bounced,
        'replied': replied,
    }
