# alerts/sms.py
"""
Twilio SMS Alert System
Replaces Discord — sends concise, actionable SMS alerts per EdgeSignal.
Stale price signals get URGENT prefix and fire immediately.
Arb + correlation signals are batched into a single SMS per scan.
"""

import logging
from datetime import datetime
from twilio.rest import Client
from core.models import EdgeSignal, SignalType

logger = logging.getLogger(__name__)

# Signal urgency — stale price fires solo, others batch
URGENT_TYPES = {SignalType.STALE_PRICE}

SIGNAL_LABELS = {
    SignalType.ARB:         "ARB",
    SignalType.CORRELATION: "COMBO",
    SignalType.STALE_PRICE: "STALE ⚡",
}


def _format_american(odds: int) -> str:
    return f"+{odds}" if odds > 0 else str(odds)


def format_signal_sms(signal: EdgeSignal) -> str:
    """
    Format a single EdgeSignal as a concise SMS message.
    Kept under 160 chars where possible for single-segment delivery.
    """
    label = SIGNAL_LABELS[signal.signal_type]
    side_price = f"{signal.side} @ {signal.kalshi_prob * 100:.0f}¢"
    odds_line = f"Kalshi {_format_american(signal.kalshi_odds_american)} | Fair {_format_american(signal.fair_odds_american)}"

    if signal.signal_type == SignalType.STALE_PRICE:
        expires_min = (
            int((signal.expires_at - datetime.utcnow()).seconds / 60)
            if signal.expires_at else "~10"
        )
        return (
            f"🔱 METISEDGE [{label}]\n"
            f"{signal.kalshi_title[:40]}\n"
            f"{side_price} | Edge: {signal.edge_pct:.1f}%\n"
            f"{odds_line}\n"
            f"Stake: ${signal.suggested_stake_usd:.0f} | "
            f"Expires: ~{expires_min}min\n"
            f"Sharp moved: {_format_american(signal.sharp_odds)} ({signal.sharp_book})"
        )

    elif signal.signal_type == SignalType.CORRELATION:
        legs_summary = " + ".join(
            leg.kalshi_title[:20] for leg in signal.legs[:3]
        ) if signal.legs else signal.kalshi_title[:50]
        return (
            f"🔱 METISEDGE [{label}]\n"
            f"{legs_summary}\n"
            f"Combined: {_format_american(signal.kalshi_odds_american)} | "
            f"Fair: {_format_american(signal.fair_odds_american)}\n"
            f"Edge: {signal.edge_pct:.1f}% | Stake: ${signal.suggested_stake_usd:.0f}"
        )

    else:  # ARB
        return (
            f"🔱 METISEDGE [{label}]\n"
            f"{signal.kalshi_title[:45]}\n"
            f"{side_price} | Edge: {signal.edge_pct:.1f}%\n"
            f"{odds_line}\n"
            f"Stake: ${signal.suggested_stake_usd:.0f} ({signal.sharp_book})"
        )


def format_batch_sms(signals: list[EdgeSignal]) -> str:
    """
    Batch multiple non-urgent signals into a single SMS summary.
    Used for arb + correlation signals found in the same scan.
    """
    lines = [f"🔱 METISEDGE — {len(signals)} signal(s) found\n"]

    for s in sorted(signals, key=lambda x: x.edge_pct, reverse=True)[:5]:
        label = SIGNAL_LABELS[s.signal_type]
        lines.append(
            f"[{label}] {s.kalshi_title[:30]} | "
            f"Edge: {s.edge_pct:.1f}% | "
            f"${s.suggested_stake_usd:.0f}"
        )

    lines.append(f"\n{datetime.utcnow().strftime('%H:%M UTC')}")
    return "\n".join(lines)


class SMSAlerter:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
    ):
        self.client = Client(account_sid, auth_token)
        self.from_number = from_number
        self.to_number = to_number

    def _send(self, body: str) -> bool:
        try:
            msg = self.client.messages.create(
                body=body,
                from_=self.from_number,
                to=self.to_number
            )
            logger.info(f"SMS sent: {msg.sid}")
            return True
        except Exception as e:
            logger.error(f"SMS failed: {e}")
            return False

    def send_signal(self, signal: EdgeSignal) -> bool:
        """Send an individual signal alert (used for urgent/stale price)"""
        body = format_signal_sms(signal)
        return self._send(body)

    def send_batch(self, signals: list[EdgeSignal]) -> bool:
        """Send a batched summary of multiple non-urgent signals"""
        if not signals:
            return True
        body = format_batch_sms(signals)
        return self._send(body)

    def dispatch(self, signals: list[EdgeSignal]):
        """
        Smart dispatch:
        - Urgent signals (stale price) → individual SMS immediately
        - Non-urgent signals (arb, correlation) → single batch SMS
        """
        urgent = [s for s in signals if s.signal_type in URGENT_TYPES]
        non_urgent = [s for s in signals if s.signal_type not in URGENT_TYPES]

        for signal in urgent:
            self.send_signal(signal)

        if non_urgent:
            self.send_batch(non_urgent)
