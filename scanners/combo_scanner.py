# scanners/combo_scanner.py
"""
MetisXdge — Combo Scanner
--------------------------
Core logic (from original screenshot trade):

  1. Pull Kalshi individual game contracts (KXMLBSPREAD, KXNHLSPREAD, KXMLBGAME etc.)
  2. For each contract, derive fair probability:
     - Spread contracts (wins by over X.5): use ML prob + run distribution model
     - Moneyline contracts: use ML prob directly
  3. Compare Kalshi price vs fair price
  4. Build 2-4 leg combos
  5. Flag where: Kalshi combined payout >> fair parlay odds

The edge: Kalshi multiplies individual contract prices naively.
When individual prices are mispriced (too cheap), the combo payout
explodes vs fair value — exactly like your +419 vs +142 example.
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

SPORT_SHORT = {
    'baseball_mlb':              'MLB',
    'basketball_nba':            'NBA',
    'icehockey_nhl':             'NHL',
    'americanfootball_nfl':      'NFL',
    'basketball_ncaab':          'NCAAB',
}

TICKER_SPORT = {
    'KXMLB': 'baseball_mlb',
    'KXNBA': 'basketball_nba',
    'KXNHL': 'icehockey_nhl',
    'KXNFL': 'americanfootball_nfl',
    'KXNCAAB': 'basketball_ncaab',
}

# Run/goal distribution model
# P(team wins by N+ | team wins) — calibrated empirically
MLB_WIN_BY = {1: 1.00, 2: 0.67, 3: 0.47, 4: 0.32, 5: 0.22, 6: 0.14}
NHL_WIN_BY = {1: 1.00, 2: 0.55, 3: 0.32, 4: 0.18}


def ticker_sport(ticker: str) -> str:
    for prefix, sport in TICKER_SPORT.items():
        if ticker.startswith(prefix):
            return sport
    return 'baseball_mlb'


def fair_prob_for_spread(ml_win_prob: float, line: float, sport: str) -> float:
    """
    P(team wins by line+) = P(win) * P(win by line+ | win)
    Used for YES side of spread contracts.
    NO side = 1 - this value.
    """
    n = int(line)
    if sport == 'baseball_mlb':
        cond = MLB_WIN_BY.get(n, max(0.06, 0.14 - (n - 6) * 0.03))
    elif sport == 'icehockey_nhl':
        cond = NHL_WIN_BY.get(n, 0.08)
    elif sport == 'basketball_nba':
        cond = max(0.03, 1.0 - line / 30.0)
    else:
        cond = max(0.10, 1.0 - line * 0.10)
    return ml_win_prob * cond


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# Explicit team name mappings for ambiguous cases
TEAM_MAP = {
    'chicago ws':    'chicago white sox',
    'chicago c':     'chicago cubs',
    'new york y':    'new york yankees',
    'new york m':    'new york mets',
    'los angeles d': 'los angeles dodgers',
    'los angeles a': 'los angeles angels',
    'los angeles l': 'los angeles lakers',
    'los angeles c': 'los angeles clippers',
    'los angeles k': 'los angeles kings',
    'new york r':    'new york rangers',
    'new york i':    'new york islanders',
    'st. louis':     'st. louis cardinals',
}


def find_ml(team: str, sport: str, lines: list) -> Optional[OddsLine]:
    """Find the moneyline for a team from sharp lines."""
    team_l   = team.lower().strip()
    # Apply explicit mapping if available
    resolved = TEAM_MAP.get(team_l, team_l)

    best, bs = None, 0.55
    for line in lines:
        if line.market_key != 'h2h':
            continue
        if sport and line.sport != sport:
            continue
        outcome_l = line.outcome.lower()

        # Score against resolved name
        words    = [w for w in resolved.split() if len(w) > 3]
        word_hit = any(w in outcome_l for w in words)
        score    = max(sim(resolved, outcome_l), 0.85 if word_hit else 0.0)

        # Penalize if original ambiguous word matches wrong team
        if team_l != resolved and team_l.split()[0] in outcome_l:
            # e.g. 'chicago' matches Cubs when we want White Sox
            if resolved not in outcome_l and sim(resolved, outcome_l) < 0.6:
                score = 0.0

        if score > bs:
            bs, best = score, line

    return best


@dataclass
class ComboLeg:
    kalshi_ticker: str
    display_name:  str
    kalshi_price:  float   # cents (0-100) — what you pay on Kalshi
    kalshi_prob:   float   # kalshi_price / 100
    fair_prob:     float   # derived from sharp ML + model
    fair_american: int
    sharp_ml:      int     # the underlying ML odds used
    sport:         str
    event:         str
    edge_pct:      float   # (fair - kalshi) / kalshi * 100


@dataclass
class ComboSignal:
    legs:                 list
    n_legs:               int
    fair_combined_prob:   float
    kalshi_combined_prob: float
    fair_american:        int     # what fair parlay should pay
    kalshi_american:      int     # what Kalshi actually pays
    odds_gap:             int     # kalshi - fair (the overlay)
    ev_per_dollar:        float
    edge_pct:             float
    sports:               list
    created_at:           datetime = field(default_factory=datetime.utcnow)
    signal_id:            Optional[str] = None


def match_legs(contracts: list, lines: list) -> list:
    """
    For each Kalshi contract:
    1. Parse the title to understand what outcome it represents
    2. Find the sharp ML for the relevant team
    3. Derive fair probability (ML directly or ML + distribution model)
    4. Compare to Kalshi price
    5. Keep if Kalshi is underpricing (kalshi_prob < fair_prob)
    """
    matched     = []
    best_by_key = {}

    logger.info(f"Starting leg matching for {len(contracts)} contracts...")
    count = 0

    for contract in contracts:
        count += 1
        if count % 200 == 0:
            logger.info(f"  Progress: {count}/{len(contracts)}, {len(best_by_key)} legs")

        ticker    = contract.ticker
        title     = contract.title.strip().rstrip('?').strip()
        yes_price = contract.yes_price
        sport     = ticker_sport(ticker)
        sport_s   = SPORT_SHORT.get(sport, sport.upper())

        # ── Parse title ────────────────────────────────────────────────────────

        # SPREAD: "[Team] wins by over X.5 runs/points/goals"
        m = re.match(
            r'^(.+?)\s+wins by over\s+([\d.]+)\s*(runs?|points?|goals?)?$',
            title, re.IGNORECASE
        )
        if m:
            team   = m.group(1).strip()
            line   = float(m.group(2))

            # Cap line values — only realistic alternate lines
            # MLB: 1.5-4.5, NHL: 1.5-2.5, NBA: 1.5-8.5
            max_lines = {'baseball_mlb': 4.5, 'icehockey_nhl': 2.5, 'basketball_nba': 8.5}
            sport_tmp = ticker_sport(ticker)
            if line > max_lines.get(sport_tmp, 5.0):
                continue

            ml_line = find_ml(team, sport, lines)
            if not ml_line:
                continue

            ml_prob = ml_line.implied_prob

            # Fair prob for YES side (team wins by line+)
            fair_yes = fair_prob_for_spread(ml_prob, line, sport)
            fair_no  = 1 - fair_yes

            # Add BOTH sides — both can be underpriced depending on Kalshi's pricing
            no_price = 100 - yes_price

            candidates = [
                (yes_price, fair_yes, f"YES {team} -{line} ({sport_s})"),
                (no_price,  fair_no,  f"NO {team} -{line} ({sport_s})"),
            ]

            for k_price, fair_prob, display in candidates:
                if k_price < 10 or k_price > 90:
                    continue
                kalshi_prob = k_price / 100.0
                # Only keep if Kalshi is underpricing (we get more than fair value)
                if kalshi_prob >= fair_prob:
                    continue
                edge = (fair_prob - kalshi_prob) / kalshi_prob * 100
                if edge < 3:  # minimum 3% edge
                    continue
                fair_am = implied_prob_to_american(fair_prob)
                event   = f"{ml_line.away_team} @ {ml_line.home_team}"
                key     = (display.lower(), event)
                leg     = ComboLeg(
                    kalshi_ticker=ticker, display_name=display,
                    kalshi_price=k_price, kalshi_prob=kalshi_prob,
                    fair_prob=fair_prob, fair_american=fair_am,
                    sharp_ml=ml_line.american_odds, sport=sport, event=event,
                    edge_pct=edge,
                )
                if key not in best_by_key or k_price > best_by_key[key].kalshi_price:
                    best_by_key[key] = leg
            continue

        # Skip winner/moneyline contracts — Kalshi prices these accurately
        # Edge is only in alternate spread lines

    matched = list(best_by_key.values())

    # Log summary
    type_counts = {}
    for leg in matched:
        t = 'NO spread' if leg.display_name.startswith('NO') else \
            'YES spread' if 'YES' in leg.display_name and 'ML' not in leg.display_name else 'ML'
        type_counts[t] = type_counts.get(t, 0) + 1

    logger.info(f"Matched {len(matched)} underpriced legs | Types: {type_counts}")
    for leg in sorted(matched, key=lambda l: l.edge_pct, reverse=True)[:8]:
        logger.info(
            f"  {leg.display_name} | Kalshi {leg.kalshi_price:.0f}c | "
            f"Fair {leg.fair_prob:.1%} ({leg.fair_american:+d}) | "
            f"Edge {leg.edge_pct:.0f}% | {leg.event}"
        )
    return matched


def kalshi_combo_payout(legs: list) -> int:
    """What Kalshi pays for this combo (naive price multiplication)."""
    dec = 1.0
    for leg in legs:
        dec *= 100.0 / leg.kalshi_price
    return round((dec - 1) * 100) if dec >= 2.0 else round(-100 / (dec - 1))


def fair_combo_odds(legs: list) -> tuple:
    """True fair parlay odds from sharp ML + model."""
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
    max_combos:   int   = 10000,
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

            if k_am > 2000:
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
