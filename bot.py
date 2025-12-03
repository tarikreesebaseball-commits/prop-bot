# bot.py
import os
import math
import requests
from dotenv import load_dotenv
import discord
from discord.ext import commands

from main_pipeline import run_full_model  # uses your big model

# Load local .env
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ODDS_API_KEY  = os.getenv("ODDS_API_KEY")  # optional

PREFIX = "!"
intents = discord.Intents.default()
intents.message_content = True  # IMPORTANT
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ----------------- Helper math functions -----------------
def poisson_p(k, lam):
    return math.exp(-lam) * (lam**k) / math.factorial(k)

def poisson_cdf(k, lam):
    s = 0.0
    for i in range(0, k+1):
        s += poisson_p(i, lam)
    return s

def probability_over(prop_line, expected_avg):
    threshold = math.ceil(prop_line)
    cdf = poisson_cdf(threshold - 1, expected_avg)
    return 1.0 - cdf

def american_to_decimal(odds):
    if odds is None:
        return None
    try:
        odds = int(odds)
    except:
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

# ----------------- Data fetcher for !prop -----------------
def get_player_season_avg(player_name, season=None):
    """
    Uses Balldontlie to find season averages.
    Returns dict with keys like 'pts','reb','ast'.
    """
    search_url = (
        "https://www.balldontlie.io/api/v1/players?"
        f"search={requests.utils.quote(player_name)}"
    )
    r = requests.get(search_url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("data"):
        return None

    player = data["data"][0]
    player_id = player["id"]

    if season is None:
        season = 2023  # change if needed
    sa_url = (
        "https://www.balldontlie.io/api/v1/season_averages?"
        f"season={season}&player_ids[]={player_id}"
    )
    r2 = requests.get(sa_url, timeout=10)
    if r2.status_code != 200:
        return None
    sa = r2.json()
    if not sa.get("data"):
        return None

    return sa["data"][0]

# ----------------- Discord events -----------------
@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user} (id: {bot.user.id})")

# Show errors in Discord so we can see what’s wrong
@bot.event
async def on_command_error(ctx, error):
    await ctx.send(f"❌ Command error: `{error}`")

# Simple test command
@bot.command(name="test", help="Check if the bot is alive.")
async def test_cmd(ctx):
    await ctx.send("✅ Bot is alive and reading commands!")

# ----------------- !prop command (PTS / REB / AST) -----------------
@bot.command(
    name="prop",
    help='Usage: !prop "Player Name" stat prop_line [book_over book_under]\n'
         'Example: !prop "Kristaps Porzingis" pts 22.5 -115 -105'
)
async def prop(ctx, player: str, stat: str, prop_line: float,
               book_over: str = None, book_under: str = None):

    async with ctx.typing():
        stat_map = {
            "pts": "pts", "point": "pts", "points": "pts",
            "reb": "reb", "rebs": "reb", "rebounds": "reb",
            "ast": "ast", "assist": "ast", "assists": "ast",
        }

        stat_key = stat_map.get(stat.lower())
        if stat_key is None:
            await ctx.send(
                "❌ Unknown stat. Use one of: pts, points, reb, rebounds, ast, assists."
            )
            return

        # 1) Fetch season avg
        season_avg = get_player_season_avg(player)
        if season_avg:
            expected_avg = season_avg.get(stat_key)
        else:
            expected_avg = None

        if expected_avg is None:
            expected_avg = prop_line  # fallback

        # 2) Probabilities
        over_prob = probability_over(prop_line, expected_avg)
        under_prob = 1.0 - over_prob

        # 3) Book odds
        book_dec_over = american_to_decimal(book_over) if book_over else None
        book_dec_under = american_to_decimal(book_under) if book_under else None

        # 4) Fair odds + EV
        fair_over = (1.0 / over_prob) if over_prob > 0 else float("inf")
        fair_under = (1.0 / under_prob) if under_prob > 0 else float("inf")
        ev_over = expected_value_percent(over_prob, book_dec_over) if book_dec_over else None
        ev_under = expected_value_percent(under_prob, book_dec_under) if book_dec_under else None

        nice_stat_name = {
            "pts": "Points",
            "reb": "Rebounds",
            "ast": "Assists",
        }.get(stat_key, stat.upper())

        embed = discord.Embed(
            title=f"🎯 {player} - {nice_stat_name}",
            color=0x0BB25F
        )
        embed.add_field(name="Prop Line", value=str(prop_line), inline=True)
        embed.add_field(name="Expected Average", value=f"{expected_avg:.2f}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # OVER
        if book_dec_over is not None:
            over_text = (
                f"Probability: {over_prob*100:.1f}%\n"
                f"Fair Odds (dec): {fair_over:.2f}\n"
                f"Book Odds (amer): {book_over}\n"
                f"Book Odds (dec): {book_dec_over:.2f}\n"
                f"EV: {ev_over:.2f}%"
            )
        else:
            over_text = (
                f"Probability: {over_prob*100:.1f}%\n"
                f"Fair Odds (dec): {fair_over:.2f}\n"
                "No book odds provided."
            )
        embed.add_field(name="🔼 OVER", value=over_text, inline=False)

        # UNDER
        if book_dec_under is not None:
            under_text = (
                f"Probability: {under_prob*100:.1f}%\n"
                f"Fair Odds (dec): {fair_under:.2f}\n"
                f"Book Odds (amer): {book_under}\n"
                f"Book Odds (dec): {book_dec_under:.2f}\n"
                f"EV: {ev_under:.2f}%"
            )
        else:
            under_text = (
                f"Probability: {under_prob*100:.1f}%\n"
                f"Fair Odds (dec): {fair_under:.2f}\n"
                "No book odds provided."
            )
        embed.add_field(name="🔽 UNDER", value=under_text, inline=False)

        # Recommendation
        if ev_over is None and ev_under is None:
            rec = "No recommendation (no book odds provided)."
        else:
            best_side = None
            best_ev = None
            if ev_over is not None:
                best_side = "OVER"
                best_ev = ev_over
            if ev_under is not None and (best_ev is None or ev_under > best_ev):
                best_side = "UNDER"
                best_ev = ev_under

            if best_ev is not None and best_ev > 0:
                rec = f"{'🔼' if best_side=='OVER' else '🔽'} {best_side} looks good (+{best_ev:.2f}% EV)."
            else:
                rec = f"Best side: {best_side} ({best_ev:.2f}% EV), but EV is not positive."
        embed.add_field(name="🏆 Recommendation", value=rec, inline=False)
        embed.set_footer(text="Model: Poisson on season averages | Bet responsibly.")

    await ctx.send(embed=embed)

# ----------------- !model command (full pipeline) -----------------
@bot.command(
    name="model",
    help="Run the full NBA model (last 10 days data) and show game total + EV."
)
async def model(ctx):
    async with ctx.typing():
        try:
            result = run_full_model(days_back=10)
        except Exception as e:
            await ctx.send(f"❌ Error running model: `{e}`")
            return

        if not result:
            await ctx.send("❌ Model returned no result.")
            return

        used_real = result.get("used_real_data", False)
        src = "balldontlie (last 10 days)" if used_real else "synthetic demo data"

        proj_total = result.get("proj_game_total", 0.0)
        book_total = result.get("book_total", proj_total)
        p_over = result.get("p_over", 0.5)
        ev = result.get("ev", 0.0)

        embed = discord.Embed(
            title="📊 Full Game Model (Demo)",
            description=f"Data source: **{src}**",
            color=0x3498DB,
        )

        embed.add_field(name="Model Total", value=f"{proj_total:.2f}", inline=True)
        embed.add_field(name="Book Total", value=f"{book_total:.2f}", inline=True)
        embed.add_field(name="P(Over)", value=f"{p_over*100:.1f}%", inline=True)
        embed.add_field(name="EV on Over @ -110", value=f"{ev*100:.2f}%", inline=False)

        tp = result.get("top_players")
        if tp is not None and not tp.empty:
            lines = []
            for _, r in tp.iterrows():
                lines.append(
                    f"**{r['name']}** ({r['team']}, {r['pos']}) – "
                    f"{r['proj_pts']:.1f} pts, {r['proj_min']:.1f} min"
                )
            embed.add_field(
                name="Top Projected Scorers",
                value="\n".join(lines),
                inline=False,
            )

        await ctx.send(embed=embed)

# ----------------- Run bot -----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN env var not set. "
              "Create a .env file with DISCORD_TOKEN=your_token.")
    else:
        bot.run(DISCORD_TOKEN)
