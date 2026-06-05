import os
from datetime import date

# Send limits — 35/day during reputation recovery, auto-bumps to 80 from 2026-07-15
DAILY_SEND_LIMIT = 80 if date.today() >= date(2026, 7, 15) else 35
SEND_WINDOW_START = 8   # 8 AM local time
SEND_WINDOW_END = 17    # 5 PM local time
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 180

# Follow-up timing (days after previous step before next follow-up is sent)
FOLLOWUP_DELAYS = [3, 7, 14]

# Warm-up settings
WARMUP_ALREADY_SENT = 65
WARMUP_ALREADY_RECEIVED = 50
WARMUP_START_DAY = 15

# Warm-up seed emails
WARMUP_SEED_EMAILS = [
    "admin@leadscalesystems.net",
    "samhindmarsh101@gmail.com",
]

# Paths — override via env vars in production
DB_PATH          = os.environ.get("DB_PATH",          "beauty_outreach.db")
CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH       = os.environ.get("TOKEN_PATH",       "token.json")

# Tracking pixel base URL — must be a real public URL in production
TRACKING_BASE_URL = os.environ.get("TRACKING_BASE_URL", "https://leadscale-outreach.onrender.com")

# Flask secret key — always set this in production
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
