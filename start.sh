#!/usr/bin/env bash
# start.sh is no longer used for production.
# The bot and dashboard are now separate Render services defined in render.yaml.
# This script is kept only for local development convenience.

set -e

echo "[start.sh] Starting Discord bot in background..."
python bot.py &
BOT_PID=$!
echo "[start.sh] Bot PID: $BOT_PID"

echo "[start.sh] Starting dashboard..."
exec gunicorn dashboard:app --workers 1 --bind 0.0.0.0:${PORT:-5000} --timeout 120
