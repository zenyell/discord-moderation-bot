"""
Heartbeat writer — import and call start() in your bot's on_ready event.
Writes /tmp/bot_heartbeat every 20 seconds so the dashboard can read it.
"""
import asyncio, time, os

HEARTBEAT_FILE = os.getenv("HEARTBEAT_PATH", "/tmp/bot_heartbeat")

async def _write_loop(bot):
    while True:
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(f"{time.time()}|{bot.user.name}")
        except Exception:
            pass
        await asyncio.sleep(20)

def start(bot):
    """Call this inside on_ready: heartbeat.start(bot)"""
    bot.loop.create_task(_write_loop(bot))
