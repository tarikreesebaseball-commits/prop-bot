# team_ratings.py
import pandas as pd

def estimate_team_ratings(team_game_df: pd.DataFrame):
    """
    Estimate very simple offensive/defensive ratings.
    """
    df = team_game_df.copy()
    df["poss"] = df["fga"] + 0.4 * df["fta"] - df["oreb"] + df["tov"]

    team_stats = (
        df.groupby("team")
          .agg(
              OffPts=("team_pts", "mean"),
              Poss=("poss", "mean")
          )
          .reset_index()
    )

    team_stats["OffRtg"] = team_stats["OffPts"] / (team_stats["Poss"] / 100.0)
    league_avg = team_stats["OffRtg"].mean()

    team_stats["DefRtg"] = 100 + (league_avg - team_stats["OffRtg"])

    return team_stats[["team", "OffRtg", "DefRtg", "Poss"]]


def apply_position_matchup(proj_df: pd.DataFrame,
                           box_df: pd.DataFrame,
                           matchup_profile: dict,
                           opponent_team: str):
    """
    Apply positional defensive matchup adjustments.

    proj_df must contain:
        ['player_id','name','team','pos','proj_min']
    """

    rows = []

    # League-wide pts per min
    valid = box_df[box_df["minutes"] > 0]
    if len(valid) == 0:
        pts_per_min = 0.5
    else:
        pts_per_min = valid["pts"].sum() / valid["minutes"].sum()

    for _, r in proj_df.iterrows():
        pos = r["pos"]

        base_pts = r["proj_min"] * pts_per_min

        # Apply defensive positional modifier
        adj_pct = matchup_profile.get(opponent_team, {}).get(pos, 0.0)
        adj_pts = base_pts * (1 + adj_pct)

        rows.append({
            "player_id": r["player_id"],
            "name": r["name"],
            "team": r["team"],
            "pos": pos,
            "proj_min": r["proj_min"],
            "proj_pts_base": base_pts,
            "proj_pts_adj": adj_pts,
        })

    return pd.DataFrame(rows)
