# odds_tracker.py
import sqlite3
import pandas as pd
from datetime import datetime
import os

DB_PATH = os.path.join("data", "odds_snapshots.db")

def ensure_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS odds_snapshots (
        id INTEGER PRIMARY KEY,
        game_id TEXT,
        bookmaker TEXT,
        market_type TEXT,
        line_value REAL,
        odds_american INTEGER,
        timestamp TEXT
    )
    """)
    conn.commit()
    conn.close()

def insert_snapshot(game_id, bookmaker, market_type, line_value, odds_american, ts=None):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ts = ts or datetime.utcnow().isoformat()
    c.execute("INSERT INTO odds_snapshots (game_id,bookmaker,market_type,line_value,odds_american,timestamp) VALUES (?,?,?,?,?,?)",
              (game_id, bookmaker, market_type, line_value, odds_american, ts))
    conn.commit()
    conn.close()

def load_snapshots(game_id, market_type="total"):
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM odds_snapshots WHERE game_id=? AND market_type=? ORDER BY timestamp",
                           conn, params=[game_id, market_type])
    conn.close()
    return df

def compute_line_drift(df_snap):
    if df_snap.empty:
        return None
    first = df_snap.iloc[0]["line_value"]
    last = df_snap.iloc[-1]["line_value"]
    drift = last - first
    pct = drift / first if first != 0 else 0.0
    return {"open": float(first), "current": float(last), "drift": float(drift), "pct_change": float(pct)}
