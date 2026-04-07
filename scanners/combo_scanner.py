# scanners/combo_scanner.py
"""
MetisXdge — Combo Scanner (Revised)
Targets individual team-winner legs only.
Builds 2-4 leg combos and finds where Kalshi payout >> fair value.
"""

from itertools import combinations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging
import re

from core.fair_value import implied_prob_to_american
from core.models import KalshiContract, OddsLine

logger = logging.getLogger(__name__)

# Keywords that indicate a market is NOT a simple game winner
EXCLUDE_KEYWORDS = [
    "wins by over", "wins by under",
    "over ", "under ",
    "points scored", "runs scored",
    ": 1+", ": 2+", ": 3+", ": 5+", ": 10+", ": 15+", ": 20+", ": 25+",
    "rebounds", "assists", "strikeouts", "hits", "home run",
    "both teams", "first half", "first quarter",
    "tournament", "championship", "series",
    "to win the", "season", "playoff",
    "election", "president", "fed rate", "gdp", "inflation",
    "bitcoin", "ethereum", "crypto",
    "oscar", "emmy", "grammy",
    "weather", "temperature",
]

# A valid game winner leg has a short team name as the main outcome
TEAM_PATTERN = re.compile(
    r"^yes ([\w\s]{3,25})$",  # "yes Kansas City", "yes Golden State"
    re.IGNORECASE
)


def is_game_winner(title: str) -> tuple[bool, str]:
    """
    Returns (is_valid, team_name) for a Kalshi contract title.
    Valid = single team moneyline winner market.
    """
    title = title.strip()

    # Must start with "yes"
    if not title.lower().startswith("yes "):
        return False, ""

    # Must not contain exclude keywords
    title_lower = title.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in title_lower:
            return False, ""

    # Match pattern: "yes TeamName" with no commas (not a combo market)
    if "," in title:
        return False, ""

    # Extract team name
    m = TEAM_PATTERN.match(title)
    if not m:
        return False, ""

    team = m.group(1).strip()
    if len(team) < 3:
        return False, ""

    return True, team


@dataclass
class ComboLeg:
    kalshi_ticker:       str
    kalshi_title:        str
    team_name:           str
    side:                str
    kalshi_price:        float
    kalshi_prob:         float
    fair_prob:           float
    sharp_odds:          int
    sharp_book:          str
    sport:               str
    event:               str
    individual_edge_pct: float


@dataclass
class ComboSignal:
    legs:                 list[ComboLeg]
    n_legs:               int
    fair_combined_prob:   float
    kalshi_combined_prob: float
    fair_american:        int
    kalshi_american:      int
    odds_gap:             int
    odds_gap_pct:         float
    ev_per_dollar:        float
    edge_pct:             float
    sports:               list[str]
    created_at:           datetime = field(default_factory=datetime.utcnow)
    signal_id:            Optional[str] = None


def kalshi_combo_payout(legs: list[ComboLeg]) -> int:
    combined_decimal = 1.0
    for leg in legs:
        leg_decimal = 100.0 / leg.kalshi_price
        combined_decimal *= leg_decimal
    if combined_decimal >= 2.0:
        return round((combined_decimal - 1) * 100)
    else:
        return round(-100 / (combined_decimal - 1))


def fair_combo_odds(legs: list[ComboLeg]) -> tuple[float, int]:
    combined_prob = 1.0
    for leg in legs:
        combined_prob *= leg.fair_prob
    if combined_prob <= 0 or combined_prob >= 1:
        return combined_prob, 0
    return combined_prob, implied_prob_to_american(combined_prob)


def ev_per_dollar(fair_prob: float, kalshi_american: int) -> float:
    if kalshi_american >= 0:
        profit_if_win = kalshi_american / 100
    else:
        profit_if_win = 100 / abs(kalshi_american)
    return (fair_prob * profit_if_win) - (1 - fair_prob)


def match_legs(
    contracts: list[KalshiContract],
    lines:     list[OddsLine],
    min_fair_prob: float = 0.60,
) -> list[ComboLeg]:
    """
    Match only clean game-winner Kalshi contracts to sharp lines.
    Filters out player props, combo markets, and spread/total markets.
    """
    from difflib import SequenceMatcher

    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def team_matches(team: str, outcome: str) -> bool:
        team    = team.lower().strip()
        outcome = outcome.lower().strip()
        # Check if any word from team name appears in outcome
        parts = [p for p in team.split() if len(p) > 3]
        for part in parts:
            if part in outcome:
                return True
        return similarity(team, outcome) > 0.6

    matched = []
    seen_tickers = set()

    for contract in contracts:
        if contract.ticker in seen_tickers:
            continue

        valid, team_name = is_game_winner(contract.title)
        if not valid:
            continue

        # Find best matching sharp line
        best_line  = None
        best_score = 0.0

        for line in lines:
            if line.implied_prob < min_fair_prob:
                continue
            score = 0.0
            if team_matches(team_name, line.outcome):
                score = similarity(team_name, line.outcome)
            if score > best_score:
                best_score = score
                best_line  = line

        if not best_line or best_score < 0.4:
            continue

        kalshi_price = contract.yes_price
        kalshi_prob  = kalshi_price / 100.0
        fair_prob    = best_line.implied_prob
        edge         = (fair_prob - kalshi_prob) / kalshi_prob * 100 if kalshi_prob > 0 else 0

        matched.append(ComboLeg(
            kalshi_ticker       = contract.ticker,
            kalshi_title        = contract.title,
            team_name           = team_name,
            side                = "YES",
            kalshi_price        = kalshi_price,
            kalshi_prob         = kalshi_prob,
            fair_prob           = fair_prob,
            sharp_odds          = best_line.american_odds,
            sharp_book          = best_line.book,
            sport               = best_line.sport,
            event               = f"{best_line.away_team} @ {best_line.home_team}",
            individual_edge_pct = edge,
        ))
        seen_tickers.add(contract.ticker)

    logger.info(
        f"Matched {len(matched)} game-winner legs from "
        f"{len(contracts)} contracts / {len(lines)} lines"
    )
    return matched


def scan_combos(
    matched_legs: list[ComboLeg],
    min_legs:     int   = 2,
    max_legs:     int   = 4,
    min_odds_gap: int   = 50,
    min_ev:       float = 0.0,
    max_combos:   int   = 2000,
) -> list[ComboSignal]:
    signals   = []
    evaluated = 0

    for n in range(min_legs, max_legs + 1):
        for combo in combinations(matched_legs, n):
            if evaluated >= max_combos:
                break
            evaluated += 1

            legs = list(combo)

            # No duplicate events
            events = [leg.event for leg in legs]
            if len(events) != len(set(events)):
                continue

            try:
                kalshi_american = kalshi_combo_payout(legs)
            except Exception:
                continue

            fair_prob, fair_american = fair_combo_odds(legs)
            if fair_american == 0:
                continue

            odds_gap = kalshi_american - fair_american
            if odds_gap < min_odds_gap:
                continue

            ev = ev_per_dollar(fair_prob, kalshi_american)
            if ev < min_ev:
                continue

            gap_pct = (odds_gap / abs(fair_american)) * 100 if fair_american != 0 else 0

            kalshi_combined = 1.0
            for leg in legs:
                kalshi_combined *= leg.kalshi_prob

            edge_pct = (
                (fair_prob - kalshi_combined) / kalshi_combined * 100
                if kalshi_combined > 0 else 0
            )

            signals.append(ComboSignal(
                legs=legs, n_legs=n,
                fair_combined_prob=fair_prob,
                kalshi_combined_prob=kalshi_combined,
                fair_american=fair_american,
                kalshi_american=kalshi_american,
                odds_gap=odds_gap,
                odds_gap_pct=gap_pct,
                ev_per_dollar=ev,
                edge_pct=edge_pct,
                sports=list(set(leg.sport for leg in legs)),
            ))

    signals.sort(key=lambda s: s.odds_gap, reverse=True)

    logger.info(
        f"Combo scan: {evaluated} evaluated | "
        f"{len(signals)} signals found | "
        f"Best gap: {signals[0].odds_gap if signals else 0:+d} pts"
    )
    return signals
