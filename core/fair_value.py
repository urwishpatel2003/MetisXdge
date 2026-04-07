# core/fair_value.py
"""
Fair Value Engine
-----------------
Converts American odds → implied probability → vig-free (sharp) probability.
Computes parlay fair value and detects Kalshi mispricing.
"""

from typing import List, Tuple


# ── American Odds ↔ Probability ────────────────────────────────────────────────

def american_to_implied_prob(odds: int) -> float:
    """Raw implied probability including vig"""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def implied_prob_to_american(prob: float) -> int:
    """Convert probability back to American odds"""
    if prob <= 0 or prob >= 1:
        raise ValueError(f"Probability must be between 0 and 1, got {prob}")
    if prob < 0.5:
        return round(100 / prob - 100)
    else:
        return round(-(prob * 100) / (1 - prob))


def decimal_to_american(decimal_odds: float) -> int:
    """Convert decimal odds to American"""
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1) * 100)
    else:
        return round(-100 / (decimal_odds - 1))


# ── Vig Removal ────────────────────────────────────────────────────────────────

def remove_vig_two_way(odds_side1: int, odds_side2: int) -> Tuple[float, float]:
    """
    Remove vig from a two-way market (e.g. moneyline).
    Returns (fair_prob_side1, fair_prob_side2) that sum to 1.0
    """
    raw1 = american_to_implied_prob(odds_side1)
    raw2 = american_to_implied_prob(odds_side2)
    overround = raw1 + raw2
    return raw1 / overround, raw2 / overround


def remove_vig_multiplicative(probs: List[float]) -> List[float]:
    """
    Remove vig from N-way market using multiplicative method.
    Scales each raw probability proportionally so they sum to 1.
    """
    total = sum(probs)
    return [p / total for p in probs]


def compute_vig_pct(odds_side1: int, odds_side2: int) -> float:
    """Returns the book's vig as a percentage of handle"""
    raw1 = american_to_implied_prob(odds_side1)
    raw2 = american_to_implied_prob(odds_side2)
    overround = raw1 + raw2
    return (overround - 1.0) * 100


# ── Parlay Fair Value ──────────────────────────────────────────────────────────

def parlay_fair_value(fair_probs: List[float]) -> Tuple[float, int]:
    """
    Compute true parlay probability and fair American odds.
    Assumes legs are independent (use with caution on correlated legs).

    Returns: (combined_probability, fair_american_odds)
    """
    combined_prob = 1.0
    for p in fair_probs:
        combined_prob *= p
    fair_odds = implied_prob_to_american(combined_prob)
    return combined_prob, fair_odds


def parlay_overlay(
    fair_probs: List[float],
    offered_american_odds: int
) -> Tuple[float, float]:
    """
    Compare fair parlay value vs offered payout.

    Returns: (edge_pct, ev_per_dollar)
      - edge_pct > 0 means you have an edge
      - ev_per_dollar > 0 means +EV
    """
    fair_prob, fair_odds = parlay_fair_value(fair_probs)
    offered_prob = american_to_implied_prob(offered_american_odds)

    # Edge = how much the market underestimates your true win probability
    edge_pct = (fair_prob - offered_prob) / offered_prob * 100

    # EV: expected value per $1 wagered
    if offered_american_odds > 0:
        payout_per_dollar = offered_american_odds / 100
    else:
        payout_per_dollar = 100 / abs(offered_american_odds)

    ev_per_dollar = (fair_prob * payout_per_dollar) - (1 - fair_prob)

    return edge_pct, ev_per_dollar


# ── Kalshi Mispricing ──────────────────────────────────────────────────────────

def kalshi_edge(
    kalshi_price: float,   # 0-100 cents (YES price)
    fair_prob: float,      # Sharp-derived true probability
    side: str = "YES"      # "YES" or "NO"
) -> Tuple[float, float, int, int]:
    """
    Compute edge between Kalshi price and fair value.

    Returns: (edge_pct, ev_per_dollar, kalshi_american, fair_american)
    """
    kalshi_prob = kalshi_price / 100.0

    if side == "NO":
        kalshi_prob = 1 - kalshi_prob
        fair_prob = 1 - fair_prob

    # If fair_prob > kalshi_prob → Kalshi is underpricing this outcome → BUY
    edge_pct = (fair_prob - kalshi_prob) / kalshi_prob * 100

    # EV: Kalshi pays $1 per $price for YES contracts
    # Profit if win = (100 - price) cents, loss if lose = price cents
    profit_if_win = (100 - kalshi_price) / 100 if side == "YES" else kalshi_price / 100
    loss_if_lose = kalshi_price / 100 if side == "YES" else (100 - kalshi_price) / 100

    ev_per_dollar = (fair_prob * profit_if_win) - ((1 - fair_prob) * loss_if_lose)

    kalshi_american = implied_prob_to_american(kalshi_prob)
    fair_american = implied_prob_to_american(fair_prob)

    return edge_pct, ev_per_dollar, kalshi_american, fair_american


# ── Kelly Criterion ────────────────────────────────────────────────────────────

def kelly_stake(
    fair_prob: float,
    decimal_odds: float,
    bankroll: float,
    fraction: float = 0.25,
    max_stake: float = 200.0
) -> float:
    """
    Fractional Kelly stake sizing.

    Args:
        fair_prob: True win probability
        decimal_odds: Payout odds (e.g. 2.5 = win $1.50 per $1)
        bankroll: Total available bankroll
        fraction: Kelly fraction (0.25 = quarter Kelly)
        max_stake: Hard cap

    Returns: Recommended stake in dollars
    """
    b = decimal_odds - 1      # Net profit per unit
    q = 1 - fair_prob         # Loss probability
    kelly_full = (b * fair_prob - q) / b

    if kelly_full <= 0:
        return 0.0             # No edge, don't bet

    stake = bankroll * kelly_full * fraction
    return min(stake, max_stake)


def kalshi_decimal_odds(price: float, side: str = "YES") -> float:
    """Convert Kalshi price to decimal odds"""
    if side == "YES":
        return 100 / price     # e.g. price=30 → 3.33x
    else:
        return 100 / (100 - price)
