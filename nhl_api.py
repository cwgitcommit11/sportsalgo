"""NHL public API client — standings, team stats, schedule, scores."""

import logging
from datetime import date, datetime

import requests

from config import (
    STANDINGS_URL,
    SCHEDULE_URL,
    SCORE_URL,
    TEAM_SCHEDULE_URL,
    TEAM_STATS_URL,
)

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SportsAlgo/1.0"})

TIMEOUT = 15


def _get(url: str) -> dict | None:
    """GET JSON from *url*, returning None on failure."""
    try:
        resp = _SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.warning("API request failed: %s — %s", url, exc)
        return None


# ── Standings ────────────────────────────────────────────────────────────

def fetch_standings() -> list[dict]:
    """Return a list of team standings dicts from /v1/standings/now."""
    data = _get(STANDINGS_URL)
    if not data:
        return []
    return data.get("standings", [])


# ── Team Aggregate Stats ────────────────────────────────────────────────

def _build_name_to_abbrev(standings: list[dict]) -> dict[str, str]:
    """Build {teamFullName: abbrev} from standings data."""
    mapping: dict[str, str] = {}
    for team in standings:
        full_name = team.get("teamName", {}).get("default", "")
        abbrev = team.get("teamAbbrev", {}).get("default", "")
        if full_name and abbrev:
            mapping[full_name] = abbrev
    return mapping


def fetch_team_stats(standings: list[dict] | None = None) -> dict[str, dict]:
    """Return {teamAbbrev: stats_dict} from the stats summary API.

    Keys include: powerPlayPct, penaltyKillPct, shotsForPerGame, shotsAgainstPerGame.
    """
    data = _get(TEAM_STATS_URL)
    if not data:
        return {}

    # Build name→abbrev map from standings (stats API only has teamFullName)
    name_map: dict[str, str] = {}
    if standings:
        name_map = _build_name_to_abbrev(standings)

    out: dict[str, dict] = {}
    for row in data.get("data", []):
        full_name = row.get("teamFullName", "")
        abbrev = name_map.get(full_name, "")
        if not abbrev:
            log.debug("Could not map team name: %s", full_name)
            continue
        out[abbrev] = row
    return out


# ── Today's Schedule ────────────────────────────────────────────────────

def fetch_todays_games() -> list[dict]:
    """Return today's games from /v1/schedule/now.

    Each game dict contains at least:
      - homeTeam.abbrev, awayTeam.abbrev
      - startTimeUTC, gameState
    """
    data = _get(SCHEDULE_URL)
    if not data:
        return []
    today_str = date.today().isoformat()
    for week in data.get("gameWeek", []):
        if week.get("date") == today_str:
            # Only return regular-season (gameType 2) or playoff (gameType 3) games
            return [g for g in week.get("games", []) if g.get("gameType") in (2, 3)]
    return []


# ── Scores for a Given Date ─────────────────────────────────────────────

def fetch_scores(game_date: date) -> list[dict]:
    """Return finished-game results for *game_date* (YYYY-MM-DD)."""
    url = f"{SCORE_URL}/{game_date.isoformat()}"
    data = _get(url)
    if not data:
        return []
    return data.get("games", [])


# ── Team Season Schedule (for rest-day detection) ───────────────────────

def fetch_team_schedule(team_abbrev: str) -> list[dict]:
    """Return the full season schedule for *team_abbrev*.

    Used to detect back-to-backs and 3-in-4 situations.
    """
    url = f"{TEAM_SCHEDULE_URL}/{team_abbrev}/now"
    data = _get(url)
    if not data:
        return []
    return data.get("games", [])


def game_dates_from_schedule(schedule: list[dict]) -> list[date]:
    """Extract sorted game dates from a team schedule."""
    dates: list[date] = []
    for g in schedule:
        raw = g.get("gameDate")
        if raw:
            try:
                dates.append(datetime.strptime(raw, "%Y-%m-%d").date())
            except ValueError:
                pass
    dates.sort()
    return dates
