import sys
sys.stdout.reconfigure(encoding='utf-8')
from beauty_outreach.sender import check_inbox_for_replies
from beauty_outreach.tracker import record_reply

replies = check_inbox_for_replies(since_hours=24*14)
print(f"Replies found: {len(replies)}")
for r in replies:
    print(f"  From: {r['sender_email']} | Subject: {r['subject']} | Received: {r['received_at']}")
    record_reply(r["sender_email"])
