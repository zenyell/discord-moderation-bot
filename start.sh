#!/usr/bin/env bash
# Starts the Discord bot in the background, then starts the dashboard web server.
# Both processes share the same /tmp directory, so they use the same SQLite DB.

set -e

echo "[start.sh] Starting Discord bot in background..."
python bot.py &
BOT_PID=$!
echo "[start.sh] Bot PID: $BOT_PID"

echo "[start.sh] Starting dashboard (gunicorn)..."
exec gunicorn dashboard:app --workers 1 --bind 0.0.0.0:${PORT:-5000} --timeout 120
