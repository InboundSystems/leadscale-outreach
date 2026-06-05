# Send limits
DAILY_SEND_LIMIT = 80
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

# Warm-up seed emails — replace with real addresses that will open and reply
WARMUP_SEED_EMAILS = [
    "admin@leadscalesystems.net",
    "samhindmarsh101@gmail.com",
]

# Paths
DB_PATH = "beauty_outreach.db"
CREDENTIALS_PATH = "credentials.json"
TOKEN_PATH = "token.json"

# Tracking pixel base URL (set to your deployed domain)
TRACKING_BASE_URL = "http://localhost:5000"

# Flask secret key
SECRET_KEY = "change-me-in-production"
