# scanners/stale_price.py
"""
Strategy 3: Stale Price Detector
----------------------------------
Monitors sharp book line movements. When Pinnacle moves a line significantly,
checks if Kalshi has updated. If not — stale price window is open.

This is the fastest-closing edge (~5-15 min windows).
"""

import logging
from datetime import datetime, timedelta
from core.models import OddsLine, KalshiContract, EdgeSignal, SignalType
from core.fair_value import american_to_implied_prob, kalshi_edge, kelly_stake, kalshi_decimal_odds

logger = logging.getLogger(__name__)


class LineMoveTracker:
    """Tracks sharp book line history and detects significant moves"""

    def __init__(self, min_move_pts: int = 10):
        self.min_move_pts = min_move_pts
        self.line_history: dict[str, list[OddsLine]] = {}  # key → [OddsLine]

    def _line_key(self, line: OddsLine) -> str:
        return f"{line.sport}|{line.home_team}|{line.away_team}|{line.outcome}|{line.market_key}"

    def update(self, lines: list[OddsLine]) -> list[tuple[OddsLine, OddsLine]]:
        """
        Update line history. Returns list of (old_line, new_line) for
        any line that moved by >= min_move_pts.
        """
        moves = []

        for line in lines:
            key = self._line_key(line)

            if key in self.line_history:
                prev = self.line_history[key][-1]
                move_pts = abs(line.american_odds - prev.american_odds)

                if move_pts >= self.min_move_pts:
                    moves.append((prev, line))
                    logger.info(
                        f"Line move detected: {line.outcome} "
                        f"{prev.american_odds} → {line.american_odds} "
                        f"({'+' if line.american_odds > prev.american_odds else ''}"
                        f"{line.american_odds - prev.american_odds} pts)"
                    )

            self.line_history.setdefault(key, []).append(line)
            # Keep last 20 readings per line
            self.line_history[key] = self.line_history[key][-20:]

        return moves


def scan_stale_prices(
    line_moves: list[tuple[OddsLine, OddsLine]],
    contracts: list[KalshiContract],
    min_edge_pct: float = 4.0,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_stake: float = 200.0,
) -> list[EdgeSignal]:
    """
    For each detected line move, check if Kalshi has updated.
    If Kalshi still reflects the OLD line price → stale signal.
    """
    signals = []

    for old_line, new_line in line_moves:
        # New fair probability (post-move)
        new_fair_prob = new_line.implied_prob

        # Find matching Kalshi contract
        matching_contracts = _find_matching_contracts(new_line, contracts)

        for contract, side in matching_contracts:
            kalshi_price = contract.yes_price if side == "YES" else contract.no_price

            # What probability does Kalshi currently imply?
            kalshi_prob = (kalshi_price / 100) if side == "YES" else (1 - kalshi_price / 100)

            # What probability did the OLD sharp line imply?
            old_fair_prob = old_line.implied_prob

            # Is Kalshi still near the OLD price?
            kalshi_vs_old = abs(kalshi_prob - old_fair_prob)
            kalshi_vs_new = abs(kalshi_prob - new_fair_prob)

            if kalshi_vs_old > kalshi_vs_new:
                # Kalshi already updated — no edge
                continue

            # Compute edge vs new fair value
            edge_pct, ev, kalshi_american, fair_american = kalshi_edge(
                kalshi_price=kalshi_price,
                fair_prob=new_fair_prob,
                side=side
            )

            if edge_pct < min_edge_pct:
                continue

            dec_odds = kalshi_decimal_odds(kalshi_price, side)
            stake = kelly_stake(
                fair_prob=new_fair_prob,
                decimal_odds=dec_odds,
                bankroll=bankroll,
                fraction=kelly_fraction,
                max_stake=max_stake
            )

            signal = EdgeSignal(
                signal_type=SignalType.STALE_PRICE,
                fair_prob=new_fair_prob,
                kalshi_prob=kalshi_prob,
                edge_pct=edge_pct,
                fair_odds_american=fair_american,
                kalshi_odds_american=kalshi_american,
                kalshi_ticker=contract.ticker,
                kalshi_title=contract.title,
                side=side,
                sharp_book=new_line.book,
                sharp_odds=new_line.american_odds,
                sport=new_line.sport,
                event=f"{new_line.away_team} @ {new_line.home_team}",
                suggested_stake_usd=stake,
                kelly_fraction=kelly_fraction,
                notes=(
                    f"Pinnacle moved {old_line.american_odds}→{new_line.american_odds} | "
                    f"Kalshi still at {kalshi_price}¢ | "
                    f"Window: ~10-15 min"
                ),
                expires_at=datetime.utcnow() + timedelta(minutes=15)
            )
            signals.append(signal)

    return signals


def _find_matching_contracts(
    line: OddsLine,
    contracts: list[KalshiContract]
) -> list[tuple[KalshiContract, str]]:
    """Simple team-name matching against Kalshi titles"""
    from scanners.arb_scanner import team_in_title
    matches = []
    for c in contracts:
        if team_in_title(line.outcome, c.title):
            matches.append((c, "YES"))
    return matches
