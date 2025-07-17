# Discord VJudge Bot

[![CI & Deploy](https://github.com/<your-username>/<your-repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-username>/<your-repo>/actions)

A Discord slash‑command bot that lets server members submit code to VJudge on your own account, records accepted solves in a database, and shows a community leaderboard.

---

## Features

- **Link VJudge Credentials** (`/vjudge_link`)  
- **Submit Code** (`/submit judge:<JF> problem_id:<123A> language:<cpp> code:<…>`)  
- **Leaderboard** (`/leaderboard`)  
- Persistent storage in **PostgreSQL** (or SQLite fallback)  
- Syntax‑highlighted code in embeds  
- Permission‑fallback (sends DMs if the bot can’t post in-channel)  
- 24/7 hosting on Heroku  
- Automated **CI** (lint, test) and **CD** (auto‑deploy) via GitHub Actions  
