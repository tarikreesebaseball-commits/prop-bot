# espn_loader.py
import requests
import pandas as pd
from datetime import datetime, timedelta

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
SUMMARY_URL = "https://site.web.api.espn.com/apis/v2/sports/basketball/nba/summary"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
}


def fetch_scoreboard(date_str: str) -> dict:
    """
    Fetch ESPN NBA scoreboard JSON for a specific date YYYYMMDD.
    """
    params = {"dates": date_str}
    r = requests.get(SCOREBOARD_URL, params=params, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def fetch_espn_boxscore(event_id: str) -> pd.DataFrame:
    """
    Fetch boxscore for a single ESPN NBA game by event ID.
    Returns DataFrame with:
      game_id, game_date, player_id, player_name, team, pos, minutes, pts, team_pts
    """
    params = {"event": event_id}
    r = requests.get(SUMMARY_URL, params=params, headers=HEADERS)
    r.raise_for_status()
    data = r.json()

    competitions = data.get("header", {}).get("competitions", [])
    if not competitions:
        return pd.DataFrame()

    comp = competitions[0]
    game_date_str = comp.get("date", "")[:10]
    try:
        game_date = pd.to_datetime(game_date_str)
    except Exception:
        game_date = pd.to_datetime("today")

    # scores
    team_scores = {}
    for competitor in comp.get("competitors", []):
        team_abbr = competitor.get("team", {}).get("abbreviation")
        try:
            score = int(competitor.get("score", 0))
        except Exception:
            score = 0
        if team_abbr:
            team_scores[team_abbr] = score

    # players
    boxscore = data.get("boxscore", {})
    players_sections = boxscore.get("players", [])

    rows = []

    for team_section in players_sections:
        team_abbr = team_section.get("team", {}).get("abbreviation", "")
        team_pts = team_scores.get(team_abbr, 0)

        for stat_cat in team_section.get("statistics", []):
            for p in stat_cat.get("athletes", []):
                ath = p.get("athlete", {})
                stats = p.get("stats", [])

                minutes = 0.0
                pts = 0

                # ESPN stats often: [MIN,FGM-A,3PM-A,FTM-A,OREB,DREB,REB,AST,STL,BLK,TO,PF,PTS,+/-]
                if stats and len(stats) >= 13:
                    min_str = stats[0]
                    pts_str = stats[12]

                    # minutes "34" or "34:21" or weird strings
                    if isinstance(min_str, str) and ":" in min_str:
                        try:
                            m, s = min_str.split(":")
                            minutes = float(m) + float(s) / 60.0
                        except Exception:
                            minutes = 0.0
                    else:
                        try:
                            minutes = float(min_str)
                        except Exception:
                            minutes = 0.0

                    try:
                        pts = int(pts_str)
                    except Exception:
                        pts = 0

                rows.append({
                    "game_id": event_id,
                    "game_date": game_date,
                    "player_id": ath.get("id"),
                    "player_name": ath.get("displayName"),
                    "team": team_abbr,
                    "pos": (ath.get("position") or {}).get("abbreviation", "?"),
                    "minutes": minutes,
                    "pts": pts,
                    "team_pts": team_pts,
                })

    return pd.DataFrame(rows)


def load_recent_espn_boxscores(days: int = 10) -> pd.DataFrame:
    """
    Loads last `days` days of NBA games from ESPN.
    No manual IDs or date changes needed.
    """
    print(f"\nFetching last {days} days of NBA games from ESPN...")

    all_frames = []

    # Look back 1..days ago (yesterday, 2 days ago, etc.)
    for offset in range(1, days + 1):
        date_str = (datetime.utcnow() - timedelta(days=offset)).strftime("%Y%m%d")
        print(f"\nFetching scoreboard for {date_str}...")

        try:
            sb = fetch_scoreboard(date_str)
        except Exception as e:
            print(f"  Failed to fetch scoreboard {date_str}: {e}")
            continue

        events = sb.get("events", [])
        if not events:
            print("  No games on this date.")
            continue

        for ev in events:
            event_id = ev.get("id")
            if not event_id:
                continue

            print(f"    Fetching boxscore for game {event_id}...")
            try:
                df_game = fetch_espn_boxscore(event_id)
                if not df_game.empty:
                    all_frames.append(df_game)
            except Exception as e:
                print(f"      Skipping game {event_id} due to error: {e}")

    if not all_frames:
        print("No ESPN data available from last few days.")
        return pd.DataFrame(
            columns=[
                "game_id", "game_date", "player_id", "player_name",
                "team", "pos", "minutes", "pts", "team_pts"
            ]
        )

    df = pd.concat(all_frames, ignore_index=True)
    print(f"\nLoaded {len(df)} player stat lines from last {days} days.")
    return df
