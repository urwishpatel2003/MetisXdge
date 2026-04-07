# alerts/ntfy.py
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
NTFY_BASE = "https://ntfy.sh"


def _fmt(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def format_batch_notification(signals: list) -> dict:
    best  = signals[0]
    lines = []

    for s in signals[:5]:
        # Show the actual leg titles so you know what to trade
        leg_names = " + ".join(
            leg.kalshi_title[:30].strip() for leg in s.legs
        )
        lines.append(_safe(
            f"[{s.n_legs}L] {_fmt(s.kalshi_american)} vs {_fmt(s.fair_american)} "
            f"| +{s.odds_gap}pt | EV ${s.ev_per_dollar:.2f}\n"
            f"  {leg_names}"
        ))

    body = "\n\n".join(lines) + f"\n\n{datetime.utcnow().strftime('%H:%M UTC')}"

    return {
        "title":    _safe(f"MetisXdge: {len(signals)} signal(s) | Best +{best.odds_gap}pt gap"),
        "body":     body,
        "priority": "urgent" if best.odds_gap >= 200 else "high",
        "tags":     "moneybag,bar_chart",
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

    def send_batch(self, signals: list) -> bool:
        if not signals:
            return True
        return self._send(format_batch_notification(signals))

    def test(self) -> bool:
        return self._send({
            "title":    "MetisXdge online",
            "body":     "Combo scanner is running.",
            "priority": "default",
            "tags":     "white_check_mark",
        })
