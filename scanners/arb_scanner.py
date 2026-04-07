# scanners/arb_scanner.py
"""
Strategy 1: Kalshi vs Sharp Book Arbitrage
-------------------------------------------
Finds Kalshi contracts where the price diverges from Pinnacle's
vig-free implied probability by more than MIN_ARB_EDGE_PCT.
"""

import logging
from difflib import SequenceMatcher
from core.models import KalshiContract, OddsLine, EdgeSignal, SignalType
from core.fair_value import kalshi_edge, kalshi_decimal_odds, kelly_stake

logger = logging.getLogger(__name__)

# Sports keywords for fuzzy contract matching
SPORT_KEYWORDS = {
    "baseball_mlb": ["mlb", "baseball", "wins", "world series"],
    "basketball_nba": ["nba", "basketball"],
    "basketball_ncaab": ["ncaa", "college basketball", "march madness"],
    "americanfootball_nfl": ["nfl", "football", "super bowl"],
}


def fuzzy_match(s1: str, s2: str) -> float:
    """Returns similarity ratio between two strings (0-1)"""
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def team_in_title(team: str, title: str) -> bool:
    """Check if a team name appears (approximately) in a Kalshi contract title"""
    team = team.lower()
    title = title.lower()
    team_parts = team.split()
    # Check city name or team name
    for part in team_parts:
        if len(part) > 3 and part in title:
            return True
    return fuzzy_match(team, title) > 0.4


def match_contracts_to_lines(
    contracts: list[KalshiContract],
    lines: list[OddsLine]
) -> list[tuple[KalshiContract, OddsLine, str]]:
    """
    Attempt to match Kalshi contracts to sharp book lines.
    Returns list of (contract, odds_line, side) tuples.

    side = "YES" means buying YES on Kalshi matches the odds_line outcome.
    """
    matched = []

    for contract in contracts:
        title = contract.title.lower()

        for line in lines:
            outcome = line.outcome.lower()

            # Try to match: does this Kalshi contract correspond to this outcome?
            if team_in_title(outcome, title):
                # YES side of Kalshi = team wins
                contract.mapped_outcome = line.outcome
                contract.sport = line.sport
                matched.append((contract, line, "YES"))

            # Check NO side: Kalshi NO = opponent wins
            # (less precise matching, requires game context)

    logger.info(f"Matched {len(matched)} contract/line pairs")
    return matched


def scan_arb(
    contracts: list[KalshiContract],
    lines: list[OddsLine],
    min_edge_pct: float = 3.0,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_stake: float = 200.0,
) -> list[EdgeSignal]:
    """
    Main arb scanner.
    Returns list of EdgeSignal objects where Kalshi is mispriced vs sharp lines.
    """
    signals = []
    matched = match_contracts_to_lines(contracts, lines)

    for contract, line, side in matched:
        kalshi_price = contract.yes_price if side == "YES" else contract.no_price
        fair_prob = line.implied_prob

        edge_pct, ev, kalshi_american, fair_american = kalshi_edge(
            kalshi_price=kalshi_price,
            fair_prob=fair_prob,
            side=side
        )

        if edge_pct < min_edge_pct:
            continue

        # Compute Kelly stake
        dec_odds = kalshi_decimal_odds(kalshi_price, side)
        stake = kelly_stake(
            fair_prob=fair_prob,
            decimal_odds=dec_odds,
            bankroll=bankroll,
            fraction=kelly_fraction,
            max_stake=max_stake
        )

        signal = EdgeSignal(
            signal_type=SignalType.ARB,
            fair_prob=fair_prob,
            kalshi_prob=kalshi_price / 100,
            edge_pct=edge_pct,
            fair_odds_american=fair_american,
            kalshi_odds_american=kalshi_american,
            kalshi_ticker=contract.ticker,
            kalshi_title=contract.title,
            side=side,
            sharp_book=line.book,
            sharp_odds=line.american_odds,
            sport=line.sport,
            event=f"{line.away_team} @ {line.home_team}",
            suggested_stake_usd=stake,
            kelly_fraction=kelly_fraction,
            notes=f"EV per dollar: ${ev:.3f}"
        )
        signals.append(signal)
        logger.info(
            f"ARB signal: {contract.ticker} | "
            f"Edge: {edge_pct:.1f}% | Stake: ${stake:.0f}"
        )

    return signals
