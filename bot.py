# bot.py
import os
import math
import requests
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
from bs4 import BeautifulSoup

# Load local .env for development
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")   # set in .env / Render
ODDS_API_KEY   = os.getenv("ODDS_API_KEY")   # optional, unused for now

PREFIX = "!"
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ============================================================
# Helper math functions
# ============================================================

def poisson_p(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def poisson_cdf(k, lam):
    s = 0.0
    for i in range(0, k + 1):
        s += poisson_p(i, lam)
    return s

def probability_over(prop_line, expected_avg):
    """
    Over means X >= ceil(prop_line).
    """
    threshold = math.ceil(prop_line)
    cdf = poisson_cdf(threshold - 1, expected_avg)
    return 1.0 - cdf

def american_to_decimal(odds):
    if odds is None:
        return None
    try:
        odds = int(odds)
    except Exception:
        return None
    if odds < 0:
        return 1 + 100 / abs(odds)
    else:
        return 1 + odds / 100.0

def expected_value_percent(prob, book_decimal):
    if book_decimal is None:
        return None
    exp_return = prob * book_decimal - 1
    return exp_return * 100.0

# ============================================================
# Data fetchers: BallDontLie (for !prop)
# ============================================================

def get_player_season_avg(player_name, season=None):
    """
    BallDontLie season averages (simple, used by !prop).
    Returns dict like {'pts':..., 'reb':..., 'ast':...} or None.
    """
    search_url = f"https://www.balldontlie.io/api/v1/players?search={requests.utils.quote(player_name)}"
    r = requests.get(search_url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("data"):
        return None

    player = data["data"][0]
    player_id = player["id"]

    if season is None:
        # Simple: use recent season. You can change this year if needed.
        season = 2023

    sa_url = (
        f"https://www.balldontlie.io/api/v1/season_averages?"
        f"season={season}&player_ids[]={player_id}"
    )
    r2 = requests.get(sa_url, timeout=10)
    if r2.status_code != 200:
        return None
    sa = r2.json()
    if not sa.get("data"):
        return None
    return sa["data"][0]

# ============================================================
# Data fetchers: TeamRankings scrape (for !espnprop)
# ============================================================

TR_BASE = "https://www.teamrankings.com"

TR_STAT_PATHS = {
    "pts": "/nba/player-stat/points",
    "points": "/nba/player-stat/points",
    "reb": "/nba/player-stat/rebounds",
    "rebs": "/nba/player-stat/rebounds",
    "ast": "/nba/player-stat/assists",
    "assists": "/nba/player-stat/assists",
}

TR_STAT_HEADERS = {
    "pts": ["points", "pts", "points per game"],
    "points": ["points", "pts", "points per game"],
    "reb": ["rebounds", "reb", "rebounds per game"],
    "rebs": ["rebounds", "reb", "rebounds per game"],
    "ast": ["assists", "ast", "assists per game"],
    "assists": ["assists", "ast", "assists per game"],
}

def get_teamrankings_stat(player_name, stat):
    """
    Scrape TeamRankings NBA player tables to get per-game stat.
    Returns float or None.
    """
    key = stat.lower()
    path = TR_STAT_PATHS.get(key)
    if not path:
        return None

    url = TR_BASE + path
    try:
        r = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PropBot/1.0)"}
        )
    except Exception:
        return None

    if r.status_code != 200:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    # Try to locate the header row and find the proper stat column
    thead = table.find("thead")
    if not thead:
        return None
    header_cells = [th.get_text(strip=True) for th in thead.find_all("th")]

    # Find player column (usually first, but be safe)
    player_col = 0
    for idx, h in enumerate(header_cells):
        if "player" in h.lower():
            player_col = idx
            break

    # Find stat column based on header text
    stat_targets = TR_STAT_HEADERS.get(key, [])
    stat_col = None
    for idx, h in enumerate(header_cells):
        hl = h.lower()
        for t in stat_targets:
            if t in hl:
                stat_col = idx
                break
        if stat_col is not None:
            break

    if stat_col is None:
        # fallback: last column
        stat_col = len(header_cells) - 1

    # Now loop rows and find our player
    tbody = table.find("tbody")
    if not tbody:
        return None

    pname_lower = player_name.lower()
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) <= max(player_col, stat_col):
            continue
        name_text = cells[player_col].get_text(strip=True)
        if not name_text:
            continue

        # Rough match: contains name either way
        if pname_lower in name_text.lower() or name_text.lower() in pname_lower:
            stat_text = cells[stat_col].get_text(strip=True)
            try:
                return float(stat_text)
            except Exception:
                return None

    return None

def get_expected_from_teamrankings_or_bdl(player_name, stat, fallback_prop_line=None):
    """
    Priority:
      1) TeamRankings per-game
      2) BallDontLie season_averages
      3) fallback_prop_line (if provided)
    """
    # 1) TeamRankings
    tr_val = get_teamrankings_stat(player_name, stat)
    if tr_val is not None:
        return tr_val, "TeamRankings"

    # 2) BallDontLie
    sa = get_player_season_avg(player_name)
    if sa:
        stat_map = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebs": "reb",
            "ast": "ast", "assists": "ast",
        }
        key = stat_map.get(stat.lower())
        if key and key in sa:
            return sa[key], "BallDontLie season averages"

    # 3) Fallback
    if fallback_prop_line is not None:
        return fallback_prop_line, "Fallback = prop line"

    return None, "N/A"

# ============================================================
# Generic embed builder used by both commands
# ============================================================

def build_prop_embed(
    player,
    stat,
    prop_line,
    expected_avg,
    over_prob,
    under_prob,
    book_over,
    book_under,
    source_label: str,
    title_prefix: str = "🎯",
):
    book_dec_over = american_to_decimal(book_over) if book_over else None
    book_dec_under = american_to_decimal(book_under) if book_under else None

    fair_over = (1.0 / over_prob) if over_prob > 0 else float("inf")
    fair_under = (1.0 / under_prob) if under_prob > 0 else float("inf")
    ev_over = expected_value_percent(over_prob, book_dec_over) if book_dec_over else None
    ev_under = expected_value_percent(under_prob, book_dec_under) if book_dec_under else None

    embed = discord.Embed(
        title=f"{title_prefix} {player} - {stat.upper()}",
        color=0x0BB25F
    )
    embed.add_field(name="Prop Line", value=str(prop_line), inline=True)
    embed.add_field(name="Expected Avg", value=f"{expected_avg:.2f}", inline=True)
    embed.add_field(name="Source", value=source_label, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # OVER
    if book_dec_over:
        over_text = (
            f"Probability: {over_prob * 100:.1f}%\n"
            f"Fair Odds (dec): {fair_over:.2f}\n"
            f"Book Odds (amer): {book_over}\n"
            f"Book Odds (dec): {book_dec_over:.2f}\n"
            f"EV: {ev_over:.2f}%"
            if ev_over is not None
            else "No EV (book odds invalid)."
        )
    else:
        over_text = f"Probability: {over_prob * 100:.1f}%\nNo book odds provided."

    embed.add_field(name="🔼 OVER", value=over_text, inline=False)

    # UNDER
    if book_dec_under:
        under_text = (
            f"Probability: {under_prob * 100:.1f}%\n"
            f"Fair Odds (dec): {fair_under:.2f}\n"
            f"Book Odds (amer): {book_under}\n"
            f"Book Odds (dec): {book_dec_under:.2f}\n"
            f"EV: {ev_under:.2f}%"
            if ev_under is not None
            else "No EV (book odds invalid)."
        )
    else:
        under_text = f"Probability: {under_prob * 100:.1f}%\nNo book odds provided."

    embed.add_field(name="🔽 UNDER", value=under_text, inline=False)

    # Recommendation
    if ev_over is None and ev_under is None:
        rec = "No recommendation (no valid book odds)."
    elif ev_over is not None and ev_under is not None:
        if ev_over > ev_under and ev_over > 0:
            rec = f"🔼 OVER looks good (+{ev_over:.2f}% EV)."
        elif ev_under > ev_over and ev_under > 0:
            rec = f"🔽 UNDER looks good (+{ev_under:.2f}% EV)."
        else:
            # best side but <= 0 EV
            if ev_over >= ev_under:
                rec = f"Best side: OVER ({ev_over:.2f}% EV, not positive)."
            else:
                rec = f"Best side: UNDER ({ev_under:.2f}% EV, not positive)."
    else:
        # only one EV available
        if ev_over is not None and ev_over > 0:
            rec = f"🔼 OVER looks good (+{ev_over:.2f}% EV)."
        elif ev_under is not None and ev_under > 0:
            rec = f"🔽 UNDER looks good (+{ev_under:.2f}% EV)."
        else:
            rec = "No strong edge detected."

    embed.add_field(name="🏆 Recommendation", value=rec, inline=False)
    embed.set_footer(text="Model: Poisson on per-game stats | Use responsibly")

    return embed

# ============================================================
# Discord events & commands
# ============================================================

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user} (id: {bot.user.id})")

# ---------- Legacy BallDontLie command: !prop ----------

@bot.command(
    name="prop",
    help='Usage: !prop "Player Name" stat prop_line [book_over book_under]\n'
         'Example: !prop "Kristaps Porzingis" pts 22.5 -115 -105\n'
         'Data: BallDontLie season averages.'
)
async def prop(ctx, player: str, stat: str, prop_line: float, book_over: str = None, book_under: str = None):
    async with ctx.typing():
        expected_avg = None
        sa = get_player_season_avg(player)
        if sa:
            stat_map = {
                "pts": "pts", "points": "pts",
                "reb": "reb", "rebs": "reb",
                "ast": "ast", "assists": "ast",
            }
            key = stat_map.get(stat.lower())
            if key and key in sa:
                expected_avg = sa[key]

        if expected_avg is None:
            expected_avg = prop_line  # fallback

        over_prob = probability_over(prop_line, expected_avg)
        under_prob = 1.0 - over_prob

        embed = build_prop_embed(
            player=player,
            stat=stat,
            prop_line=prop_line,
            expected_avg=expected_avg,
            over_prob=over_prob,
            under_prob=under_prob,
            book_over=book_over,
            book_under=book_under,
            source_label="BallDontLie season averages",
            title_prefix="🎯"
        )

    await ctx.send(embed=embed)

# ---------- New command: !espnprop (TeamRankings + BDL) ----------

@bot.command(
    name="espnprop",
    help='Usage: !espnprop "Player Name" stat prop_line [book_over book_under]\n'
         'Example: !espnprop "LeBron James" ast 8.5 -115 -105\n'
         'Data: TeamRankings player stats (per game), fallback BallDontLie.'
)
async def espnprop(ctx, player: str, stat: str, prop_line: float, book_over: str = None, book_under: str = None):
    async with ctx.typing():
        expected_avg, source_label = get_expected_from_teamrankings_or_bdl(
            player_name=player,
            stat=stat,
            fallback_prop_line=prop_line
        )

        if expected_avg is None:
            await ctx.send(
                f"Could not find stats for **{player}** ({stat}). "
                f"Try a different stat or player spelling."
            )
            return

        over_prob = probability_over(prop_line, expected_avg)
        under_prob = 1.0 - over_prob

        embed = build_prop_embed(
            player=player,
            stat=stat,
            prop_line=prop_line,
            expected_avg=expected_avg,
            over_prob=over_prob,
            under_prob=under_prob,
            book_over=book_over,
            book_under=book_under,
            source_label=source_label,
            title_prefix="📊"
        )

    await ctx.send(embed=embed)

# ============================================================
# Run the bot
# ============================================================

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN env var not set.")
    bot.run(DISCORD_TOKEN)
# bot.py
import os
import math
import requests
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
from bs4 import BeautifulSoup

# Load local .env for development
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")   # set in .env / Render
ODDS_API_KEY   = os.getenv("ODDS_API_KEY")   # optional, unused for now

PREFIX = "!"
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ============================================================
# Helper math functions
# ============================================================

def poisson_p(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def poisson_cdf(k, lam):
    s = 0.0
    for i in range(0, k + 1):
        s += poisson_p(i, lam)
    return s

def probability_over(prop_line, expected_avg):
    """
    Over means X >= ceil(prop_line).
    """
    threshold = math.ceil(prop_line)
    cdf = poisson_cdf(threshold - 1, expected_avg)
    return 1.0 - cdf

def american_to_decimal(odds):
    if odds is None:
        return None
    try:
        odds = int(odds)
    except Exception:
        return None
    if odds < 0:
        return 1 + 100 / abs(odds)
    else:
        return 1 + odds / 100.0

def expected_value_percent(prob, book_decimal):
    if book_decimal is None:
        return None
    exp_return = prob * book_decimal - 1
    return exp_return * 100.0

# ============================================================
# Data fetchers: BallDontLie (for !prop)
# ============================================================

def get_player_season_avg(player_name, season=None):
    """
    BallDontLie season averages (simple, used by !prop).
    Returns dict like {'pts':..., 'reb':..., 'ast':...} or None.
    """
    search_url = f"https://www.balldontlie.io/api/v1/players?search={requests.utils.quote(player_name)}"
    r = requests.get(search_url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("data"):
        return None

    player = data["data"][0]
    player_id = player["id"]

    if season is None:
        # Simple: use recent season. You can change this year if needed.
        season = 2023

    sa_url = (
        f"https://www.balldontlie.io/api/v1/season_averages?"
        f"season={season}&player_ids[]={player_id}"
    )
    r2 = requests.get(sa_url, timeout=10)
    if r2.status_code != 200:
        return None
    sa = r2.json()
    if not sa.get("data"):
        return None
    return sa["data"][0]

# ============================================================
# Data fetchers: TeamRankings scrape (for !espnprop)
# ============================================================

TR_BASE = "https://www.teamrankings.com"

TR_STAT_PATHS = {
    "pts": "/nba/player-stat/points",
    "points": "/nba/player-stat/points",
    "reb": "/nba/player-stat/rebounds",
    "rebs": "/nba/player-stat/rebounds",
    "ast": "/nba/player-stat/assists",
    "assists": "/nba/player-stat/assists",
}

TR_STAT_HEADERS = {
    "pts": ["points", "pts", "points per game"],
    "points": ["points", "pts", "points per game"],
    "reb": ["rebounds", "reb", "rebounds per game"],
    "rebs": ["rebounds", "reb", "rebounds per game"],
    "ast": ["assists", "ast", "assists per game"],
    "assists": ["assists", "ast", "assists per game"],
}

def get_teamrankings_stat(player_name, stat):
    """
    Scrape TeamRankings NBA player tables to get per-game stat.
    Returns float or None.
    """
    key = stat.lower()
    path = TR_STAT_PATHS.get(key)
    if not path:
        return None

    url = TR_BASE + path
    try:
        r = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PropBot/1.0)"}
        )
    except Exception:
        return None

    if r.status_code != 200:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    # Try to locate the header row and find the proper stat column
    thead = table.find("thead")
    if not thead:
        return None
    header_cells = [th.get_text(strip=True) for th in thead.find_all("th")]

    # Find player column (usually first, but be safe)
    player_col = 0
    for idx, h in enumerate(header_cells):
        if "player" in h.lower():
            player_col = idx
            break

    # Find stat column based on header text
    stat_targets = TR_STAT_HEADERS.get(key, [])
    stat_col = None
    for idx, h in enumerate(header_cells):
        hl = h.lower()
        for t in stat_targets:
            if t in hl:
                stat_col = idx
                break
        if stat_col is not None:
            break

    if stat_col is None:
        # fallback: last column
        stat_col = len(header_cells) - 1

    # Now loop rows and find our player
    tbody = table.find("tbody")
    if not tbody:
        return None

    pname_lower = player_name.lower()
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) <= max(player_col, stat_col):
            continue
        name_text = cells[player_col].get_text(strip=True)
        if not name_text:
            continue

        # Rough match: contains name either way
        if pname_lower in name_text.lower() or name_text.lower() in pname_lower:
            stat_text = cells[stat_col].get_text(strip=True)
            try:
                return float(stat_text)
            except Exception:
                return None

    return None

def get_expected_from_teamrankings_or_bdl(player_name, stat, fallback_prop_line=None):
    """
    Priority:
      1) TeamRankings per-game
      2) BallDontLie season_averages
      3) fallback_prop_line (if provided)
    """
    # 1) TeamRankings
    tr_val = get_teamrankings_stat(player_name, stat)
    if tr_val is not None:
        return tr_val, "TeamRankings"

    # 2) BallDontLie
    sa = get_player_season_avg(player_name)
    if sa:
        stat_map = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebs": "reb",
            "ast": "ast", "assists": "ast",
        }
        key = stat_map.get(stat.lower())
        if key and key in sa:
            return sa[key], "BallDontLie season averages"

    # 3) Fallback
    if fallback_prop_line is not None:
        return fallback_prop_line, "Fallback = prop line"

    return None, "N/A"

# ============================================================
# Generic embed builder used by both commands
# ============================================================

def build_prop_embed(
    player,
    stat,
    prop_line,
    expected_avg,
    over_prob,
    under_prob,
    book_over,
    book_under,
    source_label: str,
    title_prefix: str = "🎯",
):
    book_dec_over = american_to_decimal(book_over) if book_over else None
    book_dec_under = american_to_decimal(book_under) if book_under else None

    fair_over = (1.0 / over_prob) if over_prob > 0 else float("inf")
    fair_under = (1.0 / under_prob) if under_prob > 0 else float("inf")
    ev_over = expected_value_percent(over_prob, book_dec_over) if book_dec_over else None
    ev_under = expected_value_percent(under_prob, book_dec_under) if book_dec_under else None

    embed = discord.Embed(
        title=f"{title_prefix} {player} - {stat.upper()}",
        color=0x0BB25F
    )
    embed.add_field(name="Prop Line", value=str(prop_line), inline=True)
    embed.add_field(name="Expected Avg", value=f"{expected_avg:.2f}", inline=True)
    embed.add_field(name="Source", value=source_label, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # OVER
    if book_dec_over:
        over_text = (
            f"Probability: {over_prob * 100:.1f}%\n"
            f"Fair Odds (dec): {fair_over:.2f}\n"
            f"Book Odds (amer): {book_over}\n"
            f"Book Odds (dec): {book_dec_over:.2f}\n"
            f"EV: {ev_over:.2f}%"
            if ev_over is not None
            else "No EV (book odds invalid)."
        )
    else:
        over_text = f"Probability: {over_prob * 100:.1f}%\nNo book odds provided."

    embed.add_field(name="🔼 OVER", value=over_text, inline=False)

    # UNDER
    if book_dec_under:
        under_text = (
            f"Probability: {under_prob * 100:.1f}%\n"
            f"Fair Odds (dec): {fair_under:.2f}\n"
            f"Book Odds (amer): {book_under}\n"
            f"Book Odds (dec): {book_dec_under:.2f}\n"
            f"EV: {ev_under:.2f}%"
            if ev_under is not None
            else "No EV (book odds invalid)."
        )
    else:
        under_text = f"Probability: {under_prob * 100:.1f}%\nNo book odds provided."

    embed.add_field(name="🔽 UNDER", value=under_text, inline=False)

    # Recommendation
    if ev_over is None and ev_under is None:
        rec = "No recommendation (no valid book odds)."
    elif ev_over is not None and ev_under is not None:
        if ev_over > ev_under and ev_over > 0:
            rec = f"🔼 OVER looks good (+{ev_over:.2f}% EV)."
        elif ev_under > ev_over and ev_under > 0:
            rec = f"🔽 UNDER looks good (+{ev_under:.2f}% EV)."
        else:
            # best side but <= 0 EV
            if ev_over >= ev_under:
                rec = f"Best side: OVER ({ev_over:.2f}% EV, not positive)."
            else:
                rec = f"Best side: UNDER ({ev_under:.2f}% EV, not positive)."
    else:
        # only one EV available
        if ev_over is not None and ev_over > 0:
            rec = f"🔼 OVER looks good (+{ev_over:.2f}% EV)."
        elif ev_under is not None and ev_under > 0:
            rec = f"🔽 UNDER looks good (+{ev_under:.2f}% EV)."
        else:
            rec = "No strong edge detected."

    embed.add_field(name="🏆 Recommendation", value=rec, inline=False)
    embed.set_footer(text="Model: Poisson on per-game stats | Use responsibly")

    return embed

# ============================================================
# Discord events & commands
# ============================================================

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user} (id: {bot.user.id})")

# ---------- Legacy BallDontLie command: !prop ----------

@bot.command(
    name="prop",
    help='Usage: !prop "Player Name" stat prop_line [book_over book_under]\n'
         'Example: !prop "Kristaps Porzingis" pts 22.5 -115 -105\n'
         'Data: BallDontLie season averages.'
)
async def prop(ctx, player: str, stat: str, prop_line: float, book_over: str = None, book_under: str = None):
    async with ctx.typing():
        expected_avg = None
        sa = get_player_season_avg(player)
        if sa:
            stat_map = {
                "pts": "pts", "points": "pts",
                "reb": "reb", "rebs": "reb",
                "ast": "ast", "assists": "ast",
            }
            key = stat_map.get(stat.lower())
            if key and key in sa:
                expected_avg = sa[key]

        if expected_avg is None:
            expected_avg = prop_line  # fallback

        over_prob = probability_over(prop_line, expected_avg)
        under_prob = 1.0 - over_prob

        embed = build_prop_embed(
            player=player,
            stat=stat,
            prop_line=prop_line,
            expected_avg=expected_avg,
            over_prob=over_prob,
            under_prob=under_prob,
            book_over=book_over,
            book_under=book_under,
            source_label="BallDontLie season averages",
            title_prefix="🎯"
        )

    await ctx.send(embed=embed)

# ---------- New command: !espnprop (TeamRankings + BDL) ----------

@bot.command(
    name="espnprop",
    help='Usage: !espnprop "Player Name" stat prop_line [book_over book_under]\n'
         'Example: !espnprop "LeBron James" ast 8.5 -115 -105\n'
         'Data: TeamRankings player stats (per game), fallback BallDontLie.'
)
async def espnprop(ctx, player: str, stat: str, prop_line: float, book_over: str = None, book_under: str = None):
    async with ctx.typing():
        expected_avg, source_label = get_expected_from_teamrankings_or_bdl(
            player_name=player,
            stat=stat,
            fallback_prop_line=prop_line
        )

        if expected_avg is None:
            await ctx.send(
                f"Could not find stats for **{player}** ({stat}). "
                f"Try a different stat or player spelling."
            )
            return

        over_prob = probability_over(prop_line, expected_avg)
        under_prob = 1.0 - over_prob

        embed = build_prop_embed(
            player=player,
            stat=stat,
            prop_line=prop_line,
            expected_avg=expected_avg,
            over_prob=over_prob,
            under_prob=under_prob,
            book_over=book_over,
            book_under=book_under,
            source_label=source_label,
            title_prefix="📊"
        )

    await ctx.send(embed=embed)

# ============================================================
# Run the bot
# ============================================================

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN env var not set.")
    bot.run(DISCORD_TOKEN)
