import os
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("DATABASE_PATH", "bot_data.db")


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with closing(_conn()) as con:
        cur = con.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS warnings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                user_id      TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason       TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mod_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                action       TEXT NOT NULL,
                target_id    TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason       TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS blacklisted_words (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                word       TEXT NOT NULL,
                added_by   TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, word)
            );
            CREATE TABLE IF NOT EXISTS blacklisted_users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                reason     TEXT,
                added_by   TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS command_settings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                command_name  TEXT NOT NULL,
                enabled       INTEGER DEFAULT 1,
                whitelist_roles TEXT DEFAULT '',
                blacklist_mods  TEXT DEFAULT '',
                UNIQUE(guild_id, command_name)
            );
        """)
        con.commit()


# ── warnings ──────────────────────────────────────────────────────────────────

def add_warning(guild_id, user_id, moderator_id, reason=None):
    with closing(_conn()) as con:
        con.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?,?,?,?)",
            (guild_id, user_id, moderator_id, reason)
        )
        con.commit()


def get_warnings(guild_id, user_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
            (guild_id, user_id)
        ).fetchall()


def clear_warnings(guild_id, user_id):
    with closing(_conn()) as con:
        con.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        con.commit()


# ── mod logs ──────────────────────────────────────────────────────────────────

def log_action(guild_id, action, target_id, moderator_id, reason=None):
    with closing(_conn()) as con:
        con.execute(
            "INSERT INTO mod_logs (guild_id, action, target_id, moderator_id, reason) VALUES (?,?,?,?,?)",
            (guild_id, action, target_id, moderator_id, reason)
        )
        con.commit()


def recent_logs(guild_id, limit=20):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM mod_logs WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit)
        ).fetchall()


def logs_for_user(guild_id, user_id, limit=50):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM mod_logs WHERE guild_id=? AND target_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, user_id, limit)
        ).fetchall()


def log_stats(guild_id):
    with closing(_conn()) as con:
        cur = con.cursor()
        total   = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=?", (guild_id,)).fetchone()[0]
        kicks   = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='kick'", (guild_id,)).fetchone()[0]
        bans    = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='ban'", (guild_id,)).fetchone()[0]
        timeouts= cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='timeout'", (guild_id,)).fetchone()[0]
        warns   = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='warn'", (guild_id,)).fetchone()[0]
        purges  = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='purge'", (guild_id,)).fetchone()[0]
        return {"total": total, "kicks": kicks, "bans": bans, "timeouts": timeouts, "warnings": warns, "purges": purges}


# ── settings ──────────────────────────────────────────────────────────────────

def get_setting(key, default=None):
    with closing(_conn()) as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with closing(_conn()) as con:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        con.commit()


# ── blacklisted words ─────────────────────────────────────────────────────────

def get_blacklisted_words(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM blacklisted_words WHERE guild_id=? ORDER BY created_at DESC",
            (guild_id,)
        ).fetchall()


def get_blacklisted_words_list(guild_id):
    with closing(_conn()) as con:
        rows = con.execute(
            "SELECT word FROM blacklisted_words WHERE guild_id=?", (guild_id,)
        ).fetchall()
        return [r["word"] for r in rows]


def add_blacklisted_word(guild_id, word, added_by=None):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR IGNORE INTO blacklisted_words (guild_id, word, added_by) VALUES (?,?,?)",
            (guild_id, word.lower().strip(), added_by)
        )
        con.commit()


def remove_blacklisted_word(guild_id, word):
    with closing(_conn()) as con:
        con.execute(
            "DELETE FROM blacklisted_words WHERE guild_id=? AND word=?",
            (guild_id, word.lower().strip())
        )
        con.commit()


# ── blacklisted users ─────────────────────────────────────────────────────────

def get_blacklisted_users(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM blacklisted_users WHERE guild_id=? ORDER BY created_at DESC",
            (guild_id,)
        ).fetchall()


def is_user_blacklisted(guild_id, user_id):
    with closing(_conn()) as con:
        row = con.execute(
            "SELECT 1 FROM blacklisted_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ).fetchone()
        return bool(row)


def add_blacklisted_user(guild_id, user_id, reason=None, added_by=None):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR IGNORE INTO blacklisted_users (guild_id, user_id, reason, added_by) VALUES (?,?,?,?)",
            (guild_id, user_id, reason, added_by)
        )
        con.commit()


def remove_blacklisted_user(guild_id, user_id):
    with closing(_conn()) as con:
        con.execute(
            "DELETE FROM blacklisted_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        )
        con.commit()


# ── command settings ──────────────────────────────────────────────────────────

ALL_COMMANDS = ["kick", "ban", "unban", "timeout", "purge", "warn", "warnings", "modlogs",
                "blacklist", "unblacklist", "addword", "removeword", "userinfo"]


def get_command_settings(guild_id):
    with closing(_conn()) as con:
        rows = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=?", (guild_id,)
        ).fetchall()
        result = {cmd: {"enabled": 1, "whitelist_roles": "", "blacklist_mods": ""} for cmd in ALL_COMMANDS}
        for r in rows:
            result[r["command_name"]] = {
                "enabled": r["enabled"],
                "whitelist_roles": r["whitelist_roles"] or "",
                "blacklist_mods": r["blacklist_mods"] or ""
            }
        return result


def get_command_setting(guild_id, command_name):
    with closing(_conn()) as con:
        row = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, command_name)
        ).fetchone()
        if not row:
            return {"enabled": 1, "whitelist_roles": "", "blacklist_mods": ""}
        return {
            "enabled": row["enabled"],
            "whitelist_roles": row["whitelist_roles"] or "",
            "blacklist_mods": row["blacklist_mods"] or ""
        }


def set_command_setting(guild_id, command_name, enabled=None, whitelist_roles=None, blacklist_mods=None):
    with closing(_conn()) as con:
        existing = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, command_name)
        ).fetchone()
        if existing:
            updates = []
            vals = []
            if enabled is not None:
                updates.append("enabled=?")
                vals.append(enabled)
            if whitelist_roles is not None:
                updates.append("whitelist_roles=?")
                vals.append(whitelist_roles)
            if blacklist_mods is not None:
                updates.append("blacklist_mods=?")
                vals.append(blacklist_mods)
            if updates:
                vals += [guild_id, command_name]
                con.execute(f"UPDATE command_settings SET {', '.join(updates)} WHERE guild_id=? AND command_name=?", vals)
        else:
            con.execute(
                "INSERT INTO command_settings (guild_id, command_name, enabled, whitelist_roles, blacklist_mods) VALUES (?,?,?,?,?)",
                (guild_id, command_name, enabled if enabled is not None else 1,
                 whitelist_roles or "", blacklist_mods or "")
            )
        con.commit()
