import os
import sys
import subprocess
import tempfile

import discord
from discord import Option
import aiosqlite
from dotenv import load_dotenv

# load .env
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DB_FILE       = os.getenv("DB_FILE", "bot_data.db")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in environment")

# helper to invoke `oj`
def run_oj(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "oj", *args],
        capture_output=True,
        text=True,
    )

async def oj_login(username: str, password: str):
    cp = run_oj([
        "login", "https://vjudge.net/user/login",
        "--username", username,
        "--password", password,
    ])
    if cp.returncode != 0:
        raise RuntimeError(f"oj login failed:\n{cp.stderr}")

async def oj_submit(problem_url: str, source_path: str, language: str) -> str:
    cp = run_oj([
        "submit", problem_url,
        "--language", language,
        source_path,
    ])
    if cp.returncode != 0:
        raise RuntimeError(f"oj submit failed:\n{cp.stderr}")
    # assumes last token in stdout is the submission ID
    return cp.stdout.strip().split()[-1]

async def oj_get_result(submission_id: str) -> dict:
    cp = run_oj(["get", submission_id])
    if cp.returncode != 0:
        raise RuntimeError(f"oj get failed:\n{cp.stderr}")
    # parse cp.stdout into a dict {verdict, time, memory}
    # simple example parsing, adjust to oj's actual output format:
    lines = cp.stdout.splitlines()
    # skip header, find your ID row
    for line in lines:
        if submission_id in line:
            parts = line.split()
            return {
                "verdict": parts[3],
                "time": parts[4],
                "memory": parts[5],
            }
    return {"verdict": "Unknown", "time": "N/A", "memory": "N/A"}

# Discord bot setup
intents = discord.Intents.default()
bot = discord.Bot(intents=intents)

async def safe_respond(ctx, *args, **kwargs):
    try:
        return await ctx.respond(*args, **kwargs)
    except discord.Forbidden:
        return await ctx.author.send(*args, **kwargs)

@bot.event
async def on_ready():
    # init SQLite
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password TEXT NOT NULL
            );""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS solves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                judge TEXT NOT NULL,
                problem_id TEXT NOT NULL,
                UNIQUE(user_id, judge, problem_id)
            );""")
        await db.commit()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.slash_command(description="Link your VJudge credentials")
async def vjudge_link(
    ctx: discord.ApplicationContext,
    username: Option(str, "Your VJudge username"),
    password: Option(str, "Your VJudge password")
):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO users(user_id, username, password) VALUES(?, ?, ?)"
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, password=excluded.password;",
            (ctx.author.id, username, password)
        )
        await db.commit()
    await safe_respond(ctx, "✅ Credentials stored securely!", ephemeral=True)

@bot.slash_command(description="Submit code via VJudge")
async def submit(
    ctx: discord.ApplicationContext,
    judge: Option(str, "Judge name, e.g. 'CF'"),
    problem_id: Option(str, "Problem code, e.g. '123A'"),
    language: Option(str, "Language slug, e.g. 'GNU G++17'"),
    code: Option(str, "Your source code")
):
    await ctx.defer(ephemeral=True)
    # fetch creds
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT username, password FROM users WHERE user_id = ?",
            (ctx.author.id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return await safe_respond(ctx, "⚠️ Please `/vjudge_link` first.", ephemeral=True)
    username, password = row

    try:
        await oj_login(username, password)
        # write code to temp file
        with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False, mode="w") as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        url = f"https://vjudge.net/problem/{judge}-{problem_id}"
        run_id = await oj_submit(url, tmp_path, language)
        result = await oj_get_result(run_id)
    except Exception as e:
        return await safe_respond(ctx, f"❌ Error: {e}", ephemeral=True)

    # record solve if accepted
    if result["verdict"].lower() == "accepted":
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT OR IGNORE INTO solves(user_id, judge, problem_id) VALUES(?, ?, ?)",
                (ctx.author.id, judge, problem_id)
            )
            await db.commit()

    # build embed
    code_block = f"```{language.lower()}\n{code}\n```"
    embed = discord.Embed(
        title=f"{judge}-{problem_id}",
        description=f"**{result['verdict']}**",
        color=0x00FF00 if result["verdict"].lower()=="accepted" else 0xFF0000
    )
    embed.add_field(name="Time", value=result["time"], inline=True)
    embed.add_field(name="Memory", value=result["memory"], inline=True)
    embed.add_field(name="Code", value=code_block, inline=False)

    await safe_respond(ctx, embed=embed)

@bot.slash_command(description="Show solve leaderboard")
async def leaderboard(ctx: discord.ApplicationContext):
    query = """
        SELECT user_id, COUNT(*) as solves
        FROM solves
        GROUP BY user_id
        ORDER BY solves DESC;
    """
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()

    if not rows:
        return await safe_respond(ctx, "No solves yet.", ephemeral=True)

    lines = []
    for user_id, cnt in rows:
        user = bot.get_user(user_id)
        name = user.display_name if user else str(user_id)
        lines.append(f"**{name}** — {cnt} solve{'s' if cnt!=1 else ''}")
    await safe_respond(ctx, "🏆 **Leaderboard**\n" + "\n".join(lines))

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
