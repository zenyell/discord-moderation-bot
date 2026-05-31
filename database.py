import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the same directory as this file, regardless of cwd
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

import libsql_client
import asyncio

_raw_url    = os.getenv("TURSO_URL",   "").strip()
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "").strip()

if _raw_url.startswith("libsql://"):
    TURSO_URL = "https://" + _raw_url[len("libsql://"):]
elif _raw_url.startswith("wss://") or _raw_url.startswith("ws://"):
    TURSO_URL = "https://" + _raw_url.split("://", 1)[1]
elif _raw_url and not _raw_url.startswith("http"):
    TURSO_URL = "https://" + _raw_url
else:
    TURSO_URL = _raw_url

TURSO_URL = TURSO_URL.strip()

print(f"[DB] TURSO_URL={TURSO_URL!r}  token_len={len(TURSO_TOKEN)}  token_ok={bool(TURSO_TOKEN)}", flush=True)
if not TURSO_URL:   print("[DB] WARNING: TURSO_URL is not set.",   flush=True)
if not TURSO_TOKEN: print("[DB] WARNING: TURSO_TOKEN is not set.", flush=True)

ALL_COMMANDS = [
    "kick", "ban", "unban", "timeout", "purge",
    "warn", "warnings", "modlogs", "blacklist",
    "unblacklist", "addword", "removeword", "userinfo",
]


def _sync(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _client():
    return libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)


async def _exec(sql, args=()):
    async with _client() as c:
        return await c.execute(sql, list(args))


async def _exec_many(stmts):
    async with _client() as c:
        return await c.batch([libsql_client.Statement(s, list(a)) for s, a in stmts])


def _rows(rs):
    if rs is None: return []
    return [dict(zip(rs.columns, row)) for row in rs.rows]

def _one(rs):
    rows = _rows(rs)
    return rows[0] if rows else None


# ── init ─────────────────────────────────────────────────────────────────

async def init_db():
    tables = [
        ("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
            moderator_id TEXT NOT NULL, reason TEXT, created_at TEXT DEFAULT (datetime('now')))""", []),
        ("""CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, action TEXT NOT NULL,
            target_id TEXT NOT NULL, moderator_id TEXT NOT NULL, reason TEXT,
            created_at TEXT DEFAULT (datetime('now')))""", []),
        ("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)", []),
        ("""CREATE TABLE IF NOT EXISTS blacklisted_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, word TEXT NOT NULL,
            added_by TEXT, created_at TEXT DEFAULT (datetime('now')), UNIQUE(guild_id, word))""", []),
        ("""CREATE TABLE IF NOT EXISTS blacklisted_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
            reason TEXT, added_by TEXT, created_at TEXT DEFAULT (datetime('now')), UNIQUE(guild_id, user_id))""", []),
        ("""CREATE TABLE IF NOT EXISTS command_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, command_name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1, whitelist_roles TEXT DEFAULT '', blacklist_mods TEXT DEFAULT '',
            whitelist_users TEXT DEFAULT '', blacklist_users TEXT DEFAULT '', UNIQUE(guild_id, command_name))""", []),
        ("""CREATE TABLE IF NOT EXISTS profile_cache (
            user_id TEXT PRIMARY KEY, username TEXT, global_name TEXT, avatar_url TEXT,
            banner_url TEXT, accent_color TEXT, bio TEXT, badges TEXT,
            updated_at TEXT DEFAULT (datetime('now')))""", []),
        ("""CREATE TABLE IF NOT EXISTS reaction_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, message_id TEXT NOT NULL,
            channel_id TEXT NOT NULL, emoji TEXT NOT NULL, role_id TEXT NOT NULL,
            UNIQUE(guild_id, message_id, emoji))""", []),
        ("""CREATE TABLE IF NOT EXISTS autoroles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, role_id TEXT NOT NULL,
            UNIQUE(guild_id, role_id))""", []),
        ("""CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, name TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')), UNIQUE(guild_id, name))""", []),
        ("""CREATE TABLE IF NOT EXISTS triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, phrase TEXT NOT NULL,
            response TEXT NOT NULL, UNIQUE(guild_id, phrase))""", []),
        ("""CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, author_id TEXT NOT NULL,
            content TEXT NOT NULL, upvotes INTEGER DEFAULT 0, downvotes INTEGER DEFAULT 0,
            message_id TEXT, created_at TEXT DEFAULT (datetime('now')))""", []),
    ]
    try:
        await _exec_many(tables)
        print("[DB] init_db() completed successfully.", flush=True)
    except Exception as e:
        print(f"[DB] init_db() FAILED: {type(e).__name__}: {e}", flush=True)
        import traceback; traceback.print_exc()


def init_db_sync():
    _sync(init_db())


# ── warnings ───────────────────────────────────────────────────────────

async def add_warning(guild_id, user_id, moderator_id, reason=None):
    await _exec("INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?,?,?,?)",
                (guild_id, user_id, moderator_id, reason))

async def get_warnings(guild_id, user_id):
    rs = await _exec("SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
                     (guild_id, user_id))
    return _rows(rs)

async def clear_warnings(guild_id, user_id):
    await _exec("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id))

def add_warning_sync(guild_id, user_id, moderator_id, reason=None):
    _sync(add_warning(guild_id, user_id, moderator_id, reason))
def get_warnings_sync(guild_id, user_id):
    return _sync(get_warnings(guild_id, user_id))
def clear_warnings_sync(guild_id, user_id):
    _sync(clear_warnings(guild_id, user_id))


# ── mod logs ────────────────────────────────────────────────────────────

async def log_action(guild_id, action, target_id, moderator_id, reason=None):
    await _exec("INSERT INTO mod_logs (guild_id, action, target_id, moderator_id, reason) VALUES (?,?,?,?,?)",
                (guild_id, action, target_id, moderator_id, reason))

async def recent_logs(guild_id, limit=20):
    rs = await _exec("SELECT * FROM mod_logs WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
                     (guild_id, limit))
    return _rows(rs)

async def logs_for_user(guild_id, user_id, limit=50):
    rs = await _exec("SELECT * FROM mod_logs WHERE guild_id=? AND target_id=? ORDER BY created_at DESC LIMIT ?",
                     (guild_id, user_id, limit))
    return _rows(rs)

async def log_stats(guild_id):
    rs = await _exec("SELECT action, COUNT(*) as cnt FROM mod_logs WHERE guild_id=? GROUP BY action", (guild_id,))
    counts = {r["action"]: r["cnt"] for r in _rows(rs)}
    total = sum(counts.values())
    return {"total": total, "kicks": counts.get("kick",0), "bans": counts.get("ban",0),
            "timeouts": counts.get("timeout",0), "warnings": counts.get("warn",0), "purges": counts.get("purge",0)}

def recent_logs_sync(guild_id, limit=20):      return _sync(recent_logs(guild_id, limit))
def logs_for_user_sync(guild_id, uid, lim=50): return _sync(logs_for_user(guild_id, uid, lim))
def log_stats_sync(guild_id):                  return _sync(log_stats(guild_id))


# ── settings ─────────────────────────────────────────────────────────────

async def get_setting(key, default=None):
    rs = await _exec("SELECT value FROM settings WHERE key=?", (key,))
    row = _one(rs)
    return row["value"] if row else default

async def set_setting(key, value):
    await _exec("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

def get_setting_sync(key, default=None): return _sync(get_setting(key, default))
def set_setting_sync(key, value):        _sync(set_setting(key, value))


# ── blacklisted words ─────────────────────────────────────────────────────

async def get_blacklisted_words(guild_id):
    rs = await _exec("SELECT * FROM blacklisted_words WHERE guild_id=? ORDER BY created_at DESC", (guild_id,))
    return _rows(rs)

async def get_blacklisted_words_list(guild_id):
    rs = await _exec("SELECT word FROM blacklisted_words WHERE guild_id=?", (guild_id,))
    return [r["word"] for r in _rows(rs)]

async def add_blacklisted_word(guild_id, word, added_by=None):
    await _exec("INSERT OR IGNORE INTO blacklisted_words (guild_id, word, added_by) VALUES (?,?,?)",
                (guild_id, word.lower().strip(), added_by))

async def remove_blacklisted_word(guild_id, word):
    await _exec("DELETE FROM blacklisted_words WHERE guild_id=? AND word=?", (guild_id, word.lower().strip()))

def get_blacklisted_words_sync(guild_id):               return _sync(get_blacklisted_words(guild_id))
def add_blacklisted_word_sync(guild_id, word, by=None):  _sync(add_blacklisted_word(guild_id, word, by))
def remove_blacklisted_word_sync(guild_id, word):        _sync(remove_blacklisted_word(guild_id, word))


# ── blacklisted users ─────────────────────────────────────────────────────

async def get_blacklisted_users(guild_id):
    rs = await _exec("SELECT * FROM blacklisted_users WHERE guild_id=? ORDER BY created_at DESC", (guild_id,))
    return _rows(rs)

async def add_blacklisted_user(guild_id, user_id, reason=None, added_by=None):
    await _exec("INSERT OR IGNORE INTO blacklisted_users (guild_id, user_id, reason, added_by) VALUES (?,?,?,?)",
                (guild_id, user_id, reason, added_by))

async def remove_blacklisted_user(guild_id, user_id):
    await _exec("DELETE FROM blacklisted_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))

async def is_user_blacklisted(guild_id, user_id):
    rs = await _exec("SELECT 1 FROM blacklisted_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return bool(_rows(rs))

def get_blacklisted_users_sync(guild_id):                           return _sync(get_blacklisted_users(guild_id))
def add_blacklisted_user_sync(guild_id, uid, reason=None, by=None):  _sync(add_blacklisted_user(guild_id, uid, reason, by))
def remove_blacklisted_user_sync(guild_id, uid):                     _sync(remove_blacklisted_user(guild_id, uid))
def is_user_blacklisted_sync(guild_id, user_id):                     return _sync(is_user_blacklisted(guild_id, user_id))


# ── command settings ──────────────────────────────────────────────────────

_CMD_DEFAULT = {"enabled": 1, "whitelist_roles": "", "blacklist_mods": "", "whitelist_users": "", "blacklist_users": ""}

async def get_command_setting(guild_id, command_name):
    rs = await _exec("SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
                     (guild_id, command_name))
    row = _one(rs)
    return row if row else dict(_CMD_DEFAULT)

async def set_command_setting(guild_id, command_name, **kwargs):
    await _exec(
        "INSERT OR IGNORE INTO command_settings (guild_id, command_name) VALUES (?,?)",
        (guild_id, command_name)
    )
    for k, v in kwargs.items():
        await _exec(
            f"UPDATE command_settings SET {k}=? WHERE guild_id=? AND command_name=?",
            (v, guild_id, command_name)
        )

async def get_all_command_settings(guild_id):
    rs = await _exec("SELECT * FROM command_settings WHERE guild_id=?", (guild_id,))
    rows = _rows(rs)
    result = {}
    for cmd in ALL_COMMANDS:
        match = next((r for r in rows if r["command_name"] == cmd), None)
        result[cmd] = match if match else dict(_CMD_DEFAULT)
    return result

def get_command_setting_sync(guild_id, cmd):       return _sync(get_command_setting(guild_id, cmd))
def set_command_setting_sync(guild_id, cmd, **kw):  _sync(set_command_setting(guild_id, cmd, **kw))
def get_command_settings_sync(guild_id):            return _sync(get_all_command_settings(guild_id))


# ── profile cache ─────────────────────────────────────────────────────────

async def cache_profile(user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges):
    await _exec(
        """INSERT OR REPLACE INTO profile_cache
           (user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges, updated_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        (user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges)
    )

async def get_cached_profile(user_id):
    rs = await _exec("SELECT * FROM profile_cache WHERE user_id=?", (user_id,))
    return _one(rs)

def get_cached_profile_sync(user_id): return _sync(get_cached_profile(user_id))


# ── reaction roles ────────────────────────────────────────────────────────

async def get_reaction_roles(guild_id):
    rs = await _exec("SELECT * FROM reaction_roles WHERE guild_id=?", (guild_id,))
    return _rows(rs)

async def add_reaction_role(guild_id, message_id, channel_id, emoji, role_id):
    await _exec(
        "INSERT OR IGNORE INTO reaction_roles (guild_id, message_id, channel_id, emoji, role_id) VALUES (?,?,?,?,?)",
        (guild_id, message_id, channel_id, emoji, role_id)
    )

async def remove_reaction_role(rr_id):
    await _exec("DELETE FROM reaction_roles WHERE id=?", (rr_id,))

async def get_reaction_role_by_emoji(guild_id, message_id, emoji):
    rs = await _exec(
        "SELECT * FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
        (guild_id, message_id, emoji)
    )
    return _one(rs)

def get_reaction_roles_sync(guild_id):                           return _sync(get_reaction_roles(guild_id))
def add_reaction_role_sync(guild_id, mid, cid, emoji, role_id):  _sync(add_reaction_role(guild_id, mid, cid, emoji, role_id))
def remove_reaction_role_sync(rr_id):                            _sync(remove_reaction_role(rr_id))


# ── autoroles ─────────────────────────────────────────────────────────────

async def get_autoroles(guild_id):
    rs = await _exec("SELECT * FROM autoroles WHERE guild_id=?", (guild_id,))
    return _rows(rs)

async def add_autorole(guild_id, role_id):
    await _exec("INSERT OR IGNORE INTO autoroles (guild_id, role_id) VALUES (?,?)", (guild_id, role_id))

async def remove_autorole(guild_id, role_id):
    await _exec("DELETE FROM autoroles WHERE guild_id=? AND role_id=?", (guild_id, role_id))

def get_autoroles_sync(guild_id):             return _sync(get_autoroles(guild_id))
def add_autorole_sync(guild_id, role_id):     _sync(add_autorole(guild_id, role_id))
def remove_autorole_sync(guild_id, role_id):  _sync(remove_autorole(guild_id, role_id))


# ── tags ──────────────────────────────────────────────────────────────────

async def get_tags(guild_id):
    rs = await _exec("SELECT * FROM tags WHERE guild_id=? ORDER BY name", (guild_id,))
    return _rows(rs)

async def add_tag(guild_id, name, content):
    await _exec("INSERT OR REPLACE INTO tags (guild_id, name, content) VALUES (?,?,?)",
                (guild_id, name.lower().strip(), content))

async def remove_tag(guild_id, name):
    await _exec("DELETE FROM tags WHERE guild_id=? AND name=?", (guild_id, name.lower().strip()))

async def get_tag(guild_id, name):
    rs = await _exec("SELECT * FROM tags WHERE guild_id=? AND name=?", (guild_id, name.lower().strip()))
    return _one(rs)

def get_tags_sync(guild_id):                 return _sync(get_tags(guild_id))
def add_tag_sync(guild_id, name, content):   _sync(add_tag(guild_id, name, content))
def remove_tag_sync(guild_id, name):         _sync(remove_tag(guild_id, name))


# ── triggers ──────────────────────────────────────────────────────────────

async def get_all_triggers(guild_id):
    rs = await _exec("SELECT * FROM triggers WHERE guild_id=?", (guild_id,))
    return _rows(rs)

async def add_trigger(guild_id, phrase, response):
    await _exec("INSERT OR REPLACE INTO triggers (guild_id, phrase, response) VALUES (?,?,?)",
                (guild_id, phrase.lower().strip(), response))

async def remove_trigger(trigger_id):
    await _exec("DELETE FROM triggers WHERE id=?", (trigger_id,))

def get_all_triggers_sync(guild_id):               return _sync(get_all_triggers(guild_id))
def get_triggers_sync(guild_id):                   return _sync(get_all_triggers(guild_id))  # alias
def add_trigger_sync(guild_id, phrase, response):  _sync(add_trigger(guild_id, phrase, response))
def remove_trigger_sync(trigger_id):               _sync(remove_trigger(trigger_id))


# ── suggestions ───────────────────────────────────────────────────────────

async def get_suggestions(guild_id, limit=50):
    rs = await _exec("SELECT * FROM suggestions WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
                     (guild_id, limit))
    return _rows(rs)

async def add_suggestion(guild_id, author_id, content, message_id=None):
    await _exec(
        "INSERT INTO suggestions (guild_id, author_id, content, message_id) VALUES (?,?,?,?)",
        (guild_id, author_id, content, message_id)
    )

async def vote_suggestion(suggestion_id, upvote: bool):
    col = "upvotes" if upvote else "downvotes"
    await _exec(f"UPDATE suggestions SET {col}={col}+1 WHERE id=?", (suggestion_id,))

def get_suggestions_sync(guild_id):                               return _sync(get_suggestions(guild_id))
def add_suggestion_sync(guild_id, author_id, content, mid=None):  _sync(add_suggestion(guild_id, author_id, content, mid))
