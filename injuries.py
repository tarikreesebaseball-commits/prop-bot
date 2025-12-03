# injuries.py
import pandas as pd

def redistribute_minutes(proj_df, box_df, injury_feed, team_code):
    """
    Adjust minutes based on injury statuses.
    Expects proj_df to have columns:
      ['player_id','name','team','pos','proj_min']
    """

    proj = proj_df.set_index("player_id").copy()

    # 1. Apply injury effects (no rescale yet)
    for inj in injury_feed:
        pid = inj["player_id"]
        prob = inj.get("probability", 1.0)
        status = inj.get("status", "")

        if pid not in proj.index:
            continue

        # OUT → zero minutes
        if status == "OUT":
            proj.at[pid, "proj_min"] = 0
            continue

        # QUESTIONABLE → scale minutes by probability
        if status == "QUESTIONABLE":
            proj.at[pid, "proj_min"] = proj.at[pid, "proj_min"] * prob

    # 2. Just return adjusted minutes, no 240-minute scaling
    proj = proj.reset_index()
    return proj
