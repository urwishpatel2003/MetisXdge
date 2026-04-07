# scanners/correlation.py
"""
Strategy 2: Correlation Play Builder
--------------------------------------
Constructs multi-leg Kalshi plays where the market doesn't price
the correlation between outcomes. Like your PrizePicks combo —
buying correlated events simultaneously captures compounded value
that individual prices don't reflect.

Best targets:
- Same-night "No" run line combos (heavy favorites to NOT lose big)
- Same-division teams on same slate
- Cross-sport same-night heavy favorites
"""

import logging
from itertools import combinations
from core.models import KalshiContract, OddsLine, EdgeSignal, SignalType, CorrelationLeg
from core.fair_value import parlay_overlay, remove_vig_two_way, american_to_implied_prob

logger = logging.getLogger(__name__)

# Correlation bonus table: same-sport same-night "No" run lines
# (conservative estimates — real correlation requires historical analysis)
CORRELATION_BONUS = {
    ("baseball_mlb", "baseball_mlb"): 0.05,      # 5% positive correlation
    ("basketball_nba", "basketball_nba"): 0.03,
    ("baseball_mlb", "basketball_nba"): 0.0,      # Cross-sport = independent
}


def get_correlation_bonus(sport1: str, sport2: str, market1: str, market2: str) -> float:
    """
    Returns estimated positive correlation bonus between two legs.
    Positive correlation = outcomes tend to happen together.
    When Kalshi prices them independently, you capture this bonus.
    """
    key = tuple(sorted([sport1, sport2]))
    base = CORRELATION_BONUS.get(key, 0.0)

    # Same market type on same sport → higher correlation
    if sport1 == sport2 and market1 == market2:
        base += 0.02

    return base


def build_correlation_combos(
    contracts: list[KalshiContract],
    lines: list[OddsLine],
    max_legs: int = 3,
    min_edge_pct: float = 8.0,
) -> list[EdgeSignal]:
    """
    Find 2-3 leg Kalshi combos where:
    1. Each leg has individual edge (fair_prob > kalshi_prob)
    2. The legs have positive correlation (compounding captures extra value)
    3. Combined edge exceeds min_edge_pct
    """
    signals = []

    # First: find individually edgy contracts
    edgy_contracts = []
    for contract in contracts:
        # Find a matching odds line
        matching_line = _find_line(contract, lines)
        if not matching_line:
            continue

        fair_prob = matching_line.implied_prob
        kalshi_prob = contract.yes_prob
        individual_edge = (fair_prob - kalshi_prob) / kalshi_prob * 100

        if individual_edge > 0:
            edgy_contracts.append({
                "contract": contract,
                "line": matching_line,
                "fair_prob": fair_prob,
                "kalshi_prob": kalshi_prob,
                "individual_edge": individual_edge
            })

    logger.info(f"Found {len(edgy_contracts)} individually edgy contracts")

    # Try all 2 and 3-leg combos
    for n_legs in range(2, max_legs + 1):
        for combo in combinations(edgy_contracts, n_legs):
            fair_probs = [c["fair_prob"] for c in combo]
            kalshi_probs = [c["kalshi_prob"] for c in combo]

            # Apply correlation bonuses
            adjusted_fair_probs = list(fair_probs)
            for i, j in combinations(range(len(combo)), 2):
                bonus = get_correlation_bonus(
                    combo[i]["line"].sport,
                    combo[j]["line"].sport,
                    combo[i]["line"].market_key,
                    combo[j]["line"].market_key,
                )
                # Boost the weaker leg's fair_prob by the correlation bonus
                weaker_idx = i if fair_probs[i] < fair_probs[j] else j
                adjusted_fair_probs[weaker_idx] = min(
                    0.99, adjusted_fair_probs[weaker_idx] + bonus
                )

            # Compute combined Kalshi implied odds
            combined_kalshi_prob = 1.0
            for kp in kalshi_probs:
                combined_kalshi_prob *= kp

            # What American odds does this combo pay on the platform?
            # Approximation: product of decimal payouts
            combined_decimal = 1.0
            for c in combo:
                combined_decimal *= (100 / c["contract"].yes_price)
            combined_american = round((combined_decimal - 1) * 100)

            # Compute edge
            edge_pct, ev = parlay_overlay(adjusted_fair_probs, combined_american)

            if edge_pct < min_edge_pct:
                continue

            # Build leg details
            legs = [
                CorrelationLeg(
                    kalshi_ticker=c["contract"].ticker,
                    kalshi_title=c["contract"].title,
                    side="YES",
                    price=c["contract"].yes_price,
                    fair_prob=c["fair_prob"],
                    sport=c["line"].sport,
                    event=f"{c['line'].away_team} @ {c['line'].home_team}"
                )
                for c in combo
            ]

            fair_combined_prob = 1.0
            for p in adjusted_fair_probs:
                fair_combined_prob *= p

            signal = EdgeSignal(
                signal_type=SignalType.CORRELATION,
                fair_prob=fair_combined_prob,
                kalshi_prob=combined_kalshi_prob,
                edge_pct=edge_pct,
                fair_odds_american=round((1/fair_combined_prob - 1) * 100),
                kalshi_odds_american=combined_american,
                kalshi_ticker=" + ".join(c["contract"].ticker for c in combo),
                kalshi_title=f"{n_legs}-leg combo",
                side="YES",
                sport=" / ".join(set(c["line"].sport for c in combo)),
                event=" | ".join(
                    f"{c['line'].away_team} @ {c['line'].home_team}" for c in combo
                ),
                suggested_stake_usd=50.0,  # flat stake for combos
                legs=legs,
                notes=(
                    f"Combined Kalshi payout: +{combined_american} | "
                    f"Fair: +{round((1/fair_combined_prob - 1) * 100)} | "
                    f"EV/dollar: ${ev:.3f}"
                )
            )
            signals.append(signal)

    logger.info(f"Found {len(signals)} correlation signals")
    return signals


def _find_line(contract: KalshiContract, lines: list[OddsLine]) -> OddsLine | None:
    """Find best matching OddsLine for a Kalshi contract"""
    from scanners.arb_scanner import team_in_title
    for line in lines:
        if team_in_title(line.outcome, contract.title):
            return line
    return None
