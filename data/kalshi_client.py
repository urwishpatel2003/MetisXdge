# data/kalshi_client.py
"""
Kalshi REST API Client — API Key Auth (2026)
"""

import requests
import logging
from datetime import datetime
from typing import Optional
from core.models import KalshiContract

logger = logging.getLogger(__name__)

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, api_key: str, ssl_verify: bool = True):
        self.session = requests.Session()
        self.session.verify = ssl_verify
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        })
        logger.info("Kalshi client initialized with API key auth")

    def _get(self, endpoint: str, params: dict = {}) -> dict:
        resp = self.session.get(f"{BASE_URL}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_markets(self, status: str = "open", limit: int = 200, cursor: Optional[str] = None) -> dict:
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("markets", params)

    def get_sports_markets(self) -> list[KalshiContract]:
        contracts = []
        cursor = None

        while True:
            data    = self.get_markets(limit=200, cursor=cursor)
            markets = data.get("markets", [])

            for m in markets:
                try:
                    yes_ask = m.get("yes_ask", 50) or 50
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
