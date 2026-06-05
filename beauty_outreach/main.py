"""
Beauty Outreach CLI - entry point for all commands.

Usage:
    python main.py <command> [args]

Commands:
    setup       Initialise database and authenticate Gmail
    import      Import contacts from an Excel file
    warmup      Send today's warm-up emails
    campaign    Send today's campaign emails (initial + follow-ups)
    poll        Check Gmail for replies and bounces
    dashboard   Start the web dashboard with background polling
    status      Print a full console status report
"""

import sys
import threading
import time
from datetime import datetime, timezone


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hr():
    print("-" * 56)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_setup():
    from .db import init_db
    from .sender import authenticate_gmail

    print(f"[{_ts()}] Initialising database...")
    init_db()
    print(f"[{_ts()}] Database ready.")

    print(f"[{_ts()}] Starting Gmail OAuth2 authentication...")
    print("       A browser window will open - sign in and grant access.")
    authenticate_gmail()
    print(f"[{_ts()}] Gmail authentication complete. token.json saved.")

    _hr()
    print("Setup complete. Next steps:")
    print("  1. Open beauty_outreach/config.py")
    print("     -Add at least 2 real email addresses to WARMUP_SEED_EMAILS")
    print("     -These addresses must be able to receive and reply to emails")
    print("  2. Import your contacts:")
    print("     python main.py import leads_batch1_new_emails.xlsx")
    print("  3. Fill in your follow-up templates in beauty_outreach/campaign.py")
    print("     -Each template is marked:  # EDIT THIS BEFORE GOING LIVE")
    print("  4. Run your first warm-up session:")
    print("     python main.py warmup")
    _hr()


def cmd_import(filepath: str):
    from .db import import_contacts_from_excel

    print(f"[{_ts()}] Importing contacts from: {filepath}")
    result = import_contacts_from_excel(filepath)
    _hr()
    print(f"  Imported (new):      {result['imported']}")
    print(f"  Updated (queued):    {result['updated']}")
    print(f"  Skipped (sent):      {result['skipped_already_sent']}")
    print(f"  Skipped (no data):   {result['skipped_empty']}")
    _hr()
    if result["imported"] == 0 and result["updated"] == 0:
        print("  Warning: no contacts were imported.")
        print("  Check that the sheet names are exactly:")
        print('    "2 - Email + Phone"  and  "3 - Email Only"')


def cmd_warmup():
    from .warmup import run_warmup_session, warmup_status_report

    print(f"[{_ts()}] Starting warm-up session...")
    result = run_warmup_session()
    report = warmup_status_report()

    _hr()
    print(f"  Warm-up day:         {report['current_day']} / 28")
    print(f"  Complete:            {'Yes' if report['is_complete'] else 'No'}")
    print(f"  Daily limit today:   {report['daily_limit']}")
    print(f"  Sent this session:   {result.get('sent_today', 0) - (report['emails_sent_today'] - result.get('sent_today', 0))}")
    print(f"  Sent today (total):  {report['emails_sent_today']}")
    print(f"  All-time warmup:     {report['total_warmup_sent']}")
    _hr()


def cmd_campaign():
    from .campaign import run_campaign_session

    print(f"[{_ts()}] Starting campaign session...")
    result = run_campaign_session()

    _hr()
    print(f"  Initial emails sent: {result['initial_sent']}")
    print(f"  Follow-ups sent:     {result['followups_sent']}")
    print(f"  Total sent:          {result['total_sent']}")
    print(f"  Remaining in queue:  {result['remaining_in_queue']}")
    if result.get("skipped_reason"):
        print(f"  Note: {result['skipped_reason']}")
    _hr()


def cmd_import_batch3(filepath: str):
    from .db import import_batch3_from_excel

    print(f"[{_ts()}] Importing batch 3 schedule from: {filepath}")
    result = import_batch3_from_excel(filepath)
    _hr()
    print(f"  Imported:  {result['imported']}")
    print(f"  Skipped:   {result['skipped']}")
    _hr()


def cmd_batch3():
    from .campaign_batch3 import run_batch3_session

    print(f"[{_ts()}] Starting batch 3 session...")
    result = run_batch3_session()

    _hr()
    print(f"  Sent:      {result['sent']}")
    print(f"  Skipped:   {result['skipped']}")
    print(f"  Errors:    {result['errors']}")
    if result.get("skipped_reason"):
        print(f"  Note: {result['skipped_reason']}")
    _hr()


def cmd_batch3_status():
    from .campaign_batch3 import batch3_status_report
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    report = batch3_status_report()

    _hr()
    print(f"  Batch 3 Status Report  ({today})")
    _hr()
    print(f"  Total scheduled:  {report['total']}")
    print(f"  Sent:             {report['sent']}")
    print(f"  Remaining:        {report['scheduled']}")
    print(f"  Skipped:          {report['skipped']}")
    print(f"  Bounced:          {report['bounced']}")
    print(f"  Replied:          {report['replied']}")
    _hr()


def cmd_poll():
    from .tracker import poll_for_replies_and_bounces

    print(f"[{_ts()}] Polling Gmail for replies and bounces...")
    result = poll_for_replies_and_bounces()
    _hr()
    print(f"  Replies recorded:    {result['replies_recorded']}")
    print(f"  Bounces recorded:    {result['bounces_recorded']}")
    _hr()


def cmd_dashboard():
    from .dashboard.app import create_app
    from .tracker import poll_for_replies_and_bounces

    def _poll_loop():
        while True:
            time.sleep(30 * 60)  # 30 minutes
            try:
                print(f"[{_ts()}] [background] Polling for replies and bounces...")
                poll_for_replies_and_bounces()
            except Exception as exc:
                print(f"[{_ts()}] [background] Poll error: {exc}")

    poller = threading.Thread(target=_poll_loop, daemon=True)
    poller.start()

    print(f"[{_ts()}] Dashboard running at http://localhost:5000 - press Ctrl+C to stop")
    app = create_app()
    app.run(debug=False, host="0.0.0.0", port=5000)


def cmd_status():
    from .db import get_conn, get_daily_send_count
    from .warmup import warmup_status_report
    from .config import DAILY_SEND_LIMIT

    today = datetime.now().strftime("%Y-%m-%d")  # local date
    sent_today = get_daily_send_count(today)
    warmup = warmup_status_report()

    with get_conn() as conn:
        total_sent = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE status IN ('sent','replied')"
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE status = 'queued'"
        ).fetchone()[0]
        total_opened = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE opened = 1"
        ).fetchone()[0]
        total_replied = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE reply_received = 1"
        ).fetchone()[0]
        bounced = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE status = 'bounced'"
        ).fetchone()[0]

    open_rate  = round(total_opened  / total_sent * 100, 1) if total_sent else 0.0
    reply_rate = round(total_replied / total_sent * 100, 1) if total_sent else 0.0

    _hr()
    print(f"  Beauty Outreach -Status Report  ({today})")
    _hr()
    print(f"  Warm-up day:      {warmup['current_day']} / 28"
          + ("  (complete) Complete" if warmup["is_complete"] else ""))
    print(f"  Sent today:       {sent_today} / {DAILY_SEND_LIMIT}")
    print(f"  Total sent:       {total_sent}")
    print(f"  Queued:           {queued}")
    print(f"  Bounced:          {bounced}")
    print(f"  Open rate:        {open_rate}%  ({total_opened} opens)")
    print(f"  Reply rate:       {reply_rate}%  ({total_replied} replies)")
    _hr()


def _print_help():
    _hr()
    print("  Beauty Outreach -Available Commands")
    _hr()
    cmds = [
        ("setup",          "Initialise DB and complete Gmail OAuth authentication"),
        ("import <file>",  "Import contacts from an Excel file (safe to re-run)"),
        ("warmup",         "Send today's warm-up emails"),
        ("campaign",       "Send today's campaign emails (initial + follow-ups)"),
        ("poll",           "Check Gmail inbox for replies and bounce notifications"),
        ("dashboard",      "Start web dashboard at localhost:5000 with auto-polling"),
        ("status",         "Print a full status report to the console"),
    ]
    for cmd, desc in cmds:
        print(f"  python main.py {cmd:<18}  {desc}")
    _hr()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if not args:
        _print_help()
        sys.exit(0)

    command = args[0].lower()

    if command == "setup":
        cmd_setup()

    elif command == "import":
        if len(args) < 2:
            print("Usage: python main.py import <filepath>")
            sys.exit(1)
        cmd_import(args[1])

    elif command == "warmup":
        cmd_warmup()

    elif command == "campaign":
        cmd_campaign()

    elif command == "import-batch3":
        if len(args) < 2:
            print("Usage: python main.py import-batch3 <filepath>")
            sys.exit(1)
        cmd_import_batch3(args[1])

    elif command == "batch3":
        cmd_batch3()

    elif command == "batch3-status":
        cmd_batch3_status()

    elif command == "poll":
        cmd_poll()

    elif command == "dashboard":
        cmd_dashboard()

    elif command == "status":
        cmd_status()

    else:
        print(f"Unrecognised command: '{command}'")
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
