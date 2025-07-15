# Discord VJudge Bot

A Discord slash‑command bot that lets server members submit code to VJudge from *your* account, persists solves, and shows a leaderboard.

## Features

- `/vjudge_link` – store VJudge credentials
- `/submit` – submit code (via `oj`) and get verdicts
- `/leaderboard` – rank users by accepted solves
- Persistent storage with SQLite
- Syntax‑highlighted code in embeds
- Permission fallback to DMs

## Setup

1. **Clone & venv**  
   ```bash
   git clone <repo>
   cd discord-vjudge-bot
   python3 -m venv venv
   source venv/bin/activate    # or `.venv\Scripts\Activate.ps1`
