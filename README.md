# Beauty Outreach

Automated Gmail outreach system with warm-up engine, 4-step follow-up sequences, open/reply tracking, and a web dashboard.

---

## Initial Setup

**1. Install dependencies**
```
pip install -r requirements.txt
```

**2. Enable Gmail API and download credentials**

Go to Google Cloud Console and enable the Gmail API:
https://console.cloud.google.com/apis/library/gmail.googleapis.com

Create an OAuth 2.0 Desktop App credential, download the JSON file, and save it as `credentials.json` in the project root folder.

**3. Run setup**
```
python main.py setup
```
This creates the database, runs through the Gmail OAuth browser flow, and saves `token.json` for future runs.

**4. Add warm-up seed emails**

Open `beauty_outreach/config.py` and add at least 2 real email addresses you control to `WARMUP_SEED_EMAILS`. These addresses need to receive and reply to warm-up emails — use a personal Gmail, a colleague's address, or a second account you own.

```python
WARMUP_SEED_EMAILS = [
    "your-personal@gmail.com",
    "colleague@example.com",
]
```

---

## Importing Your Contacts

The Excel file must contain sheets named exactly:
- `2 - Email + Phone`
- `3 - Email Only`

Each sheet must have:
- Column 2 — Business Name
- Column 4 — Email address
- Column 12 — Email subject line
- Column 13 — Email body

Row 1 is a section title, row 2 is headers — data starts from row 3.

Run once per batch file:
```
python main.py import leads_batch1_new_emails.xlsx
python main.py import leads_batch2_new_emails.xlsx
```

Duplicates are skipped automatically — safe to re-run with the same file.

---

## Before Going Live

Open `beauty_outreach/campaign.py` and fill in your three follow-up email templates. Each is marked with a comment:

```python
# EDIT THIS BEFORE GOING LIVE
FOLLOWUP_1_SUBJECT = "Re: [REPLACE WITH YOUR SUBJECT LINE]"
FOLLOWUP_1_BODY = """
[PASTE YOUR FOLLOW-UP 1 EMAIL HERE]
...
"""
```

Use `[first_name]` anywhere in the subject or body — it will be replaced with the first word of the business name at send time.

---

## Daily Workflow

**Option A — Manual (run each morning):**
```
python main.py campaign
python main.py poll
```

If warm-up is not yet complete, run `warmup` instead of `campaign`:
```
python main.py warmup
python main.py poll
```

**Option B — Automated via Windows Task Scheduler:**

Create a task to run at 8:30am daily:
```
python C:\path\to\beauty-outreach\main.py campaign
```

Create a second task to run every 2 hours:
```
python C:\path\to\beauty-outreach\main.py poll
```

---

## Viewing Your Dashboard

```
python main.py dashboard
```

Open http://localhost:5000 in your browser.

The dashboard auto-refreshes stats every 60 seconds and polls Gmail for replies and bounces every 30 minutes in the background.

---

## DNS Records (important before going live)

Add these three DNS records to your sending domain before starting outreach. Without them, emails are far more likely to land in spam.

**SPF** — tells receiving servers your domain is allowed to send email

**DKIM** — adds a cryptographic signature to prove emails aren't forged

**DMARC** — tells servers what to do if SPF/DKIM checks fail

Google's setup guides:
- SPF:   https://support.google.com/a/answer/33786
- DKIM:  https://support.google.com/a/answer/174124
- DMARC: https://support.google.com/a/answer/2466580

---

## Warm-up Schedule

The account is treated as starting at Day 15, reflecting prior sending history.

| Day | Daily send limit |
|-----|-----------------|
| 15 | 20 emails/day |
| 16–21 | 25 emails/day |
| 22+ | 40 emails/day — warm-up complete |

Campaign sends are disabled until warm-up reaches Day 22.

---

## All Commands

```
python main.py setup              Initialise DB and complete Gmail OAuth
python main.py import <file>      Import contacts from an Excel file
python main.py warmup             Send today's warm-up emails
python main.py campaign           Send today's campaign emails
python main.py poll               Check Gmail for replies and bounces
python main.py dashboard          Start web dashboard at localhost:5000
python main.py status             Print full status report to console
```
