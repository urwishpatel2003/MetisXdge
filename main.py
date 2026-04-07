# main.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import io, time, logging
from datetime import datetime

from config.settings import (
    ODDS_API_KEY, ODDS_BASE_URL,
    KALSHI_EMAIL, KALSHI_PASSWORD,
    NTFY_TOPIC, NTFY_BASE_URL,
    TARGET_SPORTS, SHARP_BOOKS,
    MIN_ODDS_GAP, MIN_EV, MIN_FAIR_PROB,
    MAX_LEGS, SCAN_INTERVAL_SECONDS,
    LOG_LEVEL, LOG_FILE, SSL_VERIFY
)
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

from data.kalshi_client import KalshiClient
from data.odds_client import OddsClient
from alerts.ntfy import NtfyAlerter
from scanners.combo_scanner import match_legs, scan_combos

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(
            stream=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        ),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("MetisXdge")

alerted_combos: set[str] = set()


def combo_fingerprint(signal) -> str:
    return "|".join(sorted(leg.kalshi_ticker for leg in signal.legs))


def run_scan(kalshi, odds, ntfy):
    logger.info("=" * 55)
    logger.info("Scanning Kalshi for mispriced combos...")

    contracts = kalshi.get_sports_markets()

    all_lines = []
    for sport in TARGET_SPORTS:
        try:
            lines = odds.get_sharp_lines(sport, SHARP_BOOKS)
            all_lines.extend(lines)
        except Exception as e:
            logger.warning(f"Odds fetch failed for {sport}: {e}")

    logger.info(f"{len(contracts)} Kalshi contracts | {len(all_lines)} sharp lines")

    if not contracts or not all_lines:
        logger.warning("Insufficient data — skipping scan")
        return

    matched = match_legs(contracts, all_lines, min_fair_prob=MIN_FAIR_PROB)

    if len(matched) < 2:
        logger.warning(f"Only {len(matched)} matched legs — need at least 2 for combos")
        return

    signals = scan_combos(
        matched,
        min_legs=2,
        max_legs=MAX_LEGS,
        min_odds_gap=MIN_ODDS_GAP,
        min_ev=MIN_EV,
    )

    if not signals:
        logger.info("No mispriced combos found this scan")
        return

    new_signals = []
    for signal in signals:
        fp = combo_fingerprint(signal)
        if fp in alerted_combos:
            continue
        alerted_combos.add(fp)
        new_signals.append(signal)

    if new_signals:
        logger.info(f"Alerting {len(new_signals)} new combo(s)")
        ntfy.send_batch(new_signals)
        best = new_signals[0]
        logger.info(
            f"Best: {best.n_legs} legs | "
            f"Kalshi {best.kalshi_american:+d} vs Fair {best.fair_american:+d} | "
            f"+{best.odds_gap}pt gap | EV ${best.ev_per_dollar:.3f}"
        )
    else:
        logger.info(f"{len(signals)} combos found — all already alerted")


def main():
    logger.info("MetisXdge starting up...")
    logger.info(f"Max legs: {MAX_LEGS} | Min gap: {MIN_ODDS_GAP}pts | Sports: {TARGET_SPORTS}")
    logger.info(f"KALSHI_EMAIL: '{KALSHI_EMAIL[:4]}...' len={len(KALSHI_EMAIL)}")
    logger.info(f"KALSHI_PASSWORD len={len(KALSHI_PASSWORD)}")

    kalshi = KalshiClient(KALSHI_EMAIL, KALSHI_PASSWORD, ssl_verify=SSL_VERIFY)
    odds   = OddsClient(ODDS_API_KEY, ODDS_BASE_URL)
    ntfy   = NtfyAlerter(topic=NTFY_TOPIC, base_url=NTFY_BASE_URL)

    ntfy.test()

    scan_count = 0
    while True:
        try:
            scan_count += 1
            logger.info(f"Scan #{scan_count}")
            run_scan(kalshi, odds, ntfy)
        except KeyboardInterrupt:
            logger.info("Stopped.")
            break
        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
