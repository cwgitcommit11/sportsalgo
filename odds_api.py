"""The Odds API client — fetch NHL moneyline odds."""

import logging
import os
import unicodedata

import requests

log = logging.getLogger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_SPORT = "icehockey_nhl"
TIMEOUT = 15

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SportsAlgo/1.0"})


def fetch_nhl_odds(name_to_abbrev: dict[str, str]) -> dict[str, dict]:
    """Return {game_key: {"home_odds": int, "away_odds": int}} for today's NHL games.

    game_key format: "AWAY @ HOME" (matches predict_game output).
    Averages moneyline prices across all available US bookmakers.
    Returns an empty dict if the API key is missing or the request fails.
    """
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        log.warning("ODDS_API_KEY not set — skipping odds fetch")
        return {}

    url = f"{_ODDS_API_BASE}/sports/{_SPORT}/odds/"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
    }

    try:
        resp = _SESSION.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("Odds API request failed: %s", exc)
        return {}

    remaining = resp.headers.get("x-requests-remaining", "?")
    log.info("Odds API: %d games fetched (%s requests remaining this month)", len(data), remaining)


    odds_map: dict[str, dict] = {}
    for game in data:
        home_full = game.get("home_team", "")
        away_full = game.get("away_team", "")
        # Try exact match first, then accent-normalized fallback
        home_abbrev = name_to_abbrev.get(home_full) or name_to_abbrev.get(
            unicodedata.normalize("NFKD", home_full).encode("ascii", "ignore").decode(), ""
        )
        away_abbrev = name_to_abbrev.get(away_full) or name_to_abbrev.get(
            unicodedata.normalize("NFKD", away_full).encode("ascii", "ignore").decode(), ""
        )

        if not home_abbrev or not away_abbrev:
            log.debug("Could not map odds team names: '%s' / '%s'", home_full, away_full)
            continue

        home_prices: list[float] = []
        away_prices: list[float] = []
        for bm in game.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome["name"] == home_full:
                        home_prices.append(outcome["price"])
                    elif outcome["name"] == away_full:
                        away_prices.append(outcome["price"])

        if not home_prices or not away_prices:
            continue

        odds_map[f"{away_abbrev} @ {home_abbrev}"] = {
            "home_odds": round(sum(home_prices) / len(home_prices)),
            "away_odds": round(sum(away_prices) / len(away_prices)),
        }

    log.info("Matched odds for %d of %d games", len(odds_map), len(data))
    return odds_map
