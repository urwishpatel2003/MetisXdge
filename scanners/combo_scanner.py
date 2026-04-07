# scanners/combo_scanner.py
"""
MetisXdge — Universal Combo Scanner
Handles all Kalshi market types:
  - Moneyline: "yes Kansas City"
  - Spread NO: "no Cleveland wins by over 2.5 runs"
  - Spread YES: "yes Oklahoma City wins by over 2.5 Points"
  - Totals: "yes Over 8.5 runs scored"
Matches each leg to sharp lines, builds 2-4 leg combos,
flags where Kalshi payout >> fair combined probability.
"""

import re
import logging
from itertools import combinations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from difflib import SequenceMatcher

from core.fair_value import implied_prob_to_american
from core.models import KalshiContract, OddsLine

logger = logging.getLogger(__name__)


# ── Leg parsing ────────────────────────────────────────────────────────────────

# Patterns for each market type
RE_SPREAD = re.compile(
    r'^(yes|no)\s+(.+?)\s+wins by over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
    re.IGNORECASE
)
RE_TOTAL = re.compile(
    r'^(yes|no)\s+(over|under)\s+([\d.]+)\s*(runs?|points?|goals?)?\s*(scored)?$',
    re.IGNORECASE
)
RE_MONEYLINE = re.compile(
    r'^yes\s+([A-Za-z][A-Za-z\s\.\-]{2,28})$',
    re.IGNORECASE
)

# Skip player props
PROP_PATTERN = re.compile(
    r':\s*\d+\+|\d+\+\s*assists|\d+\+\s*rebounds|strikeout|home run|'
    r'election|president|bitcoin|ethereum|weather|oscar|emmy|grammy|'
    r'fed rate|gdp|inflation|tournament winner|season|award',
    re.IGNORECASE
)


def parse_leg(part: str) -> Optional[dict]:
    """
    Parse a single Kalshi leg string into structured fields.
    Returns dict with keys: side, market_type, team, line, raw
    or None if unparseable/prop.
    """
    part = part.strip()
    if not part:
        return None
    if PROP_PATTERN.search(part):
        return None
    if re.search(r'\d+\+', part):  # player prop
        return None

    # Spread: "no Cleveland wins by over 2.5 runs"
    m = RE_SPREAD.match(part)
    if m:
        return {
            "side":        m.group(1).lower(),
            "market_type": "spread",
            "team":        m.group(2).strip(),
            "line":        float(m.group(3)),
            "raw":         part,
        }

    # Total: "yes Over 8.5 runs scored"
    m = RE_TOTAL.match(part)
    if m:
        return {
            "side":        m.group(1).lower(),
            "market_type": "total",
            "direction":   m.group(2).lower(),
            "line":        float(m.group(3)),
            "raw":         part,
        }

    # Moneyline: "yes Kansas City"
    m = RE_MONEYLINE.match(part)
    if m:
        team = m.group(1).strip()
        if re.search(r'\d', team):  # has numbers = prop
            return None
        if len(team) < 3 or len(team) > 30:
            return None
        return {
            "side":        "yes",
            "market_type": "moneyline",
            "team":        team,
            "raw":         part,
        }

    return None


def extract_legs_from_title(title: str, yes_price: float) -> list[dict]:
    """
    Split a Kalshi combo title by comma and parse each part.
    Each leg inherits the contract's yes_price.
    """
    legs = []
    parts = title.split(",")
    for part in parts:
        parsed = parse_leg(part.strip())
        if parsed:
            parsed["kalshi_price"] = yes_price if parsed["side"] == "yes" else (100 - yes_price)
            legs.append(parsed)
    return legs


# ── Matching ───────────────────────────────────────────────────────────────────

def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def team_sim(team: str, outcome: str) -> float:
    team_l    = team.lower()
    outcome_l = outcome.lower()
    words     = [w for w in team_l.split() if len(w) > 3]
    word_hit  = any(w in outcome_l for w in words)
    return max(sim(team_l, outcome_l), 0.7 if word_hit else 0.0)


def find_best_line(parsed_leg: dict, lines: list[OddsLine]) -> Optional[OddsLine]:
    """Find the best matching sharp line for a parsed leg."""
    best, best_score = None, 0.3  # min threshold

    for line in lines:
        if parsed_leg["market_type"] == "moneyline" and line.market_key == "h2h":
            score = team_sim(parsed_leg["team"], line.outcome)

        elif parsed_leg["market_type"] == "spread" and line.market_key == "spreads":
            score = team_sim(parsed_leg["team"], line.outcome)
            # Check spread line is close
            if score > 0.3 and hasattr(line, "point") and line.point:
                if abs(line.point - parsed_leg["line"]) > 2:
                    score *= 0.5

        elif parsed_leg["market_type"] == "total" and line.market_key == "totals":
            direction = parsed_leg.get("direction", "over")
            if direction.lower() in line.outcome.lower():
                score = 0.8
            else:
                score = 0.0

        else:
            continue

        if score > best_score:
            best_score = score
            best       = line

    return best


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ComboLeg:
    kalshi_ticker:  str
    kalshi_title:   str
    display_name:   str      # clean human-readable label
    side:           str      # "yes" or "no"
    market_type:    str      # moneyline / spread / total
    kalshi_price:   float    # price in cents (0-100)
    kalshi_prob:    float
    fair_prob:      float
    sharp_odds:     int
    sharp_book:     str
    sport:          str
    event:          str
    edge_pct:       float


@dataclass
class ComboSignal:
    legs:                 list
    n_legs:               int
    fair_combined_prob:   float
    kalshi_combined_prob: float
    fair_american:        int
    kalshi_american:      int
    odds_gap:             int
    odds_gap_pct:         float
    ev_per_dollar:        float
    edge_pct:             float
    sports:               list
    created_at:           datetime = field(default_factory=datetime.utcnow)
    signal_id:            Optional[str] = None


# ── Core functions ─────────────────────────────────────────────────────────────

def match_legs(
    contracts:     list[KalshiContract],
    lines:         list[OddsLine],
    min_fair_prob: float = 0.55,
) -> list[ComboLeg]:
    matched   = []
    seen_keys = set()

    for contract in contracts:
        parsed_legs = extract_legs_from_title(contract.title, contract.yes_price)

        for pl in parsed_legs:
            key = (contract.ticker, pl["raw"].lower())
            if key in seen_keys:
                continue

            line = find_best_line(pl, lines)
            if not line:
                continue

            # For NO side, flip the probability
            fair_prob = line.implied_prob
            if pl["side"] == "no":
                fair_prob = 1 - fair_prob

            if fair_prob < min_fair_prob:
                continue

            # Filter out illiquid/outlier prices
            # Valid range: 20c-80c (outside this = either illiquid or near-settled)
            if kalshi_price < 20 or kalshi_price > 80:
                continue

            kalshi_price = pl["kalshi_price"]
            kalshi_prob  = kalshi_price / 100.0
            edge         = (fair_prob - kalshi_prob) / kalshi_prob * 100 if kalshi_prob > 0 else 0

            # Build display name with sport context
            mt = pl["market_type"]
            sport_short = {
                "baseball_mlb": "MLB",
                "basketball_nba": "NBA",
                "basketball_ncaab": "NCAAB",
                "americanfootball_nfl": "NFL",
                "americanfootball_ncaaf": "NCAAF",
                "icehockey_nhl": "NHL",
                "soccer_epl": "EPL",
                "soccer_uefa_champs_league": "UCL",
                "soccer_usa_mls": "MLS",
            }.get(line.sport, line.sport.split("_")[-1].upper())

            if mt == "moneyline":
                display = f"{pl['team']} ML ({sport_short})"
            elif mt == "spread":
                display = f"NO {pl['team']} -{pl['line']} ({sport_short})" if pl["side"] == "no" else f"{pl['team']} -{pl['line']} ({sport_short})"
            elif mt == "total":
                display = f"{pl.get('direction','over').title()} {pl['line']} ({sport_short})"
            else:
                display = pl["raw"][:30]

            seen_keys.add(key)
            matched.append(ComboLeg(
                kalshi_ticker = contract.ticker,
                kalshi_title  = contract.title,
                display_name  = display,
                side          = pl["side"],
                market_type   = mt,
                kalshi_price  = kalshi_price,
                kalshi_prob   = kalshi_prob,
                fair_prob     = fair_prob,
                sharp_odds    = line.american_odds,
                sharp_book    = line.book,
                sport         = line.sport,
                event         = f"{line.away_team} @ {line.home_team}",
                edge_pct      = edge,
            ))

    logger.info(f"Matched {len(matched)} legs ({len(contracts)} contracts / {len(lines)} lines)")
    for leg in matched[:8]:
        logger.info(f"  LEG: {leg.display_name} | {leg.kalshi_price:.0f}c | Fair {leg.fair_prob:.1%} | {leg.event}")
    return matched


def kalshi_combo_payout(legs: list) -> int:
    dec = 1.0
    for leg in legs:
        dec *= 100.0 / leg.kalshi_price
    return round((dec - 1) * 100) if dec >= 2.0 else round(-100 / (dec - 1))


def fair_combo_odds(legs: list) -> tuple:
    prob = 1.0
    for leg in legs:
        prob *= leg.fair_prob
    if prob <= 0 or prob >= 1:
        return prob, 0
    return prob, implied_prob_to_american(prob)


def ev_per_dollar(fair_prob: float, kalshi_american: int) -> float:
    profit = kalshi_american / 100 if kalshi_american >= 0 else 100 / abs(kalshi_american)
    return (fair_prob * profit) - (1 - fair_prob)


def scan_combos(
    matched_legs: list,
    min_legs:     int   = 2,
    max_legs:     int   = 4,
    min_odds_gap: int   = 50,
    min_ev:       float = 0.0,
    max_combos:   int   = 2000,
) -> list:
    signals   = []
    evaluated = 0

    for n in range(min_legs, max_legs + 1):
        for combo in combinations(matched_legs, n):
            if evaluated >= max_combos:
                break
            evaluated += 1
            legs = list(combo)

            # No duplicate events
            if len(set(leg.event for leg in legs)) != n:
                continue

            try:
                k_american = kalshi_combo_payout(legs)
            except Exception:
                continue

            fair_prob, f_american = fair_combo_odds(legs)
            if f_american == 0:
                continue

            gap = k_american - f_american
            if gap < min_odds_gap:
                continue

            ev = ev_per_dollar(fair_prob, k_american)
            if ev < min_ev:
                continue

            k_combined = 1.0
            for leg in legs:
                k_combined *= leg.kalshi_prob

            signals.append(ComboSignal(
                legs=legs, n_legs=n,
                fair_combined_prob=fair_prob,
                kalshi_combined_prob=k_combined,
                fair_american=f_american,
                kalshi_american=k_american,
                odds_gap=gap,
                odds_gap_pct=(gap / abs(f_american) * 100) if f_american != 0 else 0,
                ev_per_dollar=ev,
                edge_pct=(fair_prob - k_combined) / k_combined * 100 if k_combined > 0 else 0,
                sports=list(set(leg.sport for leg in legs)),
            ))

    signals.sort(key=lambda s: s.odds_gap, reverse=True)
    logger.info(
        f"Combo scan: {evaluated} evaluated | {len(signals)} signals | "
        f"Best gap: {signals[0].odds_gap if signals else 0:+d} pts"
    )
    return signals
