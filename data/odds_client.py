# data/odds_client.py
import requests
import logging
from datetime import datetime
from core.models import OddsLine
from core.fair_value import american_to_implied_prob, remove_vig_two_way

logger = logging.getLogger(__name__)


class OddsClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key  = api_key
        self.base_url = base_url

    def _get(self, endpoint: str, params: dict = {}) -> list:
        params["apiKey"] = self.api_key
        resp = requests.get(f"{self.base_url}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_odds(self, sport: str, markets: str = "h2h,spreads,totals", books: list = None) -> list[OddsLine]:
        params = {
            "sport":       sport,
            "regions":     "us",
            "markets":     markets,
            "oddsFormat":  "american",
            "dateFormat":  "iso",
        }
        if books:
            params["bookmakers"] = ",".join(books)

        data = self._get(f"sports/{sport}/odds", params)
        lines = []

        for game in data:
            home = game.get("home_team", "")
            away = game.get("away_team", "")

            for bm in game.get("bookmakers", []):
                book = bm.get("key", "")

                for market in bm.get("markets", []):
                    mkey     = market.get("key", "")
                    outcomes = market.get("outcomes", [])

                    if mkey == "h2h" and len(outcomes) == 2:
                        odds_map = {o["name"]: o["price"] for o in outcomes}
                        teams    = list(odds_map.keys())
                        if len(teams) == 2:
                            fp1, fp2 = remove_vig_two_way(odds_map[teams[0]], odds_map[teams[1]])
                            for team, fp in zip(teams, [fp1, fp2]):
                                l = OddsLine(
                                    book=book, market_key=mkey, sport=sport,
                                    home_team=home, away_team=away,
                                    outcome=team, american_odds=odds_map[team],
                                    implied_prob=fp,
                                    raw_prob=american_to_implied_prob(odds_map[team]),
                                    timestamp=datetime.utcnow()
                                )
                                lines.append(l)

                    elif mkey in ("spreads", "totals"):
                        raw_probs = [american_to_implied_prob(o["price"]) for o in outcomes]
                        total     = sum(raw_probs)
                        for o, rp in zip(outcomes, raw_probs):
                            l = OddsLine(
                                book=book, market_key=mkey, sport=sport,
                                home_team=home, away_team=away,
                                outcome=o.get("name", ""),
                                american_odds=o["price"],
                                implied_prob=rp / total,
                                raw_prob=rp,
                                timestamp=datetime.utcnow()
                            )
                            # Store point value for spread/total matching
                            l.point = o.get("point")
                            lines.append(l)

        logger.info(f"Fetched {len(lines)} odds lines for {sport}")
        return lines

    def get_sharp_lines(self, sport: str, sharp_books: list = None, markets: str = "h2h,spreads,totals") -> list[OddsLine]:
        if sharp_books is None:
            sharp_books = ["pinnacle", "betfair"]
        return self.get_odds(sport, markets=markets, books=sharp_books)
