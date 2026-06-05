import sys
sys.stdout.reconfigure(encoding='utf-8')
from beauty_outreach.db import get_conn
from beauty_outreach.campaign import get_contacts_due_for_followup, _FOLLOWUP_MAP, _days_since

with get_conn() as conn:
    rows = conn.execute("""
        SELECT sequence_step, COUNT(*) as cnt, MIN(last_sent_at) as oldest, MAX(last_sent_at) as newest
        FROM contacts
        WHERE status = 'sent' AND reply_received = 0 AND sequence_step IN (1,2,3)
        GROUP BY sequence_step
        ORDER BY sequence_step
    """).fetchall()

    print("Contacts by follow-up step (all, not just due):")
    for r in rows:
        delay_days = _FOLLOWUP_MAP[r["sequence_step"]][0]
        print(f"  Step {r['sequence_step']} (needs {delay_days}d gap): {r['cnt']} contacts | oldest last sent: {str(r['oldest'])[:10] if r['oldest'] else 'N/A'} | newest last sent: {str(r['newest'])[:10] if r['newest'] else 'N/A'}")

    print()

    due = get_contacts_due_for_followup()
    overdue_by_step = {}
    for c in due:
        step = c["sequence_step"]
        days_waited = _days_since(c["last_sent_at"])
        delay_req = _FOLLOWUP_MAP[step][0]
        overdue_days = days_waited - delay_req
        overdue_by_step.setdefault(step, []).append(overdue_days)

    print("Follow-ups currently due:")
    for step, overdue_list in sorted(overdue_by_step.items()):
        avg_overdue = sum(overdue_list) / len(overdue_list)
        max_overdue = max(overdue_list)
        print(f"  Step {step}: {len(overdue_list)} due | avg {avg_overdue:.1f}d past due | max {max_overdue:.1f}d past due")

    print()

    total_initial = conn.execute("SELECT COUNT(*) FROM contacts WHERE sequence_step >= 1").fetchone()[0]
    total_queued = conn.execute("SELECT COUNT(*) FROM contacts WHERE status = 'queued'").fetchone()[0]
    total_replied = conn.execute("SELECT COUNT(*) FROM contacts WHERE reply_received = 1").fetchone()[0]
    total_done = conn.execute("SELECT COUNT(*) FROM contacts WHERE sequence_step = 4").fetchone()[0]
    print(f"Total initial emails ever sent: {total_initial}")
    print(f"Still queued (no initial yet):  {total_queued}")
    print(f"Replied:                        {total_replied}")
    print(f"Completed all 3 follow-ups:     {total_done}")
