# Discord VJudge Bot

A Discord slash‑command bot that lets server members submit code to VJudge from *your* account, persists solves, and shows a leaderboard.

## Features

- `/vjudge_link` – store VJudge credentials
- `/submit` – submit code (via `oj`) and get verdicts
- `/leaderboard` – rank users by accepted solves
- Persistent storage with PostgreSQL (or SQLite fallback)
- Syntax‑highlighted code in embeds
- Permission fallback to DMs
