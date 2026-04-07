# scanners/combo_scanner.py
"""
MetisXdge — Combo Scanner v3
Clean, strict matching only. Better to have fewer real signals than many fake ones.
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

# Known MLB team city/name keywords
MLB_TEAMS = {
    'arizona', 'atlanta', 'baltimore', 'boston', 'chicago', 'cincinnati',
    'cleveland', 'colorado', 'detroit', 'houston', 'kansas city', 'los angeles',
    'miami', 'milwaukee', 'minnesota', 'new york', 'oakland', 'philadelphia',
    'pittsburgh', 'san diego', 'san francisco', 'seattle', 'st. louis',
    'tampa bay', 'texas', 'toronto', 'washington', 'new york y', 'new york m',
    'los angeles d', 'los angeles a', 'kansas', 'st louis'
}

NBA_TEAMS = {
    'atlanta', 'boston', 'brooklyn', 'charlotte', 'chicago', 'cleveland',
    'dallas', 'denver', 'detroit', 'golden state', 'houston', 'indiana',
    'los angeles', 'memphis', 'miami', 'milwaukee', 'minnesota', 'new orleans',
    'new york', 'oklahoma city', 'orlando', 'philadelphia', 'phoenix',
    'portland', 'sacramento', 'san antonio', 'toronto', 'utah', 'washington',
    'los angeles c', 'los angeles l', 'oklahoma'
}

NHL_TEAMS = {
    'anaheim', 'arizona', 'boston', 'buffalo', 'calgary', 'carolina',
    'chicago', 'colorado', 'columbus', 'dallas', 'detroit', 'edmonton',
    'florida', 'los angeles', 'minnesota', 'montreal', 'nashville',
    'new jersey', 'new york', 'ottawa', 'philadelphia', 'pittsburgh',
    'san jose', 'seattle', 'st. louis', 'tampa bay', 'toronto', 'vancouver',
    'vegas', 'washington', 'winnipeg', 'utah', 'golden knights',
    'uta mammoth', 'min wild', 'det red wings', 'edm oilers', 'phi flyers'
}

SOCCER_TEAMS = {
    'arsenal', 'chelsea', 'liverpool', 'manchester', 'tottenham', 'newcastle',
    'aston villa', 'brighton', 'everton', 'wolves', 'west ham', 'fulham',
    'brentford', 'crystal palace', 'leicester', 'nottingham', 'bournemouth',
    'real madrid', 'barcelona', 'atletico', 'inter milan', 'ac milan',
    'juventus', 'napoli', 'bayern', 'dortmund', 'psg', 'porto',
    'celtic', 'rangers', 'la galaxy', 'inter miami', 'atlanta united',
    'seattle sounders', 'portland timbers', 'sporting', 'toronto fc'
}

SPORT_TEAMS = {
    'baseball_mlb': MLB_TEAMS,
    'basketball_nba': NBA_TEAMS,
    'icehockey_nhl': NHL_TEAMS,
    'soccer_epl': SOCCER_TEAMS,
    'soccer_uefa_champs_league': SOCCER_TEAMS,
    'soccer_usa_mls': SOCCER_TEAMS,
}

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

# Patterns to reject
REJECT_PATTERN = re.compile(
    r':\s*\d+\+|\d+\+|wins by over|wins by under|over \d|under \d|'
    r'points scored|runs scored|rebounds|assists|strikeouts|home run|'
    r'both teams|first half|quarter|tie|draw|election|bitcoin|'
    r'weather|oscar|fed rate|gdp|season wins|tournament|series',
    re.IGNORECASE
)


def parse_moneyline_team(part: str) -> Optional[str]:
    """Extract team name from 'yes TeamName' if it's a valid moneyline."""
    part = part.strip()
    if not part.lower().startswith('yes '):
        return None
    team = part[4:].strip()
    if REJECT_PATTERN.search(team):
        return None
    if re.search(r'\d', team):
        return None
    if len(team) < 3 or len(team) > 25:
        return None
    # Must not look like a person (two capitalized words = player name)
    words = team.split()
    if len(words) == 2 and all(w[0].isupper() for w in words):
        return None
    return team


def find_matching_line(team: str, lines: list[OddsLine]) -> Optional[OddsLine]:
    """
    Strict matching: find a sharp moneyline for this team.
    Requires >65% similarity to avoid cross-sport false matches.
    """
    team_l = team.lower().strip()
    best, best_score = None, 0.65  # strict threshold

    for line in lines:
        if line.market_key != 'h2h':
            continue
        outcome_l = line.outcome.lower()

        # Check if any significant word from team appears in outcome
        team_words = [w for w in team_l.split() if len(w) > 3]
        word_match = any(w in outcome_l for w in team_words)

        sim = SequenceMatcher(None, team_l, outcome_l).ratio()
        score = max(sim, 0.75 if word_match else 0.0)

        if score > best_score:
            best_score = score
            best = line

    return best


@dataclass
class ComboLeg:
    kalshi_ticker:  str
    display_name:   str
    kalshi_price:   float
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


def match_legs(
    contracts:     list[KalshiContract],
    lines:         list[OddsLine],
    min_fair_prob: float = 0.55,
) -> list[ComboLeg]:
    matched   = []
    best_by_key = {}  # (team, event) -> best leg

    for contract in contracts:
        # Only h2h moneyline markets — strict price range 25-75c
        if contract.yes_price < 25 or contract.yes_price > 75:
            continue

        parts = contract.title.split(',')
        for part in parts:
            team = parse_moneyline_team(part.strip())
            if not team:
                continue

            line = find_matching_line(team, lines)
            if not line:
                continue

            fair_prob = line.implied_prob
            if fair_prob < min_fair_prob:
                continue

            sport_short = SPORT_SHORT.get(line.sport, line.sport.upper())
            display     = f"{team} ML ({sport_short})"
            event       = f"{line.away_team} @ {line.home_team}"
            key         = (team.lower(), event)

            kalshi_price = contract.yes_price
            kalshi_prob  = kalshi_price / 100.0
            edge         = (fair_prob - kalshi_prob) / kalshi_prob * 100

            leg = ComboLeg(
                kalshi_ticker = contract.ticker,
                display_name  = display,
                kalshi_price  = kalshi_price,
                kalshi_prob   = kalshi_prob,
                fair_prob     = fair_prob,
                sharp_odds    = line.american_odds,
                sharp_book    = line.book,
                sport         = line.sport,
                event         = event,
                edge_pct      = edge,
            )

            # Keep highest-priced leg per team+event (most liquid)
            if key not in best_by_key or kalshi_price > best_by_key[key].kalshi_price:
                best_by_key[key] = leg

    matched = list(best_by_key.values())
    logger.info(f"Matched {len(matched)} unique legs ({len(contracts)} contracts / {len(lines)} lines)")
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

            try:
                k_american = kalshi_combo_payout(legs)
            except Exception:
                continue

            # Sanity cap — real combos shouldn't pay more than +2000
            if k_american > 2000:
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
