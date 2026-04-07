# core/models.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    ARB = "arb"                     # Kalshi vs sharp book divergence
    CORRELATION = "correlation"     # Correlated leg combo
    STALE_PRICE = "stale_price"     # Sharp line moved, Kalshi hasn't


class SignalStatus(str, Enum):
    OPEN = "open"
    ALERTED = "alerted"
    ACTED = "acted"
    EXPIRED = "expired"
    WON = "won"
    LOST = "lost"


@dataclass
class OddsLine:
    """A single market line from any book"""
    book: str
    market_key: str           # e.g. "h2h", "spreads", "totals"
    sport: str
    home_team: str
    away_team: str
    outcome: str              # team name or "Over"/"Under"
    american_odds: int
    implied_prob: float       # vig-removed
    raw_prob: float           # with vig
    timestamp: datetime = field(default_factory=datetime.utcnow)
    point: float = None  # spread/total line value (e.g. -2.5, 8.5)


@dataclass
class KalshiContract:
    """A Kalshi prediction market contract"""
    ticker: str               # e.g. "KXMLB-23456-T"
    title: str
    yes_price: float          # 0-100 cents
    no_price: float
    yes_prob: float           # yes_price / 100
    volume: int
    open_interest: int
    close_time: datetime
    sport: Optional[str] = None
    mapped_outcome: Optional[str] = None   # matched to OddsLine outcome
    timestamp: datetime = field(default_factory=datetime.utcnow)
    point: float = None  # spread/total line value (e.g. -2.5, 8.5)


@dataclass
class EdgeSignal:
    """A detected mispricing opportunity"""
    signal_type: SignalType
    status: SignalStatus = SignalStatus.OPEN

    # Core edge metrics
    fair_prob: float = 0.0        # True probability (from sharp books)
    kalshi_prob: float = 0.0      # Kalshi's implied probability
    edge_pct: float = 0.0         # (fair_prob - kalshi_prob) / kalshi_prob * 100
    fair_odds_american: int = 0
    kalshi_odds_american: int = 0

    # Contract details
    kalshi_ticker: str = ""
    kalshi_title: str = ""
    side: str = "YES"             # YES or NO

    # Reference line
    sharp_book: str = ""
    sharp_odds: int = 0
    sport: str = ""
    event: str = ""

    # Sizing
    suggested_stake_usd: float = 0.0
    kelly_fraction: float = 0.0

    # Metadata
    id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    notes: str = ""

    # Correlation-specific
    legs: list = field(default_factory=list)     # For multi-leg correlation plays


@dataclass
class CorrelationLeg:
    """One leg of a correlation play"""
    kalshi_ticker: str
    kalshi_title: str
    side: str
    price: float
    fair_prob: float
    sport: str
    event: str
