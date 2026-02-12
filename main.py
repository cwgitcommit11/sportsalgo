"""SportsAlgo NHL Daily Picks — Orchestrator.

Run daily via GitHub Actions or manually:
    python main.py
"""

import logging
import sys
from datetime import date, timedelta

from nhl_api import fetch_standings, fetch_team_stats, fetch_todays_games, fetch_scores
from model import predict_today
from sheets import get_client, write_daily_picks, append_to_tracker, update_results

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
        log.info("No games scheduled for %s", today)
        _print_no_games(today)
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


def _print_no_games(today: date) -> None:
    print(f"\n  SportsAlgo NHL — {today.isoformat()}: No games today.\n")


if __name__ == "__main__":
    main()
