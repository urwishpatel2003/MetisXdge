# alerts/ntfy.py
"""
ntfy.sh Push Notification Alerter — Combo Edition
Formats ComboSignal alerts clearly showing the odds gap.
"""

import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
NTFY_BASE = "https://ntfy.sh"


def _fmt(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def format_combo_notification(signal) -> dict:
    """
    Format a ComboSignal as an ntfy push notification.
    Leads with the odds gap — that's the number that matters.
    """
    n = signal.n_legs
    gap = signal.odds_gap

    # Priority based on gap size
    if gap >= 200:
        priority = "urgent"
        tags = "rotating_light,moneybag"
    elif gap >= 100:
        priority = "high"
        tags = "chart_with_upwards_trend,moneybag"
    else:
        priority = "default"
        tags = "bar_chart"

    title = (
        f"{n}-leg combo | Kalshi {_fmt(signal.kalshi_american)} vs Fair {_fmt(signal.fair_american)} "
        f"(+{gap} pts gap)"
    )

    # Build leg lines
    leg_lines = []
    for i, leg in enumerate(signal.legs, 1):
        leg_lines.append(
            f"  {i}. {leg.kalshi_title[:45]}\n"
            f"     YES @ {leg.kalshi_price:.0f}c | Sharp: {_fmt(leg.sharp_odds)} ({leg.sharp_book})\n"
            f"     Fair prob: {leg.fair_prob:.1%} | Kalshi prob: {leg.kalshi_prob:.1%}"
        )

    body = (
        f"Fair prob: {signal.fair_combined_prob:.1%} | "
        f"Kalshi prob: {signal.kalshi_combined_prob:.1%}\n"
        f"EV per $1: ${signal.ev_per_dollar:.3f} | "
        f"Edge: {signal.edge_pct:.1f}%\n"
        f"Sports: {', '.join(signal.sports)}\n\n"
        + "\n".join(leg_lines)
        + f"\n\n{datetime.utcnow().strftime('%H:%M UTC')}"
    )

    return {"title": title, "body": body, "priority": priority, "tags": tags}


def format_batch_notification(signals: list) -> dict:
    """Batch summary when multiple combos found in same scan"""
    best = signals[0]  # already sorted by odds_gap desc
    lines = []
    for s in signals[:8]:
        lines.append(
            f"[{s.n_legs}L] {_fmt(s.kalshi_american)} vs {_fmt(s.fair_american)} "
            f"| +{s.odds_gap}pt gap | EV ${s.ev_per_dollar:.2f}"
        )

    return {
        "title": f"MetisXdge — {len(signals)} combo(s) | Best: +{best.odds_gap}pt gap",
        "body": "\n".join(lines) + f"\n{datetime.utcnow().strftime('%H:%M UTC')}",
        "priority": "high" if best.odds_gap >= 150 else "default",
        "tags": "bar_chart,moneybag",
    }


class NtfyAlerter:
    def __init__(self, topic: str, base_url: str = NTFY_BASE):
        self.url   = f"{base_url.rstrip('/')}/{topic}"
        self.topic = topic

    def _send(self, payload: dict) -> bool:
        try:
            resp = requests.post(
                self.url,
                headers={
                    "Title":        payload["title"],
                    "Priority":     payload["priority"],
                    "Tags":         payload["tags"],
                    "Content-Type": "text/plain",
                },
                data=payload["body"].encode("utf-8"),
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"ntfy sent: {payload['title']}")
            return True
        except Exception as e:
            logger.error(f"ntfy failed: {e}")
            return False

    def send_combo(self, signal) -> bool:
        return self._send(format_combo_notification(signal))

    def send_batch(self, signals: list) -> bool:
        if not signals:
            return True
        if len(signals) == 1:
            return self.send_combo(signals[0])
        return self._send(format_batch_notification(signals))

    def test(self) -> bool:
        return self._send({
            "title": "MetisXdge online",
            "body":  "Combo scanner is running. Watching for mispriced Kalshi combos.",
            "priority": "default",
            "tags": "white_check_mark",
        })
