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
    seen  = set()

    for s in signals:
        key = f"{s.kalshi_american}_{s.n_legs}"
        if key in seen:
            continue
        seen.add(key)

        # Clean team names from legs
        teams = " + ".join(leg.display_name for leg in s.legs)
        lines.append(_safe(
            f"[{s.n_legs}L] {teams}\n"
            f"  Kalshi {_fmt(s.kalshi_american)} | Fair {_fmt(s.fair_american)} | "
            f"+{s.odds_gap}pt gap | EV ${s.ev_per_dollar:.2f}"
        ))
        if len(lines) >= 5:
            break

    return {
        "title":    _safe(f"MetisXdge: {len(seen)} combo(s) | Best +{best.odds_gap}pt gap"),
        "body":     "\n\n".join(lines) + f"\n\n{datetime.utcnow().strftime('%H:%M UTC')}",
        "priority": "urgent" if best.odds_gap >= 200 else "high",
        "tags":     "moneybag,bar_chart",
    }


def format_combo_notification(signal) -> dict:
    teams = " + ".join(leg.display_name for leg in signal.legs)
    title = _safe(f"[{signal.n_legs}L] {teams} | +{signal.odds_gap}pt gap")
    body  = _safe(
        f"Kalshi: {_fmt(signal.kalshi_american)} | Fair: {_fmt(signal.fair_american)}\n"
        f"EV per $1: ${signal.ev_per_dollar:.2f} | Edge: {signal.edge_pct:.1f}%\n\n"
        + "\n".join(
            f"  {leg.display_name}: {leg.kalshi_price:.0f}c (fair {leg.fair_prob:.1%})"
            for leg in signal.legs
        )
        + f"\n\n{datetime.utcnow().strftime('%H:%M UTC')}"
    )
    priority = "urgent" if signal.odds_gap >= 200 else "high"
    return {"title": title, "body": body, "priority": priority, "tags": "moneybag"}


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
            "title":    "MetisXdge online",
            "body":     "Game-winner combo scanner running.",
            "priority": "default",
            "tags":     "white_check_mark",
        })
