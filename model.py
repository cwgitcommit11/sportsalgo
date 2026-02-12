"""Prediction engine — 7-factor weighted model with situational adjustments."""

import logging
from datetime import date, timedelta

from config import (
    WEIGHTS,
    HOME_ICE_BONUS,
    BACK_TO_BACK_PENALTY,
    THREE_IN_FOUR_PENALTY,
    EXTENDED_REST_BONUS,
    STAR_THRESHOLDS,
    SKIP_THRESHOLD,
    EARLY_SEASON_GP,
)
from nhl_api import fetch_team_schedule, game_dates_from_schedule

log = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────

def normalize(value: float, all_values: list[float]) -> float:
    """Min-max normalize *value* into [0, 1] given *all_values*."""
    lo, hi = min(all_values), max(all_values)
    if hi == lo:
        return 0.5
    return (value - lo) / (hi - lo)


def _safe(val, default=0.0):
    """Return float(val) or *default* if val is None / non-numeric."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── Factor Extraction ────────────────────────────────────────────────────

def _extract_factors(team: dict, stats: dict | None) -> dict:
    """Compute the raw (unnormalized) factor values for one team.

    *team* is a standings entry; *stats* is the optional stats-API row.
    """
    gp = max(_safe(team.get("gamesPlayed"), 1), 1)
    gf = _safe(team.get("goalFor"))
    ga = _safe(team.get("goalAgainst"))
    goal_diff_per_gp = (gf - ga) / gp

    point_pct = _safe(team.get("pointPctg"))

    # L10 point %
    l10w = _safe(team.get("l10Wins"))
    l10l = _safe(team.get("l10Losses"))
    l10o = _safe(team.get("l10OtLosses"))
    l10_gp = l10w + l10l + l10o
    recent_form = (l10w * 2 + l10o) / (l10_gp * 2) if l10_gp else 0.5

    # Home / road win%
    hw = _safe(team.get("homeWins"))
    hl = _safe(team.get("homeLosses"))
    ho = _safe(team.get("homeOtLosses"))
    rw = _safe(team.get("roadWins"))
    rl = _safe(team.get("roadLosses"))
    ro = _safe(team.get("roadOtLosses"))
    home_gp = hw + hl + ho
    road_gp = rw + rl + ro
    home_pct = (hw * 2 + ho) / (home_gp * 2) if home_gp else 0.5
    road_pct = (rw * 2 + ro) / (road_gp * 2) if road_gp else 0.5

    # Special teams (from stats API, fallback 0.5)
    if stats:
        pp_pct = _safe(stats.get("powerPlayPct"), 0.5)
        pk_pct = _safe(stats.get("penaltyKillPct"), 0.5)
        special_teams = (pp_pct + pk_pct) / 2.0
        shots_for = _safe(stats.get("shotsForPerGame"), 30)
        shots_against = _safe(stats.get("shotsAgainstPerGame"), 30)
        shot_diff_per_gp = shots_for - shots_against
    else:
        special_teams = 0.5
        shot_diff_per_gp = 0.0

    # Streak momentum: encode W streaks positive, L negative, OT as half
    streak_code = team.get("streakCode", "")
    streak_count = _safe(team.get("streakCount"))
    if streak_code == "W":
        streak = streak_count
    elif streak_code == "L":
        streak = -streak_count
    else:
        streak = streak_count * 0.5  # OT streak

    return {
        "goal_diff_per_gp": goal_diff_per_gp,
        "point_pct": point_pct,
        "recent_form": recent_form,
        "home_pct": home_pct,
        "road_pct": road_pct,
        "special_teams": special_teams,
        "shot_diff_per_gp": shot_diff_per_gp,
        "streak_momentum": streak,
        "gp": gp,
    }


# ── Team Ratings ─────────────────────────────────────────────────────────

def compute_team_ratings(
    standings: list[dict],
    team_stats: dict[str, dict],
) -> dict[str, dict]:
    """Build {abbrev: {factors…, composite}} for every team in standings."""

    # Extract raw factors per team
    raw: dict[str, dict] = {}
    for team in standings:
        abbrev = team.get("teamAbbrev", {}).get("default", "???")
        stats = team_stats.get(abbrev)
        raw[abbrev] = _extract_factors(team, stats)

    if not raw:
        return {}

    # Collect all values per factor for normalization
    factor_keys = [
        "goal_diff_per_gp", "point_pct", "recent_form",
        "special_teams", "shot_diff_per_gp", "streak_momentum",
        # home/road handled separately
    ]
    all_vals: dict[str, list[float]] = {k: [] for k in factor_keys}
    all_vals["home_pct"] = []
    all_vals["road_pct"] = []
    for r in raw.values():
        for k in factor_keys:
            all_vals[k].append(r[k])
        all_vals["home_pct"].append(r["home_pct"])
        all_vals["road_pct"].append(r["road_pct"])

    # Normalize and compute composite
    ratings: dict[str, dict] = {}
    for abbrev, r in raw.items():
        normed = {}
        for k in factor_keys:
            normed[k] = normalize(r[k], all_vals[k])

        normed["home_pct"] = normalize(r["home_pct"], all_vals["home_pct"])
        normed["road_pct"] = normalize(r["road_pct"], all_vals["road_pct"])

        composite = (
            WEIGHTS["goal_diff_per_gp"] * normed["goal_diff_per_gp"]
            + WEIGHTS["point_pct"] * normed["point_pct"]
            + WEIGHTS["recent_form"] * normed["recent_form"]
            + WEIGHTS["special_teams"] * normed["special_teams"]
            + WEIGHTS["shot_diff_per_gp"] * normed["shot_diff_per_gp"]
            + WEIGHTS["streak_momentum"] * normed["streak_momentum"]
            # home_road_split weight is applied contextually in predict_game
        )
        ratings[abbrev] = {**normed, "composite": composite, "gp": r["gp"]}

    return ratings


# ── Rest / Schedule Situations ───────────────────────────────────────────

def detect_rest_situation(
    team_abbrev: str,
    game_date: date,
    schedule_cache: dict[str, list[date]],
) -> dict:
    """Detect B2B, 3-in-4, or extended rest for *team_abbrev* on *game_date*.

    Returns {"back_to_back": bool, "three_in_four": bool, "rest_days": int}.
    """
    result = {"back_to_back": False, "three_in_four": False, "rest_days": 1}

    if team_abbrev not in schedule_cache:
        sched = fetch_team_schedule(team_abbrev)
        schedule_cache[team_abbrev] = game_dates_from_schedule(sched)

    dates = schedule_cache[team_abbrev]
    if not dates:
        log.warning("No schedule data for %s — skipping rest adjustments", team_abbrev)
        return result

    # Find the most recent game before game_date
    past = [d for d in dates if d < game_date]
    if not past:
        return result

    last_game = past[-1]
    rest_days = (game_date - last_game).days
    result["rest_days"] = rest_days
    result["back_to_back"] = rest_days == 1

    # 3-in-4: including today's game, 3 games within any 4-night window
    window_start = game_date - timedelta(days=3)
    games_in_window = sum(1 for d in dates if window_start <= d < game_date)
    # games_in_window counts past games in the window; +1 for today's game
    result["three_in_four"] = (games_in_window + 1) >= 3

    return result


# ── Predict a Single Game ────────────────────────────────────────────────

def predict_game(
    home_abbrev: str,
    away_abbrev: str,
    ratings: dict[str, dict],
    schedule_cache: dict[str, list[date]],
    game_date: date,
) -> dict | None:
    """Predict one game. Returns a prediction dict or None if data missing."""
    home_r = ratings.get(home_abbrev)
    away_r = ratings.get(away_abbrev)
    if not home_r or not away_r:
        log.warning("Missing ratings for %s or %s", home_abbrev, away_abbrev)
        return None

    # Base composite diff (home perspective): use venue-specific split
    home_venue = (
        home_r["composite"]
        + WEIGHTS["home_road_split"] * home_r["home_pct"]
    )
    away_venue = (
        away_r["composite"]
        + WEIGHTS["home_road_split"] * away_r["road_pct"]
    )
    diff = home_venue - away_venue

    # Situational adjustments
    adjustments: list[str] = []

    diff += HOME_ICE_BONUS
    adjustments.append("home ice")

    home_rest = detect_rest_situation(home_abbrev, game_date, schedule_cache)
    away_rest = detect_rest_situation(away_abbrev, game_date, schedule_cache)

    if home_rest["back_to_back"]:
        diff += BACK_TO_BACK_PENALTY
        adjustments.append(f"{home_abbrev} B2B")
    if away_rest["back_to_back"]:
        diff -= BACK_TO_BACK_PENALTY  # penalty flips to benefit home
        adjustments.append(f"{away_abbrev} B2B")

    if home_rest["three_in_four"]:
        diff += THREE_IN_FOUR_PENALTY
        adjustments.append(f"{home_abbrev} 3-in-4")
    if away_rest["three_in_four"]:
        diff -= THREE_IN_FOUR_PENALTY
        adjustments.append(f"{away_abbrev} 3-in-4")

    if home_rest["rest_days"] >= 3:
        diff += EXTENDED_REST_BONUS
        adjustments.append(f"{home_abbrev} rested")
    if away_rest["rest_days"] >= 3:
        diff -= EXTENDED_REST_BONUS
        adjustments.append(f"{away_abbrev} rested")

    # Determine pick
    abs_diff = abs(diff)
    if abs_diff < SKIP_THRESHOLD:
        pick = "SKIP"
        stars = 0
    else:
        pick = home_abbrev if diff > 0 else away_abbrev
        stars = 1
        for threshold, s in STAR_THRESHOLDS:
            if abs_diff >= threshold:
                stars = s
                break

    # Early-season cap
    min_gp = min(home_r["gp"], away_r["gp"])
    if min_gp < EARLY_SEASON_GP and stars > 2:
        stars = 2
        adjustments.append("early-season cap")

    # Build reasoning string
    key_factors = ", ".join(adjustments[:4]) if adjustments else "even matchup"

    return {
        "game": f"{away_abbrev} @ {home_abbrev}",
        "home": home_abbrev,
        "away": away_abbrev,
        "pick": pick,
        "stars": stars,
        "diff": round(diff, 4),
        "key_factors": key_factors,
    }


# ── Predict All Today's Games ────────────────────────────────────────────

def predict_today(
    standings: list[dict],
    team_stats: dict[str, dict],
    games: list[dict],
    game_date: date | None = None,
) -> list[dict]:
    """Run the full model on today's slate. Returns a list of prediction dicts."""
    if game_date is None:
        game_date = date.today()

    ratings = compute_team_ratings(standings, team_stats)
    if not ratings:
        log.error("Could not compute team ratings — aborting predictions")
        return []

    schedule_cache: dict[str, list[date]] = {}
    predictions: list[dict] = []

    for game in games:
        home = game.get("homeTeam", {}).get("abbrev", "")
        away = game.get("awayTeam", {}).get("abbrev", "")
        if not home or not away:
            continue
        pred = predict_game(home, away, ratings, schedule_cache, game_date)
        if pred:
            predictions.append(pred)

    predictions.sort(key=lambda p: -p["stars"])
    return predictions
