import sys
sys.stdout.reconfigure(encoding='utf-8')
from beauty_outreach.db import get_conn
from datetime import datetime, timedelta

with get_conn() as conn:
    rows = conn.execute("""
        SELECT DATE(last_sent_at) as send_date, COUNT(*) as cnt
        FROM contacts
        WHERE sequence_step = 3 AND status = 'sent'
        GROUP BY DATE(last_sent_at)
        ORDER BY send_date
    """).fetchall()

print("Step-3 contacts by date step-2 was sent:")
total = 0
for r in rows:
    due_date = (datetime.strptime(r["send_date"], "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d")
    print(f"  Step-2 sent {r['send_date']}: {r['cnt']} contacts  ->  step-3 due {due_date}")
    total += r["cnt"]
print(f"  Total: {total}")
