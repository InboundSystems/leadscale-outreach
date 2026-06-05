import sys
sys.stdout.reconfigure(encoding='utf-8')
from beauty_outreach.db import get_conn

with get_conn() as conn:
    sample = conn.execute("""
        SELECT id, business_name, email, status, sequence_step, created_at, email_subject
        FROM contacts WHERE status='paused' LIMIT 5
    """).fetchall()
    for r in sample:
        print(f"  id={r['id']} | {r['business_name']} | {r['email']} | step={r['sequence_step']} | created={str(r['created_at'])[:10]}")
        print(f"    subject: {r['email_subject']}")
