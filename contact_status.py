import sys
sys.stdout.reconfigure(encoding='utf-8')
from beauty_outreach.db import get_conn

with get_conn() as conn:
    rows = conn.execute("""
        SELECT status, COUNT(*) as cnt
        FROM contacts
        GROUP BY status
        ORDER BY cnt DESC
    """).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

print(f"Total contacts in DB: {total}")
print()
for r in rows:
    print(f"  {r['status']:<15} {r['cnt']}")
