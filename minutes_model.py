# minutes_model.py
import pandas as pd

def calculate_projected_minutes(box_df: pd.DataFrame):
    """
    Simple baseline minutes model:
    - Calculates recent rolling minutes
    - Produces a dataframe with columns:
        ['player_id','name','team','pos','proj_min']
    """

    # Calculate rolling average minutes (last 10 games)
    roll = (
        box_df.sort_values("game_date")
            .groupby("player_id")
            .rolling(window=10, on="game_date")["minutes"]
            .mean()
            .reset_index()
            .rename(columns={"minutes": "roll_min"})
    )

    merged = box_df.merge(roll, on=["player_id", "game_date"], how="left")

    # Use player’s latest game for their projection
    last_games = (
        merged.sort_values("game_date")
              .groupby("player_id")
              .tail(1)
    )

    proj_df = last_games[[
        "player_id",
        "player_name",
        "team",
        "pos",
        "roll_min"
    ]].rename(columns={"player_name": "name", "roll_min": "proj_min"})

    # Fill missing with a soft baseline
    proj_df["proj_min"] = proj_df["proj_min"].fillna(20)

    # Round minutes
    proj_df["proj_min"] = proj_df["proj_min"].round(1)

    return proj_df
