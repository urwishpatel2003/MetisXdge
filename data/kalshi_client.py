# data/kalshi_client.py
"""
Kalshi REST API Client — RSA-PSS Auth (2026)
Production URL: https://api.elections.kalshi.com/trade-api/v2
Auth: RSA-PSS signed headers per request
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

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, api_key_id: str, private_key_pem: str, ssl_verify: bool = True):
        """
        Args:
            api_key_id:      The Key ID from Kalshi Settings → API Keys
            private_key_pem: Full contents of kalshi_private.pem
            ssl_verify:      Set False only if SSL issues on network
        """
        self.api_key_id = api_key_id
        self.ssl_verify = ssl_verify
        self.session    = requests.Session()
        self.session.verify = ssl_verify

        # Load private key
        self.private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
            backend=default_backend()
        )
        logger.info("Kalshi client initialized with RSA-PSS auth")

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        """Generate RSA-PSS signature for request headers"""
        message = f"{timestamp}{method}{path}"
        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str) -> dict:
        """Build signed auth headers for a request"""
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(timestamp, method.upper(), path)
        return {
            "KALSHI-ACCESS-KEY":       self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
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

    def get_sports_markets(self) -> list[KalshiContract]:
        contracts = []
        cursor    = None

        while True:
            data    = self.get_markets(limit=200, cursor=cursor)
            markets = data.get("markets", [])

            for m in markets:
                try:
                    # March 2026: prices are now dollar strings e.g. "0.6500"
                    yes_ask_raw = m.get("yes_ask", "0.50") or "0.50"
                    yes_ask = float(yes_ask_raw) * 100  # convert to cents
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
                    logger.warning(f"Skipping market {m.get('ticker')}: {e}")

            cursor = data.get("cursor")
            if not cursor or not markets:
                break

        logger.info(f"Fetched {len(contracts)} Kalshi contracts")
        return contracts

    def get_balance(self) -> dict:
        return self._get("portfolio/balance")
