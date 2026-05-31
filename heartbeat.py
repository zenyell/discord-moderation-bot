import os
import threading
import time

HEARTBEAT_FILE = os.getenv("HEARTBEAT_PATH", "/tmp/bot_heartbeat")

_started = False


def start(bot):
    global _started
    if _started:
        return
    _started = True

    def _loop():
        import database as db
        while True:
            try:
                name = str(bot.user) if bot.user else "Bot"
                # Write the heartbeat file that dashboard.py reads
                with open(HEARTBEAT_FILE, "w") as f:
                    f.write(f"{time.time()}|{name}")
                # Also persist to DB for other uses
                db.set_setting_sync("bot_online", "1")
                db.set_setting_sync("bot_name", name)
            except Exception:
                pass
            time.sleep(20)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
