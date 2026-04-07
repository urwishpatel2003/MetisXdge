# data/db.py
"""
SQLite Signal Logger
Persists all EdgeSignals for backtesting, outcome tracking, and audit trail.
"""

import sqlite3
import json
import logging
from datetime import datetime
from core.models import EdgeSignal, SignalType, SignalStatus

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type     TEXT NOT NULL,
    status          TEXT DEFAULT 'open',
    kalshi_ticker   TEXT,
    kalshi_title    TEXT,
    side            TEXT,
    edge_pct        REAL,
    fair_prob       REAL,
    kalshi_prob     REAL,
    fair_odds       INTEGER,
    kalshi_odds     INTEGER,
    sharp_book      TEXT,
    sharp_odds      INTEGER,
    sport           TEXT,
    event           TEXT,
    stake_usd       REAL,
    kelly_fraction  REAL,
    legs_json       TEXT,
    notes           TEXT,
    created_at      TEXT,
    expires_at      TEXT,
    resolved_at     TEXT,
    outcome         TEXT
);

CREATE INDEX IF NOT EXISTS idx_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_type ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_created ON signals(created_at);
"""


class SignalDB:
    def __init__(self, db_path: str = "data/metisedge.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.info(f"DB initialized at {self.db_path}")

    def save_signal(self, signal: EdgeSignal) -> int:
        """Insert a new signal. Returns the row ID."""
        legs_json = json.dumps([
            {
                "ticker": leg.kalshi_ticker,
                "title": leg.kalshi_title,
                "side": leg.side,
                "price": leg.price,
                "fair_prob": leg.fair_prob,
                "sport": leg.sport,
                "event": leg.event,
            }
            for leg in (signal.legs or [])
        ])

        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO signals (
                    signal_type, status, kalshi_ticker, kalshi_title, side,
                    edge_pct, fair_prob, kalshi_prob, fair_odds, kalshi_odds,
                    sharp_book, sharp_odds, sport, event, stake_usd,
                    kelly_fraction, legs_json, notes, created_at, expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                signal.signal_type.value,
                signal.status.value,
                signal.kalshi_ticker,
                signal.kalshi_title,
                signal.side,
                signal.edge_pct,
                signal.fair_prob,
                signal.kalshi_prob,
                signal.fair_odds_american,
                signal.kalshi_odds_american,
                signal.sharp_book,
                signal.sharp_odds,
                signal.sport,
                signal.event,
                signal.suggested_stake_usd,
                signal.kelly_fraction,
                legs_json,
                signal.notes,
                signal.created_at.isoformat(),
                signal.expires_at.isoformat() if signal.expires_at else None,
            ))
            return cur.lastrowid

    def update_status(self, signal_id: int, status: SignalStatus, outcome: str = None):
        with self._connect() as conn:
            conn.execute(
                """UPDATE signals SET status=?, outcome=?, resolved_at=?
                   WHERE id=?""",
                (status.value, outcome, datetime.utcnow().isoformat(), signal_id)
            )

    def get_open_signals(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE status IN ('open','alerted') ORDER BY edge_pct DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_signal_stats(self) -> dict:
        """Aggregate stats for performance tracking"""
        with self._connect() as conn:
            stats = {}
            for signal_type in ["arb", "correlation", "stale_price"]:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END) as wins,
                        AVG(edge_pct) as avg_edge,
                        SUM(stake_usd) as total_staked
                    FROM signals
                    WHERE signal_type=? AND status IN ('won','lost')
                """, (signal_type,)).fetchone()
                stats[signal_type] = dict(row) if row else {}
        return stats

    def signal_exists(self, ticker: str, signal_type: SignalType, hours: int = 4) -> bool:
        """Check if a similar signal was already logged recently (dedup)"""
        with self._connect() as conn:
            row = conn.execute("""
                SELECT id FROM signals
                WHERE kalshi_ticker=? AND signal_type=?
                AND created_at > datetime('now', ? || ' hours')
                LIMIT 1
            """, (ticker, signal_type.value, f"-{hours}")).fetchone()
        return row is not None
