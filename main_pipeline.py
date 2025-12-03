# main_pipeline.py

import os
from datetime import datetime
import numpy as np
import pandas as pd

from minutes_model import calculate_projected_minutes
from injuries import redistribute_minutes
from odds_tracker import insert_snapshot, load_snapshots, compute_line_drift, ensure_db
from team_ratings import apply_position_matchup, estimate_team_ratings
from data_loader import load_recent_boxscores


# --------- Fallback synthetic data (used if API fails) ----------

SAMPLE_PLAYERS = [
    {"player_id": 11, "name": "Leadoff Star", "team": "LAL", "pos": "G"},
    {"player_id": 12, "name": "Starter Fwd", "team": "LAL", "pos": "F"},
    {"player_id": 13, "name": "Backup Wing", "team": "LAL", "pos": "G"},
    {"player_id": 21, "name": "BOS Star", "team": "BOS", "pos": "G"},
    {"player_id": 22, "name": "BOS Role", "team": "BOS", "pos": "F"},
    {"player_id": 23, "name": "BOS Bench", "team": "BOS", "pos": "C"},
]


def build_synthetic_box():
    """Fallback demo data if real API returns nothing."""
    dates = pd.date_range(end=datetime.utcnow().date(), periods=20)
    rows = []
    for d in dates:
        for p in SAMPLE_PLAYERS:
            played = np.random.rand() > 0.05
            if played:
                base_min = 30 if p["player_id"] in (11, 21) else 14
                minutes = float(round(np.random.normal(base_min, 3), 1))
                minutes = max(0.0, minutes)
            else:
                minutes = 0.0

            pts = round(max(0, np.random.normal(minutes * 0.45, 5)), 1)

            rows.append(
                {
                    "game_date": pd.Timestamp(d),
                    "player_id": p["player_id"],
                    "player_name": p["name"],
                    "team": p["team"],
                    "pos": p["pos"],
                    "minutes": minutes,
                    "pts": pts,
                    "team_pts": int(np.random.normal(110, 8)),
                }
            )
    return pd.DataFrame(rows)


# --------- CORE MODEL FUNCTION (used by bot and CLI) ----------

def run_full_model(days_back: int = 10):
    """
    Runs the full pipeline and returns a dict for Discord / CLI.

    Returns example:
    {
        "used_real_data": bool,
        "rows_loaded": int,
        "proj_game_total": float,
        "book_total": float,
        "p_over": float,
        "ev": float,
        "team_ratings": DataFrame,
        "top_players": DataFrame,
    }
    """

    # --- 0. LOAD DATA ---
    print("Loading NBA data from balldontlie (last", days_back, "days)...")
    box_df = load_recent_boxscores(days=days_back)
    used_real_data = False

    if box_df is not None and not box_df.empty:
        used_real_data = True
    else:
        print("balldontlie returned no data. Falling back to synthetic demo data.")
        box_df = build_synthetic_box()

    # --- 1. MINUTES PROJECTION ---
    print("\n--- STEP 1: Minutes projection ---")
    proj_df = calculate_projected_minutes(box_df)
    print("\nBase projected minutes (first 15 rows):")
    print(proj_df.head(15).to_string(index=False))

    # --- 2. INJURY REDISTRIBUTION (demo: LAL) ---
    print("\n--- STEP 2: Injury redistribution demo (using LAL) ---")
    injury_feed = [
        {"player_id": 12, "status": "OUT", "probability": 0.0},
        {"player_id": 11, "status": "QUESTIONABLE", "probability": 0.7},
    ]

    modified_proj = redistribute_minutes(
        proj_df, box_df, injury_feed, team_code="LAL"
    )
    print("\nAfter injury redistribution (first 15 rows):")
    print(modified_proj.head(15).to_string(index=False))

    # --- 3. ODDS TRACKING ---
    print("\n--- STEP 3: Odds tracking demo ---")
    ensure_db()
    game_id = "MODEL_DEMO_001"

    # demo snapshots (you can later hook these to a real book)
    insert_snapshot(game_id, "BookA", "total", 229.5, -110)
    insert_snapshot(game_id, "BookA", "total", 228.0, -110)

    snaps = load_snapshots(game_id, "total")
    drift = compute_line_drift(snaps)

    print("\nOdds snapshots (most recent):")
    print(snaps.tail().to_string(index=False))
    print("Drift info:", drift)

    # --- 4. TEAM RATINGS ---
    print("\n--- STEP 4: Team ratings from game logs ---")
    team_game_rows = []
    for (_, team), g in box_df.groupby(["game_date", "team"]):
        team_game_rows.append(
            {
                "team": team,
                "game_date": g["game_date"].iloc[0],
                "team_pts": int(g["team_pts"].mean()),
                "fga": 85,
                "fta": 20,
                "oreb": 10,
                "tov": 12,
            }
        )

    team_game_df = pd.DataFrame(team_game_rows)
    team_ratings = estimate_team_ratings(team_game_df)
    print("\nTeam ratings (estimated):")
    print(team_ratings.to_string(index=False))

    # --- 5. MATCHUP ADJUSTMENTS ---
    print("\n--- STEP 5: Matchup adjustments demo (vs generic opponent) ---")

    # simple profile: no adjustments → all 0
    matchup_profile = {}  # you can add real per-team/pos modifiers later

    proj_pts_df = apply_position_matchup(
        modified_proj,
        box_df,
        matchup_profile,
        opponent_team="GENERIC",
    )

    print("\nPlayer projected points with matchup adjustments (first 20 rows):")
    print(proj_pts_df.head(20).to_string(index=False))

    # --- 6. GAME TOTAL + EV ---
    print("\n--- STEP 6: Game total & EV calc ---")

    # Approx: pick two highest projected scoring teams as a "matchup"
    team_totals = (
        proj_pts_df.groupby("team")["proj_pts_adj"].sum().reset_index()
        if "proj_pts_adj" in proj_pts_df.columns
        else proj_pts_df.groupby("team")["proj_pts"].sum().reset_index()
    )

    team_totals = team_totals.sort_values("proj_pts_adj" if "proj_pts_adj" in team_totals.columns else "proj_pts",
                                          ascending=False)

    if len(team_totals) >= 2:
        col = "proj_pts_adj" if "proj_pts_adj" in team_totals.columns else "proj_pts"
        proj_game_total = float(team_totals.iloc[0][col] + team_totals.iloc[1][col])
    else:
        col = "proj_pts_adj" if "proj_pts_adj" in team_totals.columns else "proj_pts"
        proj_game_total = float(team_totals[col].sum())

    proj_game_total = round(proj_game_total, 2)

    # book total: from drift if available, else assume same as model
    if drift and drift.get("current") is not None:
        book_total = float(drift["current"])
    else:
        book_total = proj_game_total

    # variance from team_pts distribution
    team_pts_per_team_game = (
        box_df.groupby(["game_date", "team"])["team_pts"].mean().reset_index()
    )
    team_std = float(team_pts_per_team_game["team_pts"].std() or 9.0)
    game_std = (2.0 ** 0.5) * team_std

    from scipy.stats import norm

    if game_std <= 0:
        game_std = 9.0

    z = (book_total - proj_game_total) / game_std
    p_over = 1.0 - float(norm.cdf(z))

    def dec_from_american(a):
        a = int(a)
        return 1 + (100 / abs(a)) if a < 0 else 1 + (a / 100.0)

    book_odds_amer = -110
    dec = dec_from_american(book_odds_amer)
    ev = p_over * (dec - 1) - (1 - p_over)

    print(
        f"\nModel projected game total: {proj_game_total:.2f}   "
        f"Book current total: {book_total:.1f}"
    )
    print(f"Model P(over {book_total:.1f}): {p_over:.3f}    EV per $1 at -110: {ev:.4f}")
    print("\nDEBUG: main model run finished.")

    # Build a summary of top players by projected points
    if "proj_pts_adj" in proj_pts_df.columns:
        sort_col = "proj_pts_adj"
    else:
        sort_col = "proj_pts"

    top_players = (
        proj_pts_df.sort_values(sort_col, ascending=False)
        .loc[:, ["player_id", "name", "team", "pos", "proj_min", sort_col]]
        .head(5)
        .copy()
    )
    top_players.rename(columns={sort_col: "proj_pts"}, inplace=True)

    return {
        "used_real_data": used_real_data,
        "rows_loaded": int(len(box_df)),
        "proj_game_total": float(proj_game_total),
        "book_total": float(book_total),
        "p_over": float(p_over),
        "ev": float(ev),
        "team_ratings": team_ratings,
        "top_players": top_players,
    }


# --------- CLI ENTRY POINT (when you run: py main_pipeline.py) ----------

def main():
    print("DEBUG: main() starting...")
    result = run_full_model(days_back=10)

    if not result:
        print("ERROR: Model failed to return result dict.")
        return

    print("\n=== SUMMARY ===")
    src = "balldontlie (last 10 days)" if result["used_real_data"] else "synthetic demo data"
    print(f"Data source: {src}")
    print(f"Rows loaded: {result['rows_loaded']}")
    print(
        f"Model total: {result['proj_game_total']:.2f}, "
        f"Book total: {result['book_total']:.2f}, "
        f"P(Over): {result['p_over']*100:.1f}%, "
        f"EV: {result['ev']*100:.2f}%"
    )
    print("\nTop players by projected points:")
    print(result["top_players"].to_string(index=False))
    print("DEBUG: main() finished.")


if __name__ == "__main__":
    print("DEBUG: __main__ block hit, calling main()...")
    main()
