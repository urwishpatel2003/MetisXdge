# config/settings.py — MetisXdge Combo Scanner

import os

# ── API Keys ──────────────────────────────────────────────
ODDS_API_KEY    = os.getenv("ODDS_API_KEY",    "YOUR_ODDS_API_KEY")
KALSHI_KEY_ID      = os.getenv("KALSHI_KEY_ID",      "YOUR_KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY = os.getenv("KALSHI_PRIVATE_KEY", "")

# ── ntfy.sh ───────────────────────────────────────────────
NTFY_TOPIC    = os.getenv("NTFY_TOPIC",    "metisxdge-changeme123")
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "https://ntfy.sh")

# ── The Odds API ──────────────────────────────────────────
ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
SHARP_BOOKS   = ["pinnacle", "betfair"]

# ── Sports to scan ────────────────────────────────────────
TARGET_SPORTS = [
    "baseball_mlb",
    "basketball_nba",
    "basketball_ncaab",
    "americanfootball_nfl",
]

# ── Combo Scanner Thresholds ──────────────────────────────
MIN_ODDS_GAP  = 100    # American odds points gap (Kalshi payout - fair value)
MIN_EV        = 0.05   # Min EV per $1 wagered
MIN_FAIR_PROB = 0.60   # Only use legs where sharp prob > 60%
MAX_LEGS      = 4      # Max legs per combo

# ── Scheduler ─────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300  # 5 min — Kalshi rate limit is 10 req/s basic tier

# ── Database ──────────────────────────────────────────────
DB_PATH = "data/metisxdge.db"

# ── Logging ───────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = "logs/metisxdge.log"

# ── Network ───────────────────────────────────────────────
SSL_VERIFY = True
