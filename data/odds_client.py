# data/odds_client.py
"""
The Odds API Client
Fetches sharp book lines (Pinnacle, Betfair) for fair value computation.
"""

import requests
import logging
from datetime import datetime
from typing import Optional
from core.models import OddsLine
from core.fair_value import american_to_implied_prob, remove_vig_two_way

logger = logging.getLogger(__name__)


class OddsClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url

    def _get(self, endpoint: str, params: dict = {}) -> dict | list:
        params["apiKey"] = self.api_key
        resp = requests.get(f"{self.base_url}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_sports(self) -> list:
        """List all available sports"""
        return self._get("sports")

    def get_odds(
        self,
        sport: str,
        markets: str = "h2h,spreads,totals",
        books: Optional[list] = None,
        regions: str = "us"
    ) -> list[OddsLine]:
        """
        Fetch odds for a sport from specified books.
        Returns list of OddsLine objects with vig removed.
        """
        params = {
            "sport": sport,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if books:
            params["bookmakers"] = ",".join(books)

        data = self._get(f"sports/{sport}/odds", params)
        lines = []

        for game in data:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            commence = game.get("commence_time", "")

            for bookmaker in game.get("bookmakers", []):
                book_key = bookmaker.get("key", "")

                for market in bookmaker.get("markets", []):
                    market_key = market.get("key", "")
                    outcomes = market.get("outcomes", [])

                    if market_key == "h2h" and len(outcomes) == 2:
                        # Two-way market — remove vig
                        odds_map = {o["name"]: o["price"] for o in outcomes}
                        teams = list(odds_map.keys())
                        if len(teams) == 2:
                            fp1, fp2 = remove_vig_two_way(
                                odds_map[teams[0]], odds_map[teams[1]]
                            )
                            for team, fp in zip(teams, [fp1, fp2]):
                                lines.append(OddsLine(
                                    book=book_key,
                                    market_key=market_key,
                                    sport=sport,
                                    home_team=home,
                                    away_team=away,
                                    outcome=team,
                                    american_odds=odds_map[team],
                                    implied_prob=fp,
                                    raw_prob=american_to_implied_prob(odds_map[team]),
                                    timestamp=datetime.utcnow()
                                ))

                    elif market_key in ("spreads", "totals"):
                        # Multi-outcome — multiplicative vig removal
                        raw_probs = [
                            american_to_implied_prob(o["price"])
                            for o in outcomes
                        ]
                        total = sum(raw_probs)
                        for o, rp in zip(outcomes, raw_probs):
                            lines.append(OddsLine(
                                book=book_key,
                                market_key=market_key,
                                sport=sport,
                                home_team=home,
                                away_team=away,
                                outcome=o.get("name", ""),
                                american_odds=o["price"],
                                implied_prob=rp / total,
                                raw_prob=rp,
                                timestamp=datetime.utcnow()
                            ))

        logger.info(f"Fetched {len(lines)} odds lines for {sport}")
        return lines

    def get_sharp_lines(
        self,
        sport: str,
        sharp_books: list = None
    ) -> list[OddsLine]:
        """Convenience method for just sharp book lines"""
        if sharp_books is None:
            sharp_books = ["pinnacle", "betfair"]
        return self.get_odds(sport, books=sharp_books)
