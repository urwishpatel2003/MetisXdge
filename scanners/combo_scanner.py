# scanners/combo_scanner.py
"""
MetisXdge — Combo Scanner v4
Targets confirmed Kalshi individual game markets:
  KXMLBSPREAD  → "[Team] wins by over X.5 runs?"
  KXMLBTOTAL   → "[Away] vs [Home] Total Runs?" (numbered contracts)
  KXMLBTEAMTOTAL → "Will [Team] score over X.5 runs?"
  KXNBASPREAD  → "[Team] wins by over X.5 Points?"
  KXNHLSPREAD  → "[Team] wins by over X.5 goals?"
  KXMLBGAME    → "[Away] vs [Home] Winner?" / "[Away] at [Home] Winner?"
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
    'baseball_mlb':              'MLB',
    'basketball_nba':            'NBA',
    'basketball_ncaab':          'NCAAB',
    'americanfootball_nfl':      'NFL',
    'icehockey_nhl':             'NHL',
    'soccer_epl':                'EPL',
    'soccer_uefa_champs_league': 'UCL',
    'soccer_usa_mls':            'MLS',
}

# Ticker prefix → sport mapping
TICKER_SPORT = {
    'KXMLB': 'baseball_mlb',
    'KXNBA': 'basketball_nba',
    'KXNHL': 'icehockey_nhl',
    'KXNFL': 'americanfootball_nfl',
    'KXNCAAB': 'basketball_ncaab',
}

def ticker_sport(ticker: str) -> str:
    for prefix, sport in TICKER_SPORT.items():
        if ticker.startswith(prefix):
            return sport
    return ''

def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def team_match(team: str, outcome: str) -> float:
    """Score how well team name matches an odds outcome."""
    team_l    = team.lower().strip()
    outcome_l = outcome.lower().strip()
    words     = [w for w in team_l.split() if len(w) > 3]
    word_hit  = any(w in outcome_l for w in words)
    return max(sim(team_l, outcome_l), 0.75 if word_hit else 0.0)


def parse_title(title: str, ticker: str) -> Optional[dict]:
    """
    Parse a Kalshi contract title into structured leg data.
    Returns dict with keys: type, team, line, side, raw
    or None if unparseable.
    """
    t = title.strip().rstrip('?').strip()

    # SPREAD: "New York M wins by over 3.5 runs"
    m = re.match(
        r'^(.+?)\s+wins by over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
        t, re.IGNORECASE
    )
    if m:
        return {
            'type': 'spread',
            'team': m.group(1).strip(),
            'line': float(m.group(2)),
            'side': 'yes',  # YES = team wins by over X
            'raw':  title,
        }

    # TEAM TOTAL: "Will New York M score over 7.5 runs"
    m = re.match(
        r'^will\s+(.+?)\s+score over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
        t, re.IGNORECASE
    )
    if m:
        return {
            'type': 'team_total',
            'team': m.group(1).strip(),
            'line': float(m.group(2)),
            'side': 'yes',
            'raw':  title,
        }

    # GAME TOTAL: "Arizona vs New York M Total Runs" (numbered by ticker suffix)
    m = re.match(
        r'^(.+?)\s+(?:vs|at)\s+(.+?)\s+(?:total|totals)\s*(runs?|points?|goals?)?',
        t, re.IGNORECASE
    )
    if m:
        # Extract line from ticker suffix e.g. KXMLBTOTAL-...-9 → 9
        line_m = re.search(r'-(\d+\.?\d*)$', ticker)
        if line_m:
            return {
                'type':  'game_total',
                'away':  m.group(1).strip(),
                'home':  m.group(2).strip(),
                'line':  float(line_m.group(1)),
                'side':  'yes',
                'raw':   title,
            }

    # WINNER: "Pittsburgh vs Chicago C Winner" / "Vegas at Seattle Winner"
    m = re.match(
        r'^(.+?)\s+(?:vs|at)\s+(.+?)\s+winner$',
        t, re.IGNORECASE
    )
    if m:
        return {
            'type': 'winner',
            'away': m.group(1).strip(),
            'home': m.group(2).strip(),
            'side': 'yes',
            'raw':  title,
        }

    return None


def find_sharp_line(parsed: dict, lines: list, ticker: str) -> Optional[tuple]:
    """
    Match parsed leg to a sharp odds line.
    Returns (OddsLine, fair_prob, side_description) or None.
    """
    sport = ticker_sport(ticker)

    if parsed['type'] == 'spread':
        team     = parsed['team']
        target   = parsed['line']
        best, bs = None, 0.5

        for line in lines:
            if line.market_key != 'spreads':
                continue
            if sport and line.sport != sport:
                continue
            score = team_match(team, line.outcome)
            # Boost if line value is close
            if hasattr(line, 'point') and line.point is not None:
                try:
                    if abs(abs(float(line.point)) - target) <= 1.0:
                        score += 0.15
                except:
                    pass
            if score > bs:
                bs, best = score, line

        if not best:
            return None

        # YES side = team wins by over X = spread favorite covers
        # fair_prob = sharp spread probability
        fair_prob = best.implied_prob
        return best, fair_prob, f"YES {parsed['team']} -{parsed['line']}"

    elif parsed['type'] == 'team_total':
        team   = parsed['team']
        target = parsed['line']
        best, bs = None, 0.0

        for line in lines:
            if line.market_key != 'totals':
                continue
            if sport and line.sport != sport:
                continue
            # Team totals: match team name to home/away
            score = max(
                team_match(team, line.home_team),
                team_match(team, line.away_team)
            )
            if hasattr(line, 'point') and line.point is not None:
                try:
                    if abs(float(line.point) - target) <= 1.5:
                        score += 0.2
                except:
                    pass
            if score > bs:
                bs, best = score, line

        if not best or bs < 0.4:
            return None

        fair_prob = best.implied_prob
        return best, fair_prob, f"Will {parsed['team']} Over {parsed['line']}"

    elif parsed['type'] == 'game_total':
        home   = parsed['home']
        away   = parsed['away']
        target = parsed['line']
        best, bs = None, 0.0

        for line in lines:
            if line.market_key != 'totals':
                continue
            if sport and line.sport != sport:
                continue
            score = max(
                team_match(home, line.home_team),
                team_match(away, line.away_team)
            )
            if hasattr(line, 'point') and line.point is not None:
                try:
                    if abs(float(line.point) - target) <= 1.0:
                        score += 0.2
                except:
                    pass
            if score > bs:
                bs, best = score, line

        if not best or bs < 0.3:
            return None

        # YES = Over
        fair_prob = best.implied_prob
        return best, fair_prob, f"Over {parsed['line']} ({parsed['away']} @ {parsed['home']})"

    elif parsed['type'] == 'winner':
        home = parsed['home']
        away = parsed['away']
        best, bs = None, 0.5

        for line in lines:
            if line.market_key != 'h2h':
                continue
            if sport and line.sport != sport:
                continue
            score = max(
                team_match(home, line.outcome),
                team_match(away, line.outcome)
            )
            if score > bs:
                bs, best = score, line

        if not best:
            return None

        # Match which team this contract is for from ticker
        # e.g. KXMLBGAME-...-PIT = Pittsburgh winner
        fair_prob = best.implied_prob
        return best, fair_prob, f"{parsed['home']} or {parsed['away']} Winner"

    return None


@dataclass
class ComboLeg:
    kalshi_ticker: str
    display_name:  str
    leg_type:      str
    kalshi_price:  float
    kalshi_prob:   float
    fair_prob:     float
    fair_american: int
    sharp_odds:    int
    sharp_book:    str
    sport:         str
    event:         str
    edge_pct:      float


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
    contracts:     list,
    lines:         list,
    min_fair_prob: float = 0.40,
) -> list:
    matched     = []
    best_by_key = {}

    for contract in contracts:
        ticker = contract.ticker

        # Price must be active (not near 0 or 100)
        yes_price = contract.yes_price
        if yes_price < 15 or yes_price > 85:
            continue

        parsed = parse_title(contract.title, ticker)
        if not parsed:
            continue

        result = find_sharp_line(parsed, lines, ticker)
        if not result:
            continue

        sharp_line, fair_prob, description = result

        # Fair odds filter: -250 to +250
        fair_am = implied_prob_to_american(fair_prob)
        if fair_am < -250 or fair_am > 250:
            continue

        if fair_prob < min_fair_prob:
            continue

        sport       = ticker_sport(ticker)
        sport_short = SPORT_SHORT.get(sport, sport.upper())
        display     = f"{description} ({sport_short})"
        event       = f"{sharp_line.away_team} @ {sharp_line.home_team}"
        key         = (display.lower(), event)

        kalshi_prob = yes_price / 100.0
        edge        = (fair_prob - kalshi_prob) / kalshi_prob * 100

        leg = ComboLeg(
            kalshi_ticker = ticker,
            display_name  = display,
            leg_type      = parsed['type'],
            kalshi_price  = yes_price,
            kalshi_prob   = kalshi_prob,
            fair_prob     = fair_prob,
            fair_american = fair_am,
            sharp_odds    = sharp_line.american_odds,
            sharp_book    = sharp_line.book,
            sport         = sport,
            event         = event,
            edge_pct      = edge,
        )

        if key not in best_by_key or yes_price > best_by_key[key].kalshi_price:
            best_by_key[key] = leg

    matched = list(best_by_key.values())

    type_counts = {}
    for leg in matched:
        type_counts[leg.leg_type] = type_counts.get(leg.leg_type, 0) + 1

    logger.info(f"Matched {len(matched)} legs ({len(contracts)} contracts / {len(lines)} lines)")
    logger.info(f"Leg types: {type_counts}")
    for leg in matched[:8]:
        logger.info(
            f"  {leg.display_name} | {leg.kalshi_price:.0f}c | "
            f"Fair {leg.fair_prob:.1%} ({leg.fair_american:+d}) | {leg.event}"
        )
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


def ev_per_dollar(fair_prob: float, k_american: int) -> float:
    profit = k_american / 100 if k_american >= 0 else 100 / abs(k_american)
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
            if len(set(leg.display_name for leg in legs)) != n:
                continue

            try:
                k_am = kalshi_combo_payout(legs)
            except Exception:
                continue

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
