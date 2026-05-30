# Discord Moderation Bot + Modern Admin Dashboard

A Python Discord moderation starter built with `discord.py`, SQLite, and Flask.
The dashboard uses a modern dark gray / near-black UI with rounded cards, clean spacing, and a minimal admin layout.

## Features

- Slash moderation commands: kick, ban, unban, timeout, purge, warn, warnings, modlogs
- SQLite warning storage and moderation logs
- Auto-role on member join
- Basic spam timeout protection
- Basic bad-words filter
- Flask admin dashboard with overview & settings
- Discord OAuth-ready environment fields

## Project Structure

```
discord-moderation-bot/
├── .env.example
├── README.md
├── requirements.txt
├── bot.py
├── dashboard.py
├── database.py
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    └── settings.html
```

## Setup

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable **Server Members Intent** and **Message Content Intent**.
3. Copy `.env.example` to `.env` and fill in your values.
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Start the bot:
   ```bash
   python bot.py
   ```
6. Start the dashboard in another terminal:
   ```bash
   python dashboard.py
   ```

## Dashboard Login

Default local login (from `.env.example`):
- **Username:** `admin`
- **Password:** `admin123`

Change these before deploying anywhere public.

## Required Bot Permissions

- View Channels, Send Messages, Read Message History
- Manage Messages, Kick Members, Ban Members, Moderate Members

## Notes

- Keep your bot token secret.
- SQLite is used for easy local storage.
- Extend with Discord OAuth, tickets, logging embeds, or role-based access later.
