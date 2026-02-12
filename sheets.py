"""Google Sheets integration via gspread — write picks & track accuracy."""

import base64
import json
import logging
import os

import gspread

from config import SHEET_NAME, TAB_DAILY, TAB_TRACKER

log = logging.getLogger(__name__)


# ── Auth ─────────────────────────────────────────────────────────────────

def get_client() -> gspread.Client | None:
    """Return an authenticated gspread client, or None if creds unavailable."""
    # Option 1: base64-encoded JSON in env var (GitHub Actions)
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    if b64:
        creds_json = json.loads(base64.b64decode(b64))
        return gspread.service_account_from_dict(creds_json)

    # Option 2: local file
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if os.path.exists(creds_file):
        return gspread.service_account(filename=creds_file)

    log.warning("No Google credentials found — sheets operations will be skipped")
    return None


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_or_create_worksheet(spreadsheet, title: str, rows: int = 1000, cols: int = 10):
    """Get worksheet by title, creating it if missing."""
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


# ── Daily Picks (overwrite) ─────────────────────────────────────────────

def write_daily_picks(
    client: gspread.Client,
    predictions: list[dict],
    today_str: str,
) -> None:
    """Overwrite the 'Daily Picks' tab with today's predictions."""
    sh = client.open(SHEET_NAME)
    ws = _get_or_create_worksheet(sh, TAB_DAILY)

    header = ["Date", "Game", "Pick", "Stars", "Key Factors"]
    rows = [header]
    for p in predictions:
        star_display = "SKIP" if p["pick"] == "SKIP" else "*" * p["stars"]
        rows.append([
            today_str,
            p["game"],
            p["pick"],
            star_display,
            p["key_factors"],
        ])

    ws.clear()
    ws.update(rows, "A1")
    log.info("Wrote %d picks to '%s'", len(predictions), TAB_DAILY)


# ── Season Tracker (append) ─────────────────────────────────────────────

def append_to_tracker(
    client: gspread.Client,
    predictions: list[dict],
    today_str: str,
) -> None:
    """Append today's picks to the 'Season Tracker' tab (results TBD)."""
    sh = client.open(SHEET_NAME)
    ws = _get_or_create_worksheet(sh, TAB_TRACKER)

    # Ensure header exists
    existing = ws.get_all_values()
    if not existing or existing[0][0] != "Date":
        header = ["Date", "Game", "Pick", "Stars", "Result", "Correct?"]
        ws.update([header], "A1")
        # Leave row 2 for summary — data starts row 3
        ws.update([["", "", "", "", "", ""]], "A2")

    new_rows = []
    for p in predictions:
        if p["pick"] == "SKIP":
            continue
        new_rows.append([
            today_str,
            p["game"],
            p["pick"],
            p["stars"],
            "",  # Result — filled in next day
            "",  # Correct? — filled in next day
        ])

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        log.info("Appended %d rows to '%s'", len(new_rows), TAB_TRACKER)


# ── Update Yesterday's Results ───────────────────────────────────────────

def update_results(
    client: gspread.Client,
    yesterday_str: str,
    scores: list[dict],
) -> None:
    """Fill in Result and Correct? columns for yesterday's picks."""
    sh = client.open(SHEET_NAME)
    ws = _get_or_create_worksheet(sh, TAB_TRACKER)
    all_rows = ws.get_all_values()

    # Build a lookup: "AWAY @ HOME" → "WINNER score-score"
    results_map: dict[str, tuple[str, str]] = {}
    for game in scores:
        home = game.get("homeTeam", {})
        away = game.get("awayTeam", {})
        home_abbrev = home.get("abbrev", "")
        away_abbrev = away.get("abbrev", "")
        home_score = home.get("score", 0)
        away_score = away.get("score", 0)
        game_key = f"{away_abbrev} @ {home_abbrev}"
        winner = home_abbrev if home_score > away_score else away_abbrev
        result_str = f"{winner} {max(home_score, away_score)}-{min(home_score, away_score)}"
        results_map[game_key] = (result_str, winner)

    updates = 0
    for i, row in enumerate(all_rows):
        if len(row) < 4:
            continue
        row_date, game_str, pick = row[0], row[1], row[2]
        if row_date != yesterday_str:
            continue
        if game_str in results_map:
            result_str, winner = results_map[game_str]
            correct = "Y" if pick == winner else "N"
            cell_row = i + 1  # 1-indexed
            ws.update(f"E{cell_row}", [[result_str]])
            ws.update(f"F{cell_row}", [[correct]])
            updates += 1

    log.info("Updated %d results for %s", updates, yesterday_str)

    if updates:
        update_summary_row(ws, all_rows)


# ── Summary Row ──────────────────────────────────────────────────────────

def update_summary_row(ws, all_rows: list[list[str]] | None = None) -> None:
    """Recalculate the W-L summary in row 2 of the tracker."""
    if all_rows is None:
        all_rows = ws.get_all_values()

    total_w = total_l = 0
    star_stats: dict[int, dict[str, int]] = {}

    for row in all_rows[2:]:  # skip header + summary
        if len(row) < 6 or row[5] not in ("Y", "N"):
            continue
        stars = int(row[3]) if row[3].isdigit() else 0
        correct = row[5] == "Y"
        if correct:
            total_w += 1
        else:
            total_l += 1
        if stars not in star_stats:
            star_stats[stars] = {"W": 0, "L": 0}
        star_stats[stars]["Y" if correct else "N"] = (
            star_stats[stars].get("Y" if correct else "N", 0) + 1
        )
        if correct:
            star_stats[stars]["W"] += 1
        else:
            star_stats[stars]["L"] += 1

    total = total_w + total_l
    pct = f"{total_w / total * 100:.1f}%" if total else "N/A"

    parts = [f"Overall: {total_w}-{total_l} ({pct})"]
    for s in sorted(star_stats):
        sw = star_stats[s]["W"]
        sl = star_stats[s]["L"]
        st = sw + sl
        sp = f"{sw / st * 100:.0f}%" if st else "N/A"
        parts.append(f"{s}*: {sw}-{sl} ({sp})")

    summary = " | ".join(parts)
    ws.update("A2", [[summary, "", "", "", "", ""]])
    log.info("Summary: %s", summary)
