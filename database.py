import os
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("DATABASE_PATH", "bot_data.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_conn()) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS warnings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason      TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS mod_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                action       TEXT NOT NULL,
                target_id    TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason       TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()


def add_warning(guild_id, user_id, moderator_id, reason=None):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO warnings (guild_id,user_id,moderator_id,reason) VALUES (?,?,?,?)",
            (guild_id, user_id, moderator_id, reason),
        )
        conn.commit()


def get_warnings(guild_id, user_id):
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
            (guild_id, user_id),
        ).fetchall()


def log_action(guild_id, action, target_id, moderator_id, reason=None):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO mod_logs (guild_id,action,target_id,moderator_id,reason) VALUES (?,?,?,?,?)",
            (guild_id, action, target_id, moderator_id, reason),
        )
        conn.commit()


def recent_logs(guild_id, limit=50):
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM mod_logs WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()


def log_summary(guild_id):
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM mod_logs WHERE guild_id=? GROUP BY action",
            (guild_id,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM mod_logs WHERE guild_id=?", (guild_id,)
        ).fetchone()
        summary = {r["action"]: r["cnt"] for r in rows}
        summary["total_logs"] = total["cnt"] if total else 0
        return summary


def get_setting(key, default=None):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
