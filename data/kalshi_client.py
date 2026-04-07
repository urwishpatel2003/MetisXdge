# data/kalshi_client.py
"""
Kalshi REST API Client — RSA-PSS Auth (2026)
Targets individual game markets: KXMLBGAME, KXNBAGAME, KXNHLGAME etc.
"""

import time
import base64
import logging
import requests
from datetime import datetime
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from core.models import KalshiContract

logger = logging.getLogger(__name__)

BASE_URL  = "https://api.elections.kalshi.com/trade-api/v2"
MAX_PAGES = 20

# Individual game market prefixes — these are the real tradeable markets
# Series tickers for individual game markets (from URL structure)
GAME_SERIES = [
    'kxmlbgame',    # MLB: /markets/kxmlbgame/
    'kxnbagame',    # NBA
    'kxnhlgame',    # NHL
    'kxnflgame',    # NFL
    'kxsoccer',     # Soccer
    'kxncaabgame',  # NCAAB
]


def _fix_pem(pem: str) -> str:
    """Reconstruct PEM from base64 or mangled env var."""
    import base64 as b64
    pem = pem.strip()
    try:
        decoded = b64.b64decode(pem).decode("utf-8")
        if "-----BEGIN" in decoded:
            return decoded
    except Exception:
        pass
    pem = pem.replace("\\n", "\n")
    if "-----BEGIN" in pem:
        return pem
    body       = pem.replace(" ", "").replace("\n", "")
    body_lines = "\n".join(body[i:i+64] for i in range(0, len(body), 64))
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body_lines}\n-----END RSA PRIVATE KEY-----"


class KalshiClient:
    def __init__(self, api_key_id: str, private_key_pem: str, ssl_verify: bool = True):
        self.api_key_id = api_key_id
        self.ssl_verify = ssl_verify
        self.session    = requests.Session()
        self.session.verify = ssl_verify

        pem = _fix_pem(private_key_pem)
        logger.info(f"PEM header: {pem[:40]}")
        self.private_key = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None, backend=default_backend()
        )
        logger.info("Kalshi client initialized with RSA-PSS auth")

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        message   = f"{timestamp}{method}{path}"
        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY":       self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self._sign(timestamp, method.upper(), path),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type":            "application/json",
        }

    def _get(self, endpoint: str, params: dict = {}) -> dict:
        path = f"/trade-api/v2/{endpoint}"
        resp = self.session.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            headers=self._headers("GET", path)
        )
        resp.raise_for_status()
        return resp.json()

    def get_markets(self, status: str = "open", limit: int = 200, cursor: Optional[str] = None) -> dict:
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("markets", params)

    def get_series_markets(self, series_ticker: str) -> list[KalshiContract]:
        """Fetch all open markets for a specific series (e.g. kxmlbgame)"""
        contracts = []
        cursor    = None

        while True:
            params = {"status": "open", "limit": 200, "series_ticker": series_ticker}
            if cursor:
                params["cursor"] = cursor

            path = "/trade-api/v2/markets"
            resp = self.session.get(
                f"{BASE_URL}/markets",
                params=params,
                headers=self._headers("GET", path)
            )
            resp.raise_for_status()
            data    = resp.json()
            markets = data.get("markets", [])

            for m in markets:
                try:
                    yes_ask_raw = m.get("yes_ask_dollars") or m.get("yes_bid_dollars") or m.get("last_price_dollars")
                    if yes_ask_raw is None or float(yes_ask_raw) == 0:
                        continue
                    yes_ask = float(yes_ask_raw) * 100
                    no_ask  = 100 - yes_ask
                    close_time_str = m.get("close_time", "")
                    close_time = (
                        datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                        if close_time_str else datetime.utcnow()
                    )
                    contracts.append(KalshiContract(
                        ticker        = m.get("ticker", ""),
                        title         = m.get("title", ""),
                        yes_price     = yes_ask,
                        no_price      = no_ask,
                        yes_prob      = yes_ask / 100,
                        volume        = m.get("volume", 0) or 0,
                        open_interest = m.get("open_interest", 0) or 0,
                        close_time    = close_time,
                    ))
                except Exception as e:
                    logger.warning(f"Skipping {m.get('ticker')}: {e}")

            cursor = data.get("cursor")
            if not cursor or not markets:
                break
            time.sleep(0.3)

        return contracts

    def get_sports_markets(self) -> list[KalshiContract]:
        """Fetch individual game markets via series endpoint"""
        contracts = []
        for series in GAME_SERIES:
            try:
                series_contracts = self.get_series_markets(series)
                logger.info(f"Series {series}: {len(series_contracts)} contracts")
                for c in series_contracts[:2]:
                    logger.info(f"  SAMPLE: {c.ticker} | {c.yes_price:.1f}c | {c.title[:50]}")
                contracts.extend(series_contracts)
            except Exception as e:
                logger.warning(f"Failed to fetch series {series}: {e}")
            time.sleep(0.5)

        logger.info(f"Total: {len(contracts)} individual game contracts")
        return contracts

    def get_balance(self) -> dict:
        return self._get("portfolio/balance")
