"""SportsAlgo NHL Daily Picks — Orchestrator.

Run daily via GitHub Actions or manually:
    python main.py
"""

import logging
import sys
from datetime import date, timedelta

from nhl_api import fetch_standings, fetch_team_stats, fetch_todays_games, fetch_scores
from model import predict_today
from sheets import get_client, write_daily_picks, write_standings, append_to_tracker, update_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sportsalgo")


def main() -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    log.info("SportsAlgo NHL — run date: %s", today)

    # ── Step 1: Resolve yesterday's picks ──
    log.info("Fetching yesterday's scores (%s)…", yesterday)
    yesterday_scores = fetch_scores(yesterday)
    client = get_client()

    if client and yesterday_scores:
        update_results(client, yesterday.isoformat(), yesterday_scores)
    elif not yesterday_scores:
        log.info("No scores found for %s (off day or API issue)", yesterday)

    # ── Step 2: Fetch today's data ──
    log.info("Fetching standings…")
    standings = fetch_standings()
    if not standings:
        log.error("Could not fetch standings — aborting")
        sys.exit(1)

    log.info("Fetching team stats…")
    team_stats = fetch_team_stats(standings)
    if not team_stats:
        log.warning("Team stats unavailable — using standings-only mode")

    log.info("Fetching today's schedule…")
    games = fetch_todays_games()
    if not games:
        log.info("No games scheduled for %s — writing standings", today)
        if client:
            write_standings(client, standings, today.isoformat())
        _print_standings(today, standings)
        return

    # ── Step 3 & 4: Compute ratings and predict ──
    log.info("Running predictions for %d games…", len(games))
    predictions = predict_today(standings, team_stats, games, today)

    # ── Step 5: Write to Google Sheet ──
    if client:
        write_daily_picks(client, predictions, today.isoformat())
        append_to_tracker(client, predictions, today.isoformat())
    else:
        log.info("Sheets client unavailable — skipping sheet writes")

    # ── Step 6: Console summary ──
    _print_summary(today, predictions)


def _print_summary(today: date, predictions: list[dict]) -> None:
    """Print a human-readable summary to stdout."""
    print(f"\n{'=' * 60}")
    print(f"  SportsAlgo NHL Picks — {today.isoformat()}")
    print(f"{'=' * 60}\n")

    if not predictions:
        print("  No predictions generated.\n")
        return

    for p in predictions:
        if p["pick"] == "SKIP":
            star_str = "SKIP"
        else:
            star_str = "*" * p["stars"]
        print(f"  {p['game']:<18} → {p['pick']:<5} {star_str:<6} ({p['key_factors']})")

    print(f"\n  Total: {len(predictions)} games")
    picks = [p for p in predictions if p["pick"] != "SKIP"]
    skips = len(predictions) - len(picks)
    if skips:
        print(f"  Skipped: {skips} (too close to call)")
    print()


def _print_standings(today: date, standings: list[dict]) -> None:
    """Print league standings to console on off days."""
    print(f"\n{'=' * 60}")
    print(f"  No games today — NHL Standings as of {today.isoformat()}")
    print(f"{'=' * 60}\n")

    divisions: dict[str, list[dict]] = {}
    for team in standings:
        div = team.get("divisionName", "Unknown")
        conf = team.get("conferenceName", "")
        key = f"{conf} — {div}"
        divisions.setdefault(key, []).append(team)

    for div_name in sorted(divisions):
        teams = sorted(divisions[div_name], key=lambda t: t.get("divisionSequence", 99))
        print(f"  {div_name}")
        print(f"  {'Team':<5} {'GP':>3} {'W':>3} {'L':>3} {'OTL':>3} {'PTS':>4} {'Pt%':>6} {'GD':>5} {'L10':>8} {'Strk':>5}")
        print(f"  {'-' * 50}")
        for t in teams:
            abbrev = t.get("teamAbbrev", {}).get("default", "???")
            gp = t.get("gamesPlayed", 0)
            w = t.get("wins", 0)
            l = t.get("losses", 0)
            otl = t.get("otLosses", 0)
            pts = t.get("points", 0)
            pt_pct = t.get("pointPctg", 0)
            gd = t.get("goalFor", 0) - t.get("goalAgainst", 0)
            gd_str = f"+{gd}" if gd > 0 else str(gd)
            l10 = f"{t.get('l10Wins', 0)}-{t.get('l10Losses', 0)}-{t.get('l10OtLosses', 0)}"
            streak = f"{t.get('streakCode', '')} {t.get('streakCount', '')}".strip()
            print(f"  {abbrev:<5} {gp:>3} {w:>3} {l:>3} {otl:>3} {pts:>4} {pt_pct:>6.3f} {gd_str:>5} {l10:>8} {streak:>5}")
        print()


if __name__ == "__main__":
    main()
