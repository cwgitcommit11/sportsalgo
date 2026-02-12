# SportsAlgo NHL Daily Picks — Configuration

# ── NHL API Base URLs ──
NHL_API_BASE = "https://api-web.nhle.com"
NHL_STATS_BASE = "https://api.nhle.com/stats/rest/en/team"

STANDINGS_URL = f"{NHL_API_BASE}/v1/standings/now"
SCHEDULE_URL = f"{NHL_API_BASE}/v1/schedule/now"
SCORE_URL = f"{NHL_API_BASE}/v1/score"  # append /{date}
TEAM_SCHEDULE_URL = f"{NHL_API_BASE}/v1/club-schedule-season"  # append /{abbrev}/now
TEAM_STATS_URL = (
    f"{NHL_STATS_BASE}/summary?"
    "isAggregate=false&isGame=false"
    "&sort=%5B%7B%22property%22:%22points%22,%22direction%22:%22DESC%22%7D%5D"
    "&cayenneExp=gameTypeId=2%20and%20seasonId%3C=20252026%20and%20seasonId%3E=20252026"
)

# ── Factor Weights (must sum to 1.0) ──
WEIGHTS = {
    "goal_diff_per_gp": 0.25,
    "point_pct": 0.20,
    "recent_form": 0.20,
    "home_road_split": 0.10,
    "special_teams": 0.10,
    "shot_diff_per_gp": 0.10,
    "streak_momentum": 0.05,
}

# ── Situational Adjustments (added to composite differential) ──
HOME_ICE_BONUS = 0.035
BACK_TO_BACK_PENALTY = -0.030
THREE_IN_FOUR_PENALTY = -0.045
EXTENDED_REST_BONUS = 0.010  # 3+ days off

# ── Star Rating Thresholds (on |adjusted_diff|) ──
STAR_THRESHOLDS = [
    (0.25, 5),
    (0.17, 4),
    (0.10, 3),
    (0.05, 2),
    (0.00, 1),
]

SKIP_THRESHOLD = 0.005  # |diff| below this → "SKIP"

# ── Early-season guard ──
EARLY_SEASON_GP = 10  # teams with fewer GP get star cap = 2

# ── Google Sheet ──
SHEET_NAME = "SportsAlgo NHL Picks"
TAB_DAILY = "Daily Picks"
TAB_TRACKER = "Season Tracker"
