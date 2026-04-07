# config/settings.py — MetisXdge Combo Scanner

import os

# ── API Keys ──────────────────────────────────────────────
ODDS_API_KEY       = os.getenv("ODDS_API_KEY",       "YOUR_ODDS_API_KEY")
KALSHI_KEY_ID      = os.getenv("KALSHI_KEY_ID",      "YOUR_KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY = os.getenv("KALSHI_PRIVATE_KEY", "")

# ── ntfy.sh ───────────────────────────────────────────────
NTFY_TOPIC    = os.getenv("NTFY_TOPIC",    "metisxdge-changeme123")
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "https://ntfy.sh")

# ── The Odds API ──────────────────────────────────────────
ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
SHARP_BOOKS   = ["pinnacle", "betfair"]

# All active sports — scanner will skip any returning 0 lines
TARGET_SPORTS = [
    "baseball_mlb",
    "basketball_nba",
    "americanfootball_nfl",
    "icehockey_nhl",
    "soccer_epl",
    "soccer_uefa_champs_league",
    "soccer_usa_mls",
    "tennis_atp_french_open",
    "basketball_ncaab",
    "americanfootball_ncaaf",
]

# Fetch all market types — h2h, spreads, totals
TARGET_MARKETS = "h2h,spreads,totals"

# ── Combo Scanner Thresholds ──────────────────────────────
MIN_ODDS_GAP  = 50     # Lower threshold — cast wide net
MIN_EV        = 0.05   # Min EV per $1 wagered
MIN_FAIR_PROB = 0.55   # Min fair probability per leg
MAX_LEGS      = 4

# ── Scheduler ─────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300

# ── Database ──────────────────────────────────────────────
DB_PATH = "data/metisxdge.db"

# ── Logging ───────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE  = "logs/metisxdge.log"

# ── Network ───────────────────────────────────────────────
SSL_VERIFY = True
