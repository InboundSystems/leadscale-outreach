"""
Warm-up engine — gradually ramps daily send volume to build sender reputation.

The account has ~65 sent / ~50 received prior to this system, so we treat
it as already on Day 15 of the warm-up schedule (WARMUP_START_DAY in config).

Schedule:
  Day 15       → 20 emails/day
  Days 16–21   → 25 emails/day
  Day 22+      → 40 emails/day  (warm-up complete; this is the full send cap)

All warm-up sends count toward the global DAILY_SEND_LIMIT of 40.
"""

import random
from datetime import datetime, timezone

from .config import (
    DAILY_SEND_LIMIT,
    WARMUP_SEED_EMAILS,
    WARMUP_START_DAY,
)
from .db import get_conn, get_daily_send_count
from .sender import send_email

WARMUP_PAIRS = [
    (
        "Quick question",
        "Hey, just wanted to reach out — do you have a moment to chat this week?",
    ),
    (
        "Checking in",
        "Hi! Hope things are going well on your end. Let me know if you're around.",
    ),
    (
        "Following up",
        "Hey — just circling back on something. Worth a quick chat when you're free?",
    ),
    (
        "Touching base",
        "Hi there, hope you're having a good week. Just wanted to say hello!",
    ),
    (
        "Re: catch up",
        "Hey! Long time no speak. Hope everything's going well — let's catch up soon.",
    ),
]


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [warmup] {msg}")


# ---------------------------------------------------------------------------
# Core state helpers
# ---------------------------------------------------------------------------

def get_warmup_day() -> int:
    """
    Calculate current warm-up day from warmup_log.
    Falls back to WARMUP_START_DAY when no log entries exist yet,
    reflecting prior sending history before this system was set up.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(sent_at) AS first_sent FROM warmup_log"
        ).fetchone()

    first_sent_str = row["first_sent"] if row else None

    if not first_sent_str:
        return WARMUP_START_DAY

    first_sent = datetime.fromisoformat(first_sent_str.replace("Z", "+00:00"))

    # Use local date for both sides so midnight local time advances the day,
    # regardless of whether the timestamp was stored in UTC or local time.
    first_date = first_sent.date()
    today_date = datetime.now().date()
    elapsed_days = (today_date - first_date).days

    return WARMUP_START_DAY + elapsed_days


def get_daily_warmup_limit() -> int:
    """Return today's warm-up email target based on the current warm-up day."""
    day = get_warmup_day()
    if day <= 15:
        return 20
    if day <= 21:
        return 25
    return 40  # Day 22+ — warm-up complete, full send cap


def is_warmup_complete() -> bool:
    """Return True once warm-up day reaches 22."""
    return get_warmup_day() >= 22


# ---------------------------------------------------------------------------
# Session runner
# ---------------------------------------------------------------------------

def run_warmup_session() -> dict:
    """
    Send warm-up emails for today.

    - Skips if today's warm-up quota is already met.
    - Skips if the global DAILY_SEND_LIMIT is already exhausted.
    - Rotates through WARMUP_SEED_EMAILS, picks subject/body at random.
    - Delegates actual sending (with delay enforcement) to sender.send_email().
    - Logs every send to warmup_log.

    Returns:
        {sent_today, warmup_day, is_complete}
    """
    today = datetime.now().strftime("%Y-%m-%d")  # local date — avoids UTC midnight crossing issues
    warmup_day = get_warmup_day()
    daily_limit = get_daily_warmup_limit()
    complete = is_warmup_complete()

    _log(f"Warm-up day {warmup_day} | target: {daily_limit} | complete: {complete}")

    if not WARMUP_SEED_EMAILS:
        _log("No WARMUP_SEED_EMAILS configured — skipping.")
        return {"sent_today": 0, "warmup_day": warmup_day, "is_complete": complete}

    # How many warm-up emails have already been sent today?
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM warmup_log
            WHERE DATE(sent_at) = ?
            """,
            (today,),
        ).fetchone()
    warmup_sent_today = row["cnt"] if row else 0

    if warmup_sent_today >= daily_limit:
        _log(f"Warm-up quota already met for today ({warmup_sent_today}/{daily_limit}).")
        return {"sent_today": warmup_sent_today, "warmup_day": warmup_day, "is_complete": complete}

    # How many total emails (all types) sent today?
    global_sent_today = get_daily_send_count(today)
    if global_sent_today >= DAILY_SEND_LIMIT:
        _log(f"Global daily limit reached ({global_sent_today}/{DAILY_SEND_LIMIT}) — stopping.")
        return {"sent_today": warmup_sent_today, "warmup_day": warmup_day, "is_complete": complete}

    remaining_warmup = daily_limit - warmup_sent_today
    remaining_global = DAILY_SEND_LIMIT - global_sent_today
    to_send = min(remaining_warmup, remaining_global)

    _log(f"Already sent today: {warmup_sent_today} warmup, {global_sent_today} total. Sending {to_send} more.")

    sent_count = 0
    seed_cycle = list(WARMUP_SEED_EMAILS)  # copy so we can shuffle without mutation
    random.shuffle(seed_cycle)

    for i in range(to_send):
        recipient = seed_cycle[i % len(seed_cycle)]
        subject, body_text = random.choice(WARMUP_PAIRS)

        try:
            send_email(
                to=recipient,
                subject=subject,
                body_html=f"<p>{body_text}</p>",
                body_text=body_text,
                skip_window_check=True,
            )
        except RuntimeError as exc:
            # Daily limit hit - stop gracefully
            _log(f"Stopping early: {exc}")
            break
        except Exception as exc:
            _log(f"Error sending to {recipient}: {exc}")
            continue

        now_iso = datetime.now().isoformat()  # local time — keeps DATE() comparisons consistent
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO warmup_log (sent_at, to_email) VALUES (?, ?)",
                (now_iso, recipient),
            )

        sent_count += 1
        _log(f"Warm-up send {sent_count}/{to_send} → {recipient}")

    total_sent_today = warmup_sent_today + sent_count
    _log(f"Warm-up session complete. Sent {sent_count} email(s) this run ({total_sent_today} total today).")
    return {
        "sent_today": total_sent_today,
        "warmup_day": warmup_day,
        "is_complete": is_warmup_complete(),
    }


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def warmup_status_report() -> dict:
    """Return a snapshot of warm-up state for the dashboard or CLI."""
    today = datetime.now().strftime("%Y-%m-%d")  # local date
    warmup_day = get_warmup_day()

    with get_conn() as conn:
        sent_today_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM warmup_log WHERE DATE(sent_at) = ?",
            (today,),
        ).fetchone()
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM warmup_log"
        ).fetchone()

    return {
        "current_day": warmup_day,
        "is_complete": is_warmup_complete(),
        "emails_sent_today": sent_today_row["cnt"] if sent_today_row else 0,
        "total_warmup_sent": total_row["cnt"] if total_row else 0,
        "daily_limit": get_daily_warmup_limit(),
    }
