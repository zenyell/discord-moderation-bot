import os
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("DATABASE_PATH", "/tmp/bot_data.db")


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
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id         TEXT NOT NULL,
                command_name     TEXT NOT NULL,
                enabled          INTEGER DEFAULT 1,
                whitelist_roles  TEXT DEFAULT '',
                blacklist_mods   TEXT DEFAULT '',
                whitelist_users  TEXT DEFAULT '',
                blacklist_users  TEXT DEFAULT '',
                UNIQUE(guild_id, command_name)
            );
            CREATE TABLE IF NOT EXISTS profile_cache (
                user_id      TEXT PRIMARY KEY,
                username     TEXT,
                global_name  TEXT,
                avatar_url   TEXT,
                banner_url   TEXT,
                accent_color TEXT,
                bio          TEXT,
                badges       TEXT,
                updated_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS reaction_roles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                emoji      TEXT NOT NULL,
                role_id    TEXT NOT NULL,
                UNIQUE(guild_id, message_id, emoji)
            );
            CREATE TABLE IF NOT EXISTS autoroles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                role_id    TEXT NOT NULL,
                UNIQUE(guild_id, role_id)
            );
            CREATE TABLE IF NOT EXISTS tags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                name       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(guild_id, name)
            );
            CREATE TABLE IF NOT EXISTS triggers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                phrase     TEXT NOT NULL,
                response   TEXT NOT NULL,
                UNIQUE(guild_id, phrase)
            );
            CREATE TABLE IF NOT EXISTS suggestions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                author_id  TEXT NOT NULL,
                content    TEXT NOT NULL,
                upvotes    INTEGER DEFAULT 0,
                downvotes  INTEGER DEFAULT 0,
                message_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        for col, default in [
            ("whitelist_users", "''"),
            ("blacklist_users", "''"),
        ]:
            try:
                con.execute(f"ALTER TABLE command_settings ADD COLUMN {col} TEXT DEFAULT {default}")
                con.commit()
            except Exception:
                pass
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
        total    = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=?", (guild_id,)).fetchone()[0]
        kicks    = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='kick'", (guild_id,)).fetchone()[0]
        bans     = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='ban'", (guild_id,)).fetchone()[0]
        timeouts = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='timeout'", (guild_id,)).fetchone()[0]
        warns    = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='warn'", (guild_id,)).fetchone()[0]
        purges   = cur.execute("SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND action='purge'", (guild_id,)).fetchone()[0]
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

ALL_COMMANDS = [
    "kick", "ban", "unban", "timeout", "purge", "warn", "warnings", "modlogs",
    "blacklist", "unblacklist", "addword", "removeword", "userinfo"
]

_POLICY_DEFAULTS = {
    "enabled": 1,
    "whitelist_roles": "",
    "blacklist_mods": "",
    "whitelist_users": "",
    "blacklist_users": "",
}


def _row_to_policy(row):
    keys = row.keys()
    return {
        "enabled":         row["enabled"],
        "whitelist_roles": row["whitelist_roles"] or "",
        "blacklist_mods":  row["blacklist_mods"]  or "",
        "whitelist_users": row["whitelist_users"] if "whitelist_users" in keys else "",
        "blacklist_users": row["blacklist_users"] if "blacklist_users" in keys else "",
    }


def get_command_settings(guild_id):
    with closing(_conn()) as con:
        rows = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=?", (guild_id,)
        ).fetchall()
        result = {cmd: dict(_POLICY_DEFAULTS) for cmd in ALL_COMMANDS}
        for r in rows:
            result[r["command_name"]] = _row_to_policy(r)
        return result


def get_command_setting(guild_id, command_name):
    with closing(_conn()) as con:
        row = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, command_name)
        ).fetchone()
        if not row:
            return dict(_POLICY_DEFAULTS)
        return _row_to_policy(row)


def set_command_setting(guild_id, command_name, enabled=None, whitelist_roles=None,
                        blacklist_mods=None, whitelist_users=None, blacklist_users=None):
    with closing(_conn()) as con:
        existing = con.execute(
            "SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, command_name)
        ).fetchone()
        if existing:
            updates, vals = [], []
            for field, value in [
                ("enabled",         enabled),
                ("whitelist_roles", whitelist_roles),
                ("blacklist_mods",  blacklist_mods),
                ("whitelist_users", whitelist_users),
                ("blacklist_users", blacklist_users),
            ]:
                if value is not None:
                    updates.append(f"{field}=?")
                    vals.append(value)
            if updates:
                vals += [guild_id, command_name]
                con.execute(
                    f"UPDATE command_settings SET {', '.join(updates)} WHERE guild_id=? AND command_name=?",
                    vals
                )
        else:
            con.execute(
                """INSERT INTO command_settings
                   (guild_id, command_name, enabled, whitelist_roles, blacklist_mods, whitelist_users, blacklist_users)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    guild_id, command_name,
                    enabled         if enabled         is not None else 1,
                    whitelist_roles if whitelist_roles is not None else "",
                    blacklist_mods  if blacklist_mods  is not None else "",
                    whitelist_users if whitelist_users is not None else "",
                    blacklist_users if blacklist_users is not None else "",
                )
            )
        con.commit()


# ── profile cache ─────────────────────────────────────────────────────────────

def cache_profile(user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges):
    with closing(_conn()) as con:
        con.execute(
            """INSERT OR REPLACE INTO profile_cache
               (user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges, updated_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges)
        )
        con.commit()


def get_cached_profile(user_id):
    with closing(_conn()) as con:
        row = con.execute(
            "SELECT * FROM profile_cache WHERE user_id=?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


# ── reaction roles ────────────────────────────────────────────────────────────

def add_reaction_role(guild_id, message_id, channel_id, emoji, role_id):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR IGNORE INTO reaction_roles (guild_id, message_id, channel_id, emoji, role_id) VALUES (?,?,?,?,?)",
            (guild_id, message_id, channel_id, emoji, role_id)
        )
        con.commit()


def remove_reaction_role(rr_id):
    with closing(_conn()) as con:
        con.execute("DELETE FROM reaction_roles WHERE id=?", (rr_id,))
        con.commit()


def get_reaction_roles(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM reaction_roles WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()


def get_reaction_role(guild_id, message_id, emoji):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (guild_id, message_id, emoji)
        ).fetchone()


# ── autoroles ─────────────────────────────────────────────────────────────────

def add_autorole(guild_id, role_id):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR IGNORE INTO autoroles (guild_id, role_id) VALUES (?,?)",
            (guild_id, role_id)
        )
        con.commit()


def remove_autorole(guild_id, role_id):
    with closing(_conn()) as con:
        con.execute(
            "DELETE FROM autoroles WHERE guild_id=? AND role_id=?",
            (guild_id, role_id)
        )
        con.commit()


def get_autoroles(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM autoroles WHERE guild_id=?", (guild_id,)
        ).fetchall()


# ── tags ──────────────────────────────────────────────────────────────────────

def add_tag(guild_id, name, content):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR REPLACE INTO tags (guild_id, name, content) VALUES (?,?,?)",
            (guild_id, name.lower().strip(), content)
        )
        con.commit()


def remove_tag(guild_id, name):
    with closing(_conn()) as con:
        con.execute(
            "DELETE FROM tags WHERE guild_id=? AND name=?",
            (guild_id, name.lower().strip())
        )
        con.commit()


def get_tags(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM tags WHERE guild_id=? ORDER BY name ASC",
            (guild_id,)
        ).fetchall()


def get_tag(guild_id, name):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM tags WHERE guild_id=? AND name=?",
            (guild_id, name.lower().strip())
        ).fetchone()


# ── triggers ──────────────────────────────────────────────────────────────────

def add_trigger(guild_id, phrase, response):
    with closing(_conn()) as con:
        con.execute(
            "INSERT OR REPLACE INTO triggers (guild_id, phrase, response) VALUES (?,?,?)",
            (guild_id, phrase.lower().strip(), response)
        )
        con.commit()


def remove_trigger(trigger_id):
    with closing(_conn()) as con:
        con.execute("DELETE FROM triggers WHERE id=?", (trigger_id,))
        con.commit()


def get_triggers(guild_id):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM triggers WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()


def get_all_triggers(guild_id):
    """Used by the bot to check incoming messages."""
    with closing(_conn()) as con:
        return con.execute(
            "SELECT phrase, response FROM triggers WHERE guild_id=?",
            (guild_id,)
        ).fetchall()


# ── suggestions ───────────────────────────────────────────────────────────────

def add_suggestion(guild_id, author_id, content, message_id=None):
    with closing(_conn()) as con:
        con.execute(
            "INSERT INTO suggestions (guild_id, author_id, content, message_id) VALUES (?,?,?,?)",
            (guild_id, author_id, content, message_id)
        )
        con.commit()


def get_suggestions(guild_id, limit=50):
    with closing(_conn()) as con:
        return con.execute(
            "SELECT * FROM suggestions WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit)
        ).fetchall()


def update_suggestion_votes(message_id, upvotes, downvotes):
    with closing(_conn()) as con:
        con.execute(
            "UPDATE suggestions SET upvotes=?, downvotes=? WHERE message_id=?",
            (upvotes, downvotes, message_id)
        )
        con.commit()
