# scanners/combo_scanner.py
"""
MetisXdge — Alternate Run Line / Totals Scanner
------------------------------------------------
Targets the specific mispricing pattern from the original screenshot:
  - "No [team] wins by over X.5 runs/points" on Kalshi
  - Sharp books price the equivalent spread/total line precisely
  - Kalshi's retail math underprices these NO sides on favorites
  - Combining 2-4 of these creates compound overlay vs fair value

Example from screenshot:
  No Cleveland wins by over 2.5 runs → Kalshi prices at ~42c
  Sharp book: Cleveland -2.5 runs = -278 = 73.5% implied
  NO side fair prob = 1 - 0.735 = 26.5% → fair price 26.5c
  Kalshi overcharging for NO = edge on YES side? No...
  Actually Kalshi UNDERCHARGING on NO when the team is a big favorite
  and the line is set low (e.g. -1.5, -2.5).
"""

import re
import logging
from itertools import combinations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from difflib import SequenceMatcher

from core.fair_value import implied_prob_to_american, american_to_implied_prob
from core.models import KalshiContract, OddsLine

logger = logging.getLogger(__name__)

SPORT_SHORT = {
    'baseball_mlb': 'MLB',
    'basketball_nba': 'NBA',
    'basketball_ncaab': 'NCAAB',
    'americanfootball_nfl': 'NFL',
    'icehockey_nhl': 'NHL',
    'soccer_epl': 'EPL',
    'soccer_uefa_champs_league': 'UCL',
    'soccer_usa_mls': 'MLS',
}

# Parse "no [team] wins by over X.5 runs/points"
RE_NO_RUNLINE = re.compile(
    r'^no\s+(.+?)\s+wins by over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
    re.IGNORECASE
)
# Parse "yes [team] wins by over X.5 runs/points"
RE_YES_RUNLINE = re.compile(
    r'^yes\s+(.+?)\s+wins by over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
    re.IGNORECASE
)
# Parse "no over X.5 runs/points scored"
RE_NO_TOTAL = re.compile(
    r'^no\s+over\s+([\d.]+)\s*(runs?|points?|goals?)?\s*(scored)?$',
    re.IGNORECASE
)
# Parse "yes over X.5 runs/points scored"
RE_YES_TOTAL = re.compile(
    r'^yes\s+over\s+([\d.]+)\s*(runs?|points?|goals?)?\s*(scored)?$',
    re.IGNORECASE
)
# Simple moneyline "yes [team]"
RE_MONEYLINE = re.compile(
    r'^yes\s+([A-Za-z][A-Za-z\s\.\-]{2,25})$',
    re.IGNORECASE
)

REJECT = re.compile(
    r':\s*\d+\+|\d+\+|election|bitcoin|weather|oscar|fed rate|gdp|'
    r'season wins|tournament|award|both teams|first half|quarter',
    re.IGNORECASE
)


def parse_leg(part: str) -> Optional[dict]:
    """Parse a single Kalshi leg string."""
    part = part.strip()
    if not part or REJECT.search(part):
        return None

    # NO run line — the key target
    m = RE_NO_RUNLINE.match(part)
    if m:
        return {
            'type': 'no_runline',
            'side': 'no',
            'team': m.group(1).strip(),
            'line': float(m.group(2)),
            'raw': part,
        }

    # YES run line
    m = RE_YES_RUNLINE.match(part)
    if m:
        return {
            'type': 'yes_runline',
            'side': 'yes',
            'team': m.group(1).strip(),
            'line': float(m.group(2)),
            'raw': part,
        }

    # NO total
    m = RE_NO_TOTAL.match(part)
    if m:
        return {
            'type': 'no_total',
            'side': 'no',
            'line': float(m.group(1)),
            'raw': part,
        }

    # YES total
    m = RE_YES_TOTAL.match(part)
    if m:
        return {
            'type': 'yes_total',
            'side': 'yes',
            'line': float(m.group(1)),
            'raw': part,
        }

    # Moneyline
    m = RE_MONEYLINE.match(part)
    if m:
        team = m.group(1).strip()
        if re.search(r'\d', team):
            return None
        words = team.split()
        if len(words) == 2 and all(w[0].isupper() for w in words):
            return None  # person name
        return {
            'type': 'moneyline',
            'side': 'yes',
            'team': team,
            'raw': part,
        }

    return None


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_sharp_line(parsed: dict, lines: list[OddsLine]) -> Optional[tuple[OddsLine, float]]:
    """
    Find the matching sharp line and return (line, fair_prob).
    For NO runline: matches spread line, flips probability.
    For NO total: matches total line, flips probability.
    For moneyline: matches h2h.
    Returns (line, fair_prob) or None.
    """
    leg_type = parsed['type']

    if leg_type in ('no_runline', 'yes_runline'):
        team = parsed['team'].lower()
        target_line = parsed['line']
        best, best_score = None, 0.5

        for line in lines:
            if line.market_key != 'spreads':
                continue
            outcome_l = line.outcome.lower()
            team_words = [w for w in team.split() if len(w) > 3]
            word_match = any(w in outcome_l for w in team_words)
            score = max(sim(team, outcome_l), 0.7 if word_match else 0.0)

            # Check spread line proximity (Kalshi uses over X.5, spreads use -X.5)
            # e.g. "wins by over 2.5" corresponds to spread of -2.5
            if hasattr(line, 'point') and line.point:
                try:
                    if abs(abs(float(line.point)) - target_line) <= 1.5:
                        score += 0.1
                except:
                    pass

            if score > best_score:
                best_score = score
                best = line

        if not best:
            return None

        fair_prob = best.implied_prob
        if leg_type == 'no_runline':
            fair_prob = 1 - fair_prob  # NO side

        return best, fair_prob

    elif leg_type in ('no_total', 'yes_total'):
        target_line = parsed['line']
        best, best_score = None, 0.0

        for line in lines:
            if line.market_key != 'totals':
                continue
            # Match Over outcome
            if 'over' not in line.outcome.lower():
                continue
            # Check line proximity
            if hasattr(line, 'point') and line.point:
                try:
                    diff = abs(float(line.point) - target_line)
                    score = max(0, 1.0 - diff * 0.1)
                    if score > best_score:
                        best_score = score
                        best = line
                except:
                    pass

        if not best or best_score < 0.3:
            return None

        fair_prob = best.implied_prob
        if leg_type == 'no_total':
            fair_prob = 1 - fair_prob

        return best, fair_prob

    elif leg_type == 'moneyline':
        team = parsed['team'].lower()
        best, best_score = None, 0.6

        for line in lines:
            if line.market_key != 'h2h':
                continue
            outcome_l = line.outcome.lower()
            team_words = [w for w in team.split() if len(w) > 3]
            word_match = any(w in outcome_l for w in team_words)
            score = max(sim(team, outcome_l), 0.75 if word_match else 0.0)
            if score > best_score:
                best_score = score
                best = line

        if not best:
            return None
        return best, best.implied_prob

    return None


@dataclass
class ComboLeg:
    kalshi_ticker:  str
    display_name:   str
    leg_type:       str
    side:           str
    kalshi_price:   float
    kalshi_prob:    float
    fair_prob:      float
    fair_american:  int
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
    ev_per_dollar:        float
    edge_pct:             float
    sports:               list
    created_at:           datetime = field(default_factory=datetime.utcnow)
    signal_id:            Optional[str] = None


def match_legs(
    contracts:     list[KalshiContract],
    lines:         list[OddsLine],
    min_fair_prob: float = 0.40,
) -> list[ComboLeg]:
    matched     = []
    best_by_key = {}

    for contract in contracts:
        # Skip Kalshi pre-built combo packages
        if contract.ticker.startswith('KXMV'):
            continue

        # Price sanity — must be a real active market
        if contract.yes_price < 20 or contract.yes_price > 80:
            continue

        parts = contract.title.split(',')
        for part in parts:
            parsed = parse_leg(part.strip())
            if not parsed:
                continue

            result = find_sharp_line(parsed, lines)
            if not result:
                continue

            sharp_line, fair_prob = result

            if fair_prob < min_fair_prob or fair_prob > 0.85:
                continue

            # Fair American odds filter: -200 to +200 only
            fair_am = implied_prob_to_american(fair_prob)
            if fair_am < -200 or fair_am > 200:
                continue

            # Kalshi price for this side
            side = parsed['side']
            kalshi_price = contract.yes_price if side == 'yes' else (100 - contract.yes_price)

            if kalshi_price < 20 or kalshi_price > 80:
                continue

            kalshi_prob = kalshi_price / 100.0
            edge = (fair_prob - kalshi_prob) / kalshi_prob * 100

            # Build clean display name
            sport_s = SPORT_SHORT.get(sharp_line.sport, sharp_line.sport.upper())
            lt = parsed['type']
            if lt == 'no_runline':
                display = f"NO {parsed['team']} -{parsed['line']} ({sport_s})"
            elif lt == 'yes_runline':
                display = f"{parsed['team']} -{parsed['line']} ({sport_s})"
            elif lt == 'no_total':
                display = f"Under {parsed['line']} ({sport_s})"
            elif lt == 'yes_total':
                display = f"Over {parsed['line']} ({sport_s})"
            else:
                display = f"{parsed['team']} ML ({sport_s})"

            event = f"{sharp_line.away_team} @ {sharp_line.home_team}"
            key   = (display.lower(), event)

            leg = ComboLeg(
                kalshi_ticker = contract.ticker,
                display_name  = display,
                leg_type      = lt,
                side          = side,
                kalshi_price  = kalshi_price,
                kalshi_prob   = kalshi_prob,
                fair_prob     = fair_prob,
                fair_american = fair_am,
                sharp_odds    = sharp_line.american_odds,
                sharp_book    = sharp_line.book,
                sport         = sharp_line.sport,
                event         = event,
                edge_pct      = edge,
            )

            if key not in best_by_key or kalshi_price > best_by_key[key].kalshi_price:
                best_by_key[key] = leg

    matched = list(best_by_key.values())
    logger.info(f"Matched {len(matched)} legs ({len(contracts)} contracts / {len(lines)} lines)")

    # Log by type
    type_counts = {}
    for leg in matched:
        type_counts[leg.leg_type] = type_counts.get(leg.leg_type, 0) + 1
    logger.info(f"Leg types: {type_counts}")

    for leg in matched[:10]:
        logger.info(
            f"  {leg.display_name} | Kalshi {leg.kalshi_price:.0f}c | "
            f"Fair {leg.fair_prob:.1%} ({leg.fair_american:+d}) | {leg.event}"
        )
    return matched


def kalshi_combo_payout(legs: list) -> int:
    dec = 1.0
    for leg in legs:
        dec *= 100.0 / leg.kalshi_price
    if dec >= 2.0:
        return round((dec - 1) * 100)
    else:
        return round(-100 / (dec - 1))


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
    min_ev:       float = 0.05,
    max_combos:   int   = 5000,
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

            # No duplicate display names
            if len(set(leg.display_name for leg in legs)) != n:
                continue

            try:
                k_am = kalshi_combo_payout(legs)
            except Exception:
                continue

            # Sanity cap
            if k_am > 1500:
                continue

            fair_prob, f_am = fair_combo_odds(legs)
            if f_am == 0:
                continue

            gap = k_am - f_am
            if gap < min_odds_gap:
                continue

            ev = ev_per_dollar(fair_prob, k_am)
            if ev < min_ev:
                continue

            k_combined = 1.0
            for leg in legs:
                k_combined *= leg.kalshi_prob

            signals.append(ComboSignal(
                legs=legs, n_legs=n,
                fair_combined_prob=fair_prob,
                kalshi_combined_prob=k_combined,
                fair_american=f_am,
                kalshi_american=k_am,
                odds_gap=gap,
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
