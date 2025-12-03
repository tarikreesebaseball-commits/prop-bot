# data_loader.py
import requests
import pandas as pd
from datetime import datetime, timedelta

BASE_URL = "https://api.balldontlie.io/v1"

# Your balldontlie API key
API_KEY = "10e8e478-b640-451a-856b-c7cd54650464"


def _get_with_auth(path: str, params: dict):
    """
    Helper: call balldontlie with your key.
    Tries plain 'Authorization: KEY' first,
    if that 401s, retries with 'Authorization: Bearer KEY'.
    """
    url = f"{BASE_URL}{path}"

    # Try plain key
    headers_plain = {"Authorization": API_KEY}
    r = requests.get(url, headers=headers_plain, params=params)
    if r.status_code == 401:
        print(f"  balldontlie returned 401 for {url} with plain key, trying Bearer...", flush=True)
        headers_bearer = {"Authorization": f"Bearer {API_KEY}"}
        r = requests.get(url, headers=headers_bearer, params=params)

    if not r.ok:
        print(f"  balldontlie error {r.status_code} on {url}", flush=True)
        try:
            print("  Response:", r.text[:300], flush=True)
        except Exception:
            pass
        return None

    try:
        return r.json()
    except Exception as e:
        print("  Failed to parse JSON from balldontlie:", e, flush=True)
        return None


def fetch_games_for_date(date_str: str) -> list:
    """
    Fetch all NBA games for a given date (YYYY-MM-DD) from balldontlie.
    Returns a list of game dicts.
    """
    print(f"\nFetching games for {date_str}...", flush=True)
    data = _get_with_auth(
        "/games",
        {
            "dates[]": date_str,
            "per_page": 100,
        },
    )
    if not data:
        print("  No response / error from balldontlie for games.", flush=True)
        return []
    return data.get("data", [])


def fetch_stats_for_game(game_id: int) -> list:
    """
    Fetch all player stats for a given game ID from balldontlie.
    Returns a list of stats dicts.
    """
    print(f"  Fetching stats for game {game_id}...", flush=True)
    data = _get_with_auth(
        "/stats",
        {
            "game_ids[]": game_id,
            "per_page": 100,
        },
    )
    if not data:
        print(f"    No response / error from balldontlie for game {game_id}.", flush=True)
        return []
    return data.get("data", [])


def load_recent_boxscores(days: int = 10) -> pd.DataFrame:
    """
    Load last `days` days of NBA boxscores from balldontlie.
    Builds a DataFrame with:
      game_date, player_id, player_name, team, pos, minutes, pts, team_pts
    """
    print(f"\nFetching last {days} days of NBA games from balldontlie...", flush=True)

    all_rows = []

    for offset in range(1, days + 1):
        date_obj = datetime.utcnow().date() - timedelta(days=offset)
        date_str = date_obj.strftime("%Y-%m-%d")

        games = fetch_games_for_date(date_str)
        if not games:
            print("  No games on this date or failed to load.", flush=True)
            continue

        for g in games:
            game_id = g["id"]
            home_team = g["home_team"]["abbreviation"]
            visitor_team = g["visitor_team"]["abbreviation"]
            home_score = g["home_team_score"]
            visitor_score = g["visitor_team_score"]

            print(f"  Game {game_id}: {visitor_team} @ {home_team}", flush=True)

            stats = fetch_stats_for_game(game_id)
            if not stats:
                print(f"    No stats returned for game {game_id}.", flush=True)
                continue

            # map team -> final score
            team_totals = {
                home_team: home_score,
                visitor_team: visitor_score,
            }

            for s in stats:
                player = s.get("player", {})
                team = s.get("team", {})

                player_id = player.get("id")
                player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()

                team_abbr = team.get("abbreviation")
                team_pts = team_totals.get(team_abbr, 0)

                # minutes like "34:21"
                min_str = s.get("min") or "0:00"
                try:
                    m, sec = min_str.split(":")
                    minutes = float(m) + float(sec) / 60.0
                except Exception:
                    minutes = 0.0

                pts = s.get("pts", 0)

                row = {
                    "game_date": pd.to_datetime(date_str),
                    "player_id": player_id,
                    "player_name": player_name,
                    "team": team_abbr,
                    "pos": player.get("position") or "?",
                    "minutes": minutes,
                    "pts": pts,
                    "team_pts": team_pts,
                }
                all_rows.append(row)

    if not all_rows:
        print("No real NBA data found from balldontlie in last few days.", flush=True)
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    print(f"\nLoaded {len(df)} player stat lines from last {days} days of balldontlie.", flush=True)
    return df
