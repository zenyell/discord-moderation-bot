import os
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


def _sync(coro):
    """Run async coroutine from sync Flask routes (no running loop)."""
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
    """Called from Flask/dashboard startup only."""
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

# sync wrappers for Flask
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

# sync wrappers for Flask
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

# sync wrappers for Flask
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

# sync wrappers for Flask
def get_blacklisted_words_sync(guild_id):              return _sync(get_blacklisted_words(guild_id))
def get_blacklisted_words_list_sync(guild_id):         return _sync(get_blacklisted_words_list(guild_id))
def add_blacklisted_word_sync(gid, word, by=None):     _sync(add_blacklisted_word(gid, word, by))
def remove_blacklisted_word_sync(gid, word):           _sync(remove_blacklisted_word(gid, word))


# ── blacklisted users ─────────────────────────────────────────────────────

async def get_blacklisted_users(guild_id):
    rs = await _exec("SELECT * FROM blacklisted_users WHERE guild_id=? ORDER BY created_at DESC", (guild_id,))
    return _rows(rs)

async def is_user_blacklisted(guild_id, user_id):
    rs = await _exec("SELECT 1 FROM blacklisted_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return bool(_one(rs))

async def add_blacklisted_user(guild_id, user_id, reason=None, added_by=None):
    await _exec("INSERT OR IGNORE INTO blacklisted_users (guild_id, user_id, reason, added_by) VALUES (?,?,?,?)",
                (guild_id, user_id, reason, added_by))

async def remove_blacklisted_user(guild_id, user_id):
    await _exec("DELETE FROM blacklisted_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))

# sync wrappers for Flask
def get_blacklisted_users_sync(gid):               return _sync(get_blacklisted_users(gid))
def is_user_blacklisted_sync(gid, uid):            return _sync(is_user_blacklisted(gid, uid))
def add_blacklisted_user_sync(gid, uid, r=None, by=None): _sync(add_blacklisted_user(gid, uid, r, by))
def remove_blacklisted_user_sync(gid, uid):        _sync(remove_blacklisted_user(gid, uid))


# ── command settings ─────────────────────────────────────────────────────

ALL_COMMANDS = [
    "kick", "ban", "unban", "timeout", "purge", "warn", "warnings", "modlogs",
    "blacklist", "unblacklist", "addword", "removeword", "userinfo"
]
_POLICY_DEFAULTS = {"enabled": 1, "whitelist_roles": "", "blacklist_mods": "",
                    "whitelist_users": "", "blacklist_users": ""}

async def get_command_settings(guild_id):
    rs = await _exec("SELECT * FROM command_settings WHERE guild_id=?", (guild_id,))
    result = {cmd: dict(_POLICY_DEFAULTS) for cmd in ALL_COMMANDS}
    for r in _rows(rs):
        result[r["command_name"]] = {
            "enabled": r["enabled"],
            "whitelist_roles": r.get("whitelist_roles") or "",
            "blacklist_mods":  r.get("blacklist_mods")  or "",
            "whitelist_users": r.get("whitelist_users") or "",
            "blacklist_users": r.get("blacklist_users") or "",
        }
    return result

async def get_command_setting(guild_id, command_name):
    rs = await _exec("SELECT * FROM command_settings WHERE guild_id=? AND command_name=?",
                     (guild_id, command_name))
    row = _one(rs)
    if not row: return dict(_POLICY_DEFAULTS)
    return {"enabled": row["enabled"],
            "whitelist_roles": row.get("whitelist_roles") or "",
            "blacklist_mods":  row.get("blacklist_mods")  or "",
            "whitelist_users": row.get("whitelist_users") or "",
            "blacklist_users": row.get("blacklist_users") or ""}

async def set_command_setting(guild_id, command_name, enabled=None, whitelist_roles=None,
                              blacklist_mods=None, whitelist_users=None, blacklist_users=None):
    existing = await get_command_setting(guild_id, command_name)
    if existing == _POLICY_DEFAULTS:
        await _exec("""INSERT INTO command_settings
                       (guild_id,command_name,enabled,whitelist_roles,blacklist_mods,whitelist_users,blacklist_users)
                       VALUES (?,?,?,?,?,?,?)""",
                    (guild_id, command_name,
                     enabled         if enabled         is not None else 1,
                     whitelist_roles if whitelist_roles is not None else "",
                     blacklist_mods  if blacklist_mods  is not None else "",
                     whitelist_users if whitelist_users is not None else "",
                     blacklist_users if blacklist_users is not None else ""))
    else:
        fields, vals = [], []
        for f, v in [("enabled", enabled), ("whitelist_roles", whitelist_roles),
                     ("blacklist_mods", blacklist_mods), ("whitelist_users", whitelist_users),
                     ("blacklist_users", blacklist_users)]:
            if v is not None:
                fields.append(f"{f}=?"); vals.append(v)
        if fields:
            vals += [guild_id, command_name]
            await _exec(f"UPDATE command_settings SET {', '.join(fields)} WHERE guild_id=? AND command_name=?", vals)

# sync wrappers for Flask
def get_command_settings_sync(gid):       return _sync(get_command_settings(gid))
def get_command_setting_sync(gid, cmd):   return _sync(get_command_setting(gid, cmd))
def set_command_setting_sync(gid, cmd, **kw): _sync(set_command_setting(gid, cmd, **kw))


# ── profile cache ────────────────────────────────────────────────────────

async def cache_profile(user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges):
    await _exec("""INSERT OR REPLACE INTO profile_cache
                   (user_id,username,global_name,avatar_url,banner_url,accent_color,bio,badges,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
                (user_id, username, global_name, avatar_url, banner_url, accent_color, bio, badges))

async def get_cached_profile(user_id):
    rs = await _exec("SELECT * FROM profile_cache WHERE user_id=?", (user_id,))
    return _one(rs)

# sync wrappers for Flask
def get_cached_profile_sync(uid): return _sync(get_cached_profile(uid))


# ── reaction roles ───────────────────────────────────────────────────────

async def add_reaction_role(guild_id, message_id, channel_id, emoji, role_id):
    await _exec("INSERT OR IGNORE INTO reaction_roles (guild_id,message_id,channel_id,emoji,role_id) VALUES (?,?,?,?,?)",
                (guild_id, message_id, channel_id, emoji, role_id))

async def remove_reaction_role(rr_id):
    await _exec("DELETE FROM reaction_roles WHERE id=?", (rr_id,))

async def get_reaction_roles(guild_id):
    rs = await _exec("SELECT * FROM reaction_roles WHERE guild_id=? ORDER BY id DESC", (guild_id,))
    return _rows(rs)

async def get_reaction_role(guild_id, message_id, emoji):
    rs = await _exec("SELECT * FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
                     (guild_id, message_id, emoji))
    return _one(rs)

# sync wrappers for Flask
def add_reaction_role_sync(gid, mid, cid, em, rid): _sync(add_reaction_role(gid, mid, cid, em, rid))
def remove_reaction_role_sync(rr_id):               _sync(remove_reaction_role(rr_id))
def get_reaction_roles_sync(gid):                   return _sync(get_reaction_roles(gid))


# ── autoroles ────────────────────────────────────────────────────────────

async def add_autorole(guild_id, role_id):
    await _exec("INSERT OR IGNORE INTO autoroles (guild_id, role_id) VALUES (?,?)", (guild_id, role_id))

async def remove_autorole(guild_id, role_id):
    await _exec("DELETE FROM autoroles WHERE guild_id=? AND role_id=?", (guild_id, role_id))

async def get_autoroles(guild_id):
    rs = await _exec("SELECT * FROM autoroles WHERE guild_id=?", (guild_id,))
    return _rows(rs)

# sync wrappers for Flask
def add_autorole_sync(gid, rid):    _sync(add_autorole(gid, rid))
def remove_autorole_sync(gid, rid): _sync(remove_autorole(gid, rid))
def get_autoroles_sync(gid):        return _sync(get_autoroles(gid))


# ── tags ──────────────────────────────────────────────────────────────────

async def add_tag(guild_id, name, content):
    await _exec("INSERT OR REPLACE INTO tags (guild_id, name, content) VALUES (?,?,?)",
                (guild_id, name.lower().strip(), content))

async def remove_tag(guild_id, name):
    await _exec("DELETE FROM tags WHERE guild_id=? AND name=?", (guild_id, name.lower().strip()))

async def get_tags(guild_id):
    rs = await _exec("SELECT * FROM tags WHERE guild_id=? ORDER BY name ASC", (guild_id,))
    return _rows(rs)

async def get_tag(guild_id, name):
    rs = await _exec("SELECT * FROM tags WHERE guild_id=? AND name=?", (guild_id, name.lower().strip()))
    return _one(rs)

# sync wrappers for Flask
def add_tag_sync(gid, name, content): _sync(add_tag(gid, name, content))
def remove_tag_sync(gid, name):       _sync(remove_tag(gid, name))
def get_tags_sync(gid):               return _sync(get_tags(gid))
def get_tag_sync(gid, name):          return _sync(get_tag(gid, name))


# ── triggers ─────────────────────────────────────────────────────────────

async def add_trigger(guild_id, phrase, response):
    await _exec("INSERT OR REPLACE INTO triggers (guild_id, phrase, response) VALUES (?,?,?)",
                (guild_id, phrase.lower().strip(), response))

async def remove_trigger(trigger_id):
    await _exec("DELETE FROM triggers WHERE id=?", (trigger_id,))

async def get_triggers(guild_id):
    rs = await _exec("SELECT * FROM triggers WHERE guild_id=? ORDER BY id DESC", (guild_id,))
    return _rows(rs)

async def get_all_triggers(guild_id):
    rs = await _exec("SELECT phrase, response FROM triggers WHERE guild_id=?", (guild_id,))
    return _rows(rs)

# sync wrappers for Flask
def add_trigger_sync(gid, phrase, resp):  _sync(add_trigger(gid, phrase, resp))
def remove_trigger_sync(tid):             _sync(remove_trigger(tid))
def get_triggers_sync(gid):               return _sync(get_triggers(gid))
def get_all_triggers_sync(gid):           return _sync(get_all_triggers(gid))


# ── suggestions ──────────────────────────────────────────────────────────

async def add_suggestion(guild_id, author_id, content, message_id=None):
    await _exec("INSERT INTO suggestions (guild_id, author_id, content, message_id) VALUES (?,?,?,?)",
                (guild_id, author_id, content, message_id))

async def get_suggestions(guild_id, limit=50):
    rs = await _exec("SELECT * FROM suggestions WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
                     (guild_id, limit))
    return _rows(rs)

async def update_suggestion_votes(message_id, upvotes, downvotes):
    await _exec("UPDATE suggestions SET upvotes=?, downvotes=? WHERE message_id=?",
                (upvotes, downvotes, message_id))

# sync wrappers for Flask
def get_suggestions_sync(gid, limit=50): return _sync(get_suggestions(gid, limit))
