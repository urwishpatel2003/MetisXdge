# scanners/combo_scanner.py
"""
MetisXdge — Core Combo Scanner
--------------------------------
Finds Kalshi multi-leg combos where the platform's implied payout
dramatically exceeds what the true combined probability justifies.

Exactly like the screenshot: 3 heavy favorite legs paying +419
when fair value is only +142. That gap IS the edge.

Flow:
  1. Pull all open Kalshi markets
  2. Match each to a sharp book line → get true probability
  3. Build all 2-4 leg combos from matched contracts
  4. For each combo: compute fair parlay odds vs Kalshi payout
  5. Alert when payout gap > threshold (e.g. +200 odds points)
"""

from itertools import combinations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

from core.fair_value import (
    american_to_implied_prob,
    implied_prob_to_american,
    remove_vig_two_way,
)
from core.models import KalshiContract, OddsLine

logger = logging.getLogger(__name__)


@dataclass
class ComboLeg:
    kalshi_ticker: str
    kalshi_title:  str
    side:          str        # "YES" or "NO"
    kalshi_price:  float      # 0-100 cents
    kalshi_prob:   float      # kalshi_price / 100
    fair_prob:     float      # sharp-derived true probability
    sharp_odds:    int        # American odds from sharp book
    sharp_book:    str
    sport:         str
    event:         str        # "Away @ Home"
    individual_edge_pct: float  # (fair_prob - kalshi_prob) / kalshi_prob * 100


@dataclass
class ComboSignal:
    legs:                list[ComboLeg]
    n_legs:              int

    # Probability math
    fair_combined_prob:   float   # product of each leg's fair_prob
    kalshi_combined_prob: float   # product of each leg's kalshi_prob

    # Odds
    fair_american:        int     # what fair value says this should pay
    kalshi_american:      int     # what Kalshi actually pays
    odds_gap:             int     # kalshi_american - fair_american (the edge)
    odds_gap_pct:         float   # gap as % of fair odds

    # EV
    ev_per_dollar:        float   # expected profit per $1 wagered
    edge_pct:             float   # (fair_prob - kalshi_prob) / kalshi_prob * 100

    # Metadata
    sports:               list[str]
    created_at:           datetime = field(default_factory=datetime.utcnow)
    signal_id:            Optional[str] = None


def kalshi_combo_payout(legs: list[ComboLeg]) -> int:
    """
    Compute what Kalshi actually pays for a multi-leg combo.
    Kalshi multiplies decimal payouts of each leg naively —
    it does NOT adjust for true combined probability.
    This is where the mispricing lives.

    For YES @ price p cents:
      decimal odds = 100 / p  (e.g. 25c YES = 4.0x = +300)
    Combined decimal = product of all leg decimals
    """
    combined_decimal = 1.0
    for leg in legs:
        if leg.side == "YES":
            leg_decimal = 100.0 / leg.kalshi_price
        else:
            leg_decimal = 100.0 / (100.0 - leg.kalshi_price)
        combined_decimal *= leg_decimal

    # Convert to American
    if combined_decimal >= 2.0:
        return round((combined_decimal - 1) * 100)
    else:
        return round(-100 / (combined_decimal - 1))


def fair_combo_odds(legs: list[ComboLeg]) -> tuple[float, int]:
    """
    Compute the true fair parlay odds from sharp probabilities.
    Returns (combined_probability, fair_american_odds)
    """
    combined_prob = 1.0
    for leg in legs:
        combined_prob *= leg.fair_prob

    if combined_prob <= 0 or combined_prob >= 1:
        return combined_prob, 0

    fair_american = implied_prob_to_american(combined_prob)
    return combined_prob, fair_american


def ev_per_dollar(fair_prob: float, kalshi_american: int) -> float:
    """
    Expected value per $1 wagered on the Kalshi combo.
    Positive = profitable long term.
    """
    if kalshi_american >= 0:
        profit_if_win = kalshi_american / 100
    else:
        profit_if_win = 100 / abs(kalshi_american)

    return (fair_prob * profit_if_win) - (1 - fair_prob)


def scan_combos(
    matched_legs:      list[ComboLeg],
    min_legs:          int   = 2,
    max_legs:          int   = 4,
    min_odds_gap:      int   = 50,    # minimum gap in American odds points
    min_ev:            float = 0.0,   # minimum EV per dollar (0 = any +EV)
    max_combos:        int   = 500,   # cap to avoid explosion on large slates
) -> list[ComboSignal]:
    """
    Build all n-leg combos from matched legs and find mispriced ones.

    A combo is flagged when:
      kalshi_american - fair_american > min_odds_gap
      AND ev_per_dollar > min_ev

    Args:
        matched_legs:  Legs that have been matched to sharp lines
        min_legs:      Minimum combo size
        max_legs:      Maximum combo size (4+ gets very high variance)
        min_odds_gap:  Minimum American odds gap to flag (50 = conservative)
        min_ev:        Minimum EV per dollar
        max_combos:    Safety cap on total combos evaluated

    Returns:
        List of ComboSignal sorted by odds_gap descending
    """
    signals = []
    evaluated = 0

    for n in range(min_legs, max_legs + 1):
        for combo in combinations(matched_legs, n):
            if evaluated >= max_combos:
                logger.warning(f"Hit max_combos cap ({max_combos}) — increase or filter input legs")
                break
            evaluated += 1

            legs = list(combo)

            # Skip combos with duplicate events (same game twice)
            events = [leg.event for leg in legs]
            if len(events) != len(set(events)):
                continue

            # Compute Kalshi payout (naive retail math)
            try:
                kalshi_american = kalshi_combo_payout(legs)
            except Exception:
                continue

            # Compute fair value from sharp probs
            fair_prob, fair_american = fair_combo_odds(legs)
            if fair_american == 0:
                continue

            # Compute the gap — this is the core signal
            odds_gap = kalshi_american - fair_american

            if odds_gap < min_odds_gap:
                continue

            # EV check
            ev = ev_per_dollar(fair_prob, kalshi_american)
            if ev < min_ev:
                continue

            # Odds gap as % of fair odds
            gap_pct = (odds_gap / abs(fair_american)) * 100 if fair_american != 0 else 0

            # Combined Kalshi prob (what Kalshi thinks you'll win)
            kalshi_combined = 1.0
            for leg in legs:
                kalshi_combined *= leg.kalshi_prob

            # Edge = how much more likely you are to win than Kalshi thinks
            edge_pct = (
                (fair_prob - kalshi_combined) / kalshi_combined * 100
                if kalshi_combined > 0 else 0
            )

            signal = ComboSignal(
                legs=legs,
                n_legs=n,
                fair_combined_prob=fair_prob,
                kalshi_combined_prob=kalshi_combined,
                fair_american=fair_american,
                kalshi_american=kalshi_american,
                odds_gap=odds_gap,
                odds_gap_pct=gap_pct,
                ev_per_dollar=ev,
                edge_pct=edge_pct,
                sports=list(set(leg.sport for leg in legs)),
            )
            signals.append(signal)

    # Sort by odds gap — biggest mispricing first
    signals.sort(key=lambda s: s.odds_gap, reverse=True)

    logger.info(
        f"Combo scan: {evaluated} evaluated | "
        f"{len(signals)} signals found | "
        f"Best gap: {signals[0].odds_gap if signals else 0:+d} pts"
    )
    return signals


def match_legs(
    contracts: list[KalshiContract],
    lines:     list[OddsLine],
    min_fair_prob: float = 0.60,   # Only include legs where fair prob > 60% (favorites)
) -> list[ComboLeg]:
    """
    Match Kalshi contracts to sharp book lines.
    Only keeps legs where a sharp line match is found AND
    fair probability exceeds min_fair_prob (heavy favorites).

    Heavy favorites are the sweet spot — Kalshi underprices
    their parlay combinations the most aggressively.
    """
    from scanners.arb_scanner import team_in_title
    matched = []

    for contract in contracts:
        for line in lines:
            if not team_in_title(line.outcome, contract.title):
                continue

            fair_prob = line.implied_prob
            if fair_prob < min_fair_prob:
                continue   # Skip underdogs — not the target market

            kalshi_price = contract.yes_price
            kalshi_prob  = kalshi_price / 100.0

            individual_edge = (
                (fair_prob - kalshi_prob) / kalshi_prob * 100
                if kalshi_prob > 0 else 0
            )

            matched.append(ComboLeg(
                kalshi_ticker=contract.ticker,
                kalshi_title=contract.title,
                side="YES",
                kalshi_price=kalshi_price,
                kalshi_prob=kalshi_prob,
                fair_prob=fair_prob,
                sharp_odds=line.american_odds,
                sharp_book=line.book,
                sport=line.sport,
                event=f"{line.away_team} @ {line.home_team}",
                individual_edge_pct=individual_edge,
            ))
            break  # one match per contract

    logger.info(
        f"Matched {len(matched)} legs from "
        f"{len(contracts)} contracts / {len(lines)} lines "
        f"(min_fair_prob={min_fair_prob:.0%})"
    )
    return matched
