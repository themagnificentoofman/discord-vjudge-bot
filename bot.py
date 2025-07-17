import os
import sys
import subprocess
import tempfile
import asyncio

import discord
from discord import Option
from dotenv import load_dotenv

import aiosqlite
import asyncpg

# ──────── Load Environment ────────
load_dotenv()  # reads .env in project root

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in environment")

DATABASE_URL = os.getenv("DATABASE_URL")
DB_FILE      = os.getenv("DB_FILE", "bot_data.db")  # used only if DATABASE_URL is absent

# ──────── OJ Helpers ────────

def run_oj(args: list[str]) -> subprocess.CompletedProcess:
    """Invoke `oj` as a module under this Python interpreter."""
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
        source_path
    ])
    if cp.returncode != 0:
        raise RuntimeError(f"oj submit failed:\n{cp.stderr}")
    # assume last token of stdout is the submission ID
    return cp.stdout.strip().split()[-1]

async def oj_get_result(submission_id: str) -> dict:
    """Poll once for status; user code should loop if needed."""
    cp = run_oj(["get", submission_id])
    if cp.returncode != 0:
        raise RuntimeError(f"oj get failed:\n{cp.stderr}")
    # parse the table: find line containing submission_id
    for line in cp.stdout.splitlines():
        if submission_id in line:
            parts = line.split()
            # typical columns: ID, date, problem, verdict, time, memory, ...
            return {
                "verdict": parts[3],
                "time":    parts[4],
                "memory":  parts[5],
            }
    return {"verdict": "Unknown", "time": "N/A", "memory": "N/A"}

# ──────── Database Initialization ────────

async def init_db():
    """
    Create tables in either Postgres (via asyncpg) or SQLite (via aiosqlite).
    Returns:
      - asyncpg.Pool instance if using Postgres
      - None if using SQLite
    """
    if DATABASE_URL:
        pool = await asyncpg.create_pool(DATABASE_URL)
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    username   TEXT   NOT NULL,
                    password   TEXT   NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS solves (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL,
                    judge       TEXT   NOT NULL,
                    problem_id  TEXT   NOT NULL,
                    UNIQUE(user_id, judge, problem_id)
                );
            """)
        return pool
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    username   TEXT    NOT NULL,
                    password   TEXT    NOT NULL
                );
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS solves (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    judge       TEXT    NOT NULL,
                    problem_id  TEXT    NOT NULL,
                    UNIQUE(user_id, judge, problem_id)
                );
            """)
            await db.commit()
        return None

# ──────── Discord Bot Setup ────────

intents = discord.Intents.default()
bot = discord.Bot(intents=intents)
db_pool = None  # will hold asyncpg.Pool if DATABASE_URL is set

async def safe_respond(ctx, *args, **kwargs):
    """Try ctx.respond, fallback to DM on permissions error."""
    try:
        return await ctx.respond(*args, **kwargs)
    except discord.Forbidden:
        return await ctx.author.send(*args, **kwargs)

@bot.event
async def on_ready():
    global db_pool
    db_pool = await init_db()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# ──────── Slash Commands ────────

GUILD_ID = 1394424001845137468  # replace with *your* test server’s ID

@bot.slash_command(
    name="vjudge_link",
    description="Link your VJudge credentials",
    guild_ids=[GUILD_ID],
)
async def vjudge_link(
    ctx: discord.ApplicationContext,
    username: Option(str, "Your VJudge username"),
    password: Option(str, "Your VJudge password")
):
    """Store or update Discord user → VJudge credentials."""
    if db_pool:
        # Postgres
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users(user_id, username, password)
                VALUES($1, $2, $3)
                ON CONFLICT(user_id) DO UPDATE
                  SET username = EXCLUDED.username,
                      password = EXCLUDED.password;
            """, ctx.author.id, username, password)
    else:
        # SQLite
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                INSERT INTO users(user_id, username, password)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  username = excluded.username,
                  password = excluded.password;
            """, (ctx.author.id, username, password))
            await db.commit()

    await safe_respond(ctx, "✅ Credentials stored securely!", ephemeral=True)

@bot.slash_command(description="Submit code via VJudge")
async def submit(
    ctx: discord.ApplicationContext,
    judge:      Option(str, "Judge code, e.g. 'CF'"),
    problem_id: Option(str, "Problem number, e.g. '123A'"),
    language:   Option(str, "Language slug, e.g. 'GNU G++17'"),
    code:       Option(str, "Your complete source code")
):
    """Log in to VJudge, submit code, record solves, and show verdict."""
    await ctx.defer(ephemeral=True)

    # 1) Fetch credentials
    row = None
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT username, password FROM users WHERE user_id=$1",
                ctx.author.id
            )
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT username, password FROM users WHERE user_id = ?",
                (ctx.author.id,)
            ) as cur:
                row = await cur.fetchone()

    if not row:
        return await safe_respond(ctx,
            "⚠️ You must first link your credentials with `/vjudge_link`.",
            ephemeral=True
        )
    username, password = row

    # 2) Perform login & submission
    try:
        await oj_login(username, password)

        # write code to temp file
        suffix = ".cpp" if "c++" in language.lower() or "cpp" in language.lower() else ".txt"
        with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        problem_url = f"https://vjudge.net/problem/{judge}-{problem_id}"
        run_id = await oj_submit(problem_url, tmp_path, language)

        # 3) Poll until verdict (timeout after e.g. 60s)
        verdict_data = None
        for _ in range(30):
            verdict_data = await oj_get_result(run_id)
            if verdict_data["verdict"].lower() not in ("running", "judging"):
                break
            await asyncio.sleep(2)
        if not verdict_data:
            verdict_data = {"verdict": "Timeout", "time": "N/A", "memory": "N/A"}

    except Exception as e:
        return await safe_respond(ctx,
            f"❌ Submission error: `{e}`", ephemeral=True
        )

    # 4) Record solve if Accepted
    if verdict_data["verdict"].lower() == "accepted":
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO solves(user_id, judge, problem_id)
                    VALUES($1, $2, $3)
                    ON CONFLICT DO NOTHING;
                """, ctx.author.id, judge, problem_id)
        else:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("""
                    INSERT OR IGNORE INTO solves(user_id, judge, problem_id)
                    VALUES(?, ?, ?)
                """, (ctx.author.id, judge, problem_id))
                await db.commit()

    # 5) Build and send embed
    code_block = f"```{language.lower()}\n{code}\n```"
    color = 0x00FF00 if verdict_data["verdict"].lower()=="accepted" else 0xFF0000
    embed = discord.Embed(
        title=f"{judge}-{problem_id}",
        description=f"**{verdict_data['verdict']}**",
        color=color
    )
    embed.add_field(name="Time",   value=verdict_data["time"],   inline=True)
    embed.add_field(name="Memory", value=verdict_data["memory"], inline=True)
    embed.add_field(name="Your Code", value=code_block, inline=False)

    await safe_respond(ctx, embed=embed)

@bot.slash_command(description="Show solve leaderboard")
async def leaderboard(ctx: discord.ApplicationContext):
    """Aggregate accepted solves per user and display a ranking."""
    if db_pool:
        rows = await db_pool.fetch("""
            SELECT user_id, COUNT(*) AS solves
              FROM solves
             GROUP BY user_id
             ORDER BY solves DESC
        """)
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("""
                SELECT user_id, COUNT(*) AS solves
                  FROM solves
                 GROUP BY user_id
                 ORDER BY solves DESC
            """) as cur:
                rows = await cur.fetchall()

    if not rows:
        return await safe_respond(ctx, "No solves recorded yet.", ephemeral=True)

    lines = []
    for rec in rows:
        user_id = rec[0]
        cnt     = rec[1]
        member  = bot.get_user(user_id)
        name    = member.display_name if member else str(user_id)
        lines.append(f"**{name}** — {cnt} solve{'s' if cnt != 1 else ''}")

    await safe_respond(ctx, "🏆 **Leaderboard**\n" + "\n".join(lines))

# ──────── Entry Point ────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
