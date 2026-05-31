import threading
import time
import database as db

_started = False


def start(bot):
    global _started
    if _started:
        return
    _started = True

    def _loop():
        while True:
            try:
                db.set_setting("bot_online", "1")
                db.set_setting("bot_name", str(bot.user) if bot.user else "")
            except Exception:
                pass
            time.sleep(20)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
