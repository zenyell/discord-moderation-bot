import os
import json
import time
import secrets
import traceback
import urllib.request
import urllib.parse
from pathlib import Path
from functools import wraps
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, abort
)

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

import database as db

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "modpanel-secret-key-change-me")
app.config["PROPAGATE_EXCEPTIONS"] = False

BOT_TOKEN      = os.getenv("DISCORD_BOT_TOKEN", "")
HEARTBEAT_FILE = os.getenv("HEARTBEAT_PATH", "/tmp/bot_heartbeat")

# Discord OAuth2
DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/callback")

# Bot invite URL — uses bot scope + required permissions (Administrator = 8)
BOT_INVITE_URL = (
    f"https://discord.com/oauth2/authorize"
    f"?client_id={DISCORD_CLIENT_ID}"
    f"&permissions=8"
    f"&scope=bot%20applications.commands"
) if DISCORD_CLIENT_ID else "#"

DISCORD_API    = "https://discord.com/api/v10"
DISCORD_OAUTH  = "https://discord.com/api/oauth2"
# identify = basic user info, guilds = list of their servers
DISCORD_SCOPES = "identify guilds"

# Permission bit flags
PERM_ADMINISTRATOR  = 0x8
PERM_MANAGE_GUILD   = 0x20
ADMIN_PERMS         = PERM_ADMINISTRATOR | PERM_MANAGE_GUILD

print(f"[Dashboard] BOT_TOKEN present={bool(BOT_TOKEN)}  CLIENT_ID={DISCORD_CLIENT_ID!r}", flush=True)

db.init_db_sync()

LOG_TOGGLE_KEYS = [
    "log_message_delete", "log_message_edit", "log_bulk_delete",
    "log_member_join",    "log_member_leave",  "log_nickname_change",
    "log_role_change",    "log_ban",           "log_kick",
    "log_mute",           "log_warn",          "log_channel_update",
    "log_role_update",    "log_voice",
]


def _gkey(guild_id, key):
    return f"{guild_id}:{key}"


# ── Context processor ──────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    current_user = {"username": "", "avatar": "", "logged_in": False, "discord_id": ""}
    if session.get("logged_in"):
        current_user = {
            "username":   session.get("username", ""),
            "avatar":     session.get("avatar", ""),
            "logged_in":  True,
            "discord_id": session.get("discord_id", ""),
        }
    active_guild = session.get("active_guild")  # {id, name, icon}
    return {
        "current_user": current_user,
        "active_guild": active_guild,
        "bot_invite_url": BOT_INVITE_URL,
    }


# ── Error handlers ─────────────────────────────────────────────────────────

@app.errorhandler(500)
def internal_error(e):
    tb = traceback.format_exc()
    return (f"""<!DOCTYPE html><html><head><title>500</title>
<style>body{{font-family:monospace;background:#1e2124;color:#dcddde;padding:32px}}
pre{{background:#2f3136;border:1px solid rgba(255,255,255,.08);border-radius:8px;
padding:20px;overflow-x:auto;white-space:pre-wrap;font-size:13px;line-height:1.6}}
h2{{color:#ed4245;margin-bottom:16px}}p{{color:#72767d;margin-bottom:12px;font-size:13px}}
</style></head><body><h2>&#x26A0; 500 Internal Server Error</h2><p>Traceback:</p>
<pre>{tb}</pre></body></html>""", 500)


@app.errorhandler(Exception)
def unhandled_exception(e):
    tb = traceback.format_exc()
    return (f"""<!DOCTYPE html><html><head><title>Error</title>
<style>body{{font-family:monospace;background:#1e2124;color:#dcddde;padding:32px}}
pre{{background:#2f3136;border:1px solid rgba(255,255,255,.08);border-radius:8px;
padding:20px;overflow-x:auto;white-space:pre-wrap;font-size:13px;line-height:1.6}}
h2{{color:#ed4245;margin-bottom:16px}}p{{color:#72767d;margin-bottom:12px;font-size:13px}}
</style></head><body><h2>&#x26A0; {type(e).__name__}</h2><p>Traceback:</p>
<pre>{tb}</pre></body></html>""", 500)


# ── Discord Bot API helpers ────────────────────────────────────────────────

def _bot_headers():
    return {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}


def _bot_guilds() -> set:
    """Return set of guild IDs the bot is currently in."""
    if not BOT_TOKEN:
        return set()
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds",
            headers=_bot_headers()
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {g["id"] for g in json.loads(resp.read())}
    except Exception:
        return set()


def _fetch_discord_user(user_id: str) -> tuple[dict, str]:
    if not BOT_TOKEN:
        return {}, "DISCORD_BOT_TOKEN not set"
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/{user_id}", headers=_bot_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return {}, f"Discord API HTTP {e.code}: {body}"
    except Exception as e:
        return {}, f"{type(e).__name__}: {e}"


def _fetch_guild_member(guild_id: str, user_id: str) -> dict:
    if not BOT_TOKEN:
        return {}
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}",
            headers=_bot_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _fetch_guild_roles(guild_id: str) -> list:
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/roles", headers=_bot_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def _fetch_guild_member_count(guild_id: str) -> int:
    if not BOT_TOKEN:
        return 0
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}?with_counts=true",
            headers=_bot_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("approximate_member_count") or data.get("member_count") or 0
    except Exception:
        return 0


def _search_guild_members(guild_id: str, query: str) -> list:
    if not BOT_TOKEN:
        return []
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/members/search?query={encoded}&limit=10",
            headers=_bot_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def _avatar_url(user_data: dict, member_data: dict, guild_id: str = "") -> str:
    uid = user_data.get("id", "")
    if guild_id:
        guild_av = member_data.get("avatar", "")
        if guild_av:
            ext = "gif" if guild_av.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/guilds/{guild_id}/users/{uid}/avatars/{guild_av}.{ext}?size=256"
    av = user_data.get("avatar", "")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{av}.{ext}?size=256"
    disc = user_data.get("discriminator", "0")
    idx  = (int(disc) % 5) if disc and disc != "0" else ((int(uid) >> 22) % 6) if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _banner_url(user_data: dict, member_data: dict, guild_id: str = "") -> str:
    uid = user_data.get("id", "")
    if guild_id:
        guild_banner = member_data.get("banner", "") or ""
        if guild_banner:
            ext = "gif" if guild_banner.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/guilds/{guild_id}/users/{uid}/banners/{guild_banner}.{ext}?size=1024"
    banner = user_data.get("banner", "") or ""
    if banner:
        ext = "gif" if banner.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/banners/{uid}/{banner}.{ext}?size=1024"
    return ""


def _accent_hex(user_data: dict) -> str:
    color = user_data.get("accent_color")
    return f"#{color:06x}" if color else "#5865F2"


def _bio(user_data: dict) -> str:
    return user_data.get("bio") or ""


BADGE_MAP = {
    1:       ("Discord Staff",         "https://cdn.discordapp.com/badge-icons/5e74e9b61934fc1f67c65515d1f7e60d.png"),
    2:       ("Partnered Server Owner", "https://cdn.discordapp.com/badge-icons/3f9748e53446a137a052f3454e2de41e.png"),
    4:       ("HypeSquad Events",       "https://cdn.discordapp.com/badge-icons/bf01d1073931f921909045f3a39fd264.png"),
    8:       ("Bug Hunter Level 1",     "https://cdn.discordapp.com/badge-icons/2717692c7dca7289b35297368a940dd0.png"),
    64:      ("HypeSquad Bravery",      "https://cdn.discordapp.com/badge-icons/8a88d63823d8a71cd5e390baa45efa02.png"),
    128:     ("HypeSquad Brilliance",   "https://cdn.discordapp.com/badge-icons/011940fd013082d99d0e62f73b7f08d6.png"),
    256:     ("HypeSquad Balance",      "https://cdn.discordapp.com/badge-icons/3aa41de486fa12454c3761e8e223442e.png"),
    512:     ("Early Supporter",        "https://cdn.discordapp.com/badge-icons/7060786766c9c840eb3019e725d2b358.png"),
    16384:   ("Bug Hunter Level 2",     "https://cdn.discordapp.com/badge-icons/848f79194d4be5ff5f81505cbd0ce1e6.png"),
    131072:  ("Verified Bot Developer", "https://cdn.discordapp.com/badge-icons/6df5892e0f35b051f8b61eace34f4967.png"),
    4194304: ("Active Developer",       "https://cdn.discordapp.com/badge-icons/6bdc42827a38498929a4920da12695d9.png"),
}


def _get_badges(public_flags: int) -> list:
    return [(name, icon) for bit, (name, icon) in BADGE_MAP.items() if public_flags & bit]


# ── Auth decorators ────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def guild_required(f):
    """Ensure user has selected a guild and still has admin perms in it."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        # Accept guild_id from URL kwarg, query param, or session
        guild_id = (
            kwargs.get("guild_id")
            or request.args.get("guild_id")
            or session.get("active_guild", {}).get("id")
        )
        if not guild_id:
            return redirect(url_for("servers"))
        # Validate user still has rights in this guild
        user_guilds = session.get("user_guilds", {})
        guild_info  = user_guilds.get(guild_id)
        if not guild_info:
            flash("You no longer have access to that server.")
            return redirect(url_for("servers"))
        perms = int(guild_info.get("permissions", 0))
        if not (perms & ADMIN_PERMS):
            flash("You need Administrator or Manage Server permission.")
            return redirect(url_for("servers"))
        # Inject guild_id into kwargs if the route expects it
        if "guild_id" in f.__code__.co_varnames:
            kwargs["guild_id"] = guild_id
        # Store as active guild in session
        session["active_guild"] = {
            "id":   guild_id,
            "name": guild_info.get("name", guild_id),
            "icon": guild_info.get("icon", ""),
        }
        return f(*args, **kwargs)
    return decorated


# ── OAuth2 helpers ─────────────────────────────────────────────────────────

def _exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  DISCORD_REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        f"{DISCORD_OAUTH}/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_oauth_user(access_token: str) -> dict:
    req = urllib.request.Request(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_oauth_guilds(access_token: str) -> list:
    """Fetch all guilds the user is in via their OAuth token."""
    req = urllib.request.Request(
        f"{DISCORD_API}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _build_oauth_url(state: str) -> str:
    params = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope":         DISCORD_SCOPES,
        "state":         state,
        "prompt":        "none",
    })
    return f"https://discord.com/oauth2/authorize?{params}"


def _guild_icon_url(guild: dict) -> str:
    gid  = guild.get("id", "")
    icon = guild.get("icon", "")
    if icon:
        ext = "gif" if icon.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/icons/{gid}/{icon}.{ext}?size=128"
    return ""


# ── Core routes ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def landing():
    if session.get("logged_in"):
        return redirect(url_for("servers"))
    return render_template("landing.html")


@app.route("/login")
def login():
    if session.get("logged_in"):
        return redirect(url_for("servers"))
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return render_template("login.html", error=
            "Discord OAuth is not configured. Set DISCORD_CLIENT_ID, "
            "DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI in .env.",
            oauth_url=None)
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    return render_template("login.html", error=None, oauth_url=_build_oauth_url(state))


@app.route("/discord_login")
def discord_login():
    return redirect(url_for("login"))


@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        flash(f"Discord denied access: {error}")
        return redirect(url_for("login"))

    code  = request.args.get("code", "")
    state = request.args.get("state", "")

    if not code:
        flash("No authorisation code received from Discord.")
        return redirect(url_for("login"))

    if state != session.pop("oauth_state", None):
        flash("Invalid OAuth state — possible CSRF. Please try again.")
        return redirect(url_for("login"))

    try:
        token_data = _exchange_code(code)
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        flash(f"Failed to exchange code: HTTP {e.code} — {body}")
        return redirect(url_for("login"))
    except Exception as e:
        flash(f"Failed to exchange code: {e}")
        return redirect(url_for("login"))

    access_token = token_data.get("access_token", "")
    if not access_token:
        flash("Discord did not return an access token.")
        return redirect(url_for("login"))

    # Fetch user profile
    try:
        user = _fetch_oauth_user(access_token)
    except Exception as e:
        flash(f"Failed to fetch user: {e}")
        return redirect(url_for("login"))

    # Fetch user's guilds
    try:
        raw_guilds = _fetch_oauth_guilds(access_token)
    except Exception:
        raw_guilds = []

    user_id  = user.get("id", "")
    username = user.get("global_name") or user.get("username", "Unknown")
    av_hash  = user.get("avatar", "")
    avatar   = ""
    if av_hash and user_id:
        ext    = "gif" if av_hash.startswith("a_") else "png"
        avatar = f"https://cdn.discordapp.com/avatars/{user_id}/{av_hash}.{ext}?size=256"

    # Build dict of guilds where user has admin/manage perms
    # keyed by guild_id for fast lookup
    admin_guilds = {}
    for g in raw_guilds:
        perms = int(g.get("permissions", 0))
        if perms & ADMIN_PERMS:
            gid = g["id"]
            admin_guilds[gid] = {
                "id":          gid,
                "name":        g.get("name", gid),
                "icon":        g.get("icon", ""),
                "icon_url":    _guild_icon_url(g),
                "permissions": perms,
                "owner":       g.get("owner", False),
            }

    session["logged_in"]  = True
    session["username"]   = username
    session["avatar"]     = avatar
    session["discord_id"] = user_id
    session["user_guilds"] = admin_guilds  # only guilds with admin perms
    session.pop("active_guild", None)      # force fresh guild selection
    return redirect(url_for("servers"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# ── Server picker ──────────────────────────────────────────────────────────

@app.route("/servers")
@login_required
def servers():
    """Show all servers the user has admin perms in + whether the bot is in them."""
    user_guilds = session.get("user_guilds", {})
    bot_guild_ids = _bot_guilds()

    guilds_with_bot    = []
    guilds_without_bot = []

    for gid, g in user_guilds.items():
        entry = dict(g)
        entry["has_bot"] = gid in bot_guild_ids
        if entry["has_bot"]:
            guilds_with_bot.append(entry)
        else:
            guilds_without_bot.append(entry)

    guilds_with_bot.sort(key=lambda x: x["name"].lower())
    guilds_without_bot.sort(key=lambda x: x["name"].lower())

    return render_template(
        "servers.html",
        guilds_with_bot=guilds_with_bot,
        guilds_without_bot=guilds_without_bot,
        bot_invite_url=BOT_INVITE_URL,
    )


@app.route("/servers/select/<guild_id>")
@login_required
def select_guild(guild_id):
    """Set the active guild and redirect to its dashboard."""
    user_guilds = session.get("user_guilds", {})
    if guild_id not in user_guilds:
        flash("You don't have access to that server.")
        return redirect(url_for("servers"))
    perms = int(user_guilds[guild_id].get("permissions", 0))
    if not (perms & ADMIN_PERMS):
        flash("You need Administrator or Manage Server permission.")
        return redirect(url_for("servers"))
    session["active_guild"] = {
        "id":   guild_id,
        "name": user_guilds[guild_id].get("name", guild_id),
        "icon": user_guilds[guild_id].get("icon", ""),
    }
    return redirect(url_for("index"))


# ── Dashboard (per-guild) ──────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def index():
    guild = session.get("active_guild")
    if not guild:
        return redirect(url_for("servers"))
    guild_id = guild["id"]

    # Re-validate perms
    user_guilds = session.get("user_guilds", {})
    if guild_id not in user_guilds:
        return redirect(url_for("servers"))
    perms = int(user_guilds[guild_id].get("permissions", 0))
    if not (perms & ADMIN_PERMS):
        flash("You need Administrator or Manage Server permission.")
        return redirect(url_for("servers"))

    try:
        stats = db.log_stats_sync(guild_id)
    except Exception:
        stats = {"total": 0, "kicks": 0, "bans": 0, "timeouts": 0, "warnings": 0, "purges": 0}
    try:
        logs = db.recent_logs_sync(guild_id, 15)
    except Exception:
        logs = []
    total_members = _fetch_guild_member_count(guild_id)
    return render_template("dashboard.html", stats=stats, logs=logs,
                           total_members=total_members, guild_id=guild_id)


@app.route("/api/status")
@login_required
def api_status():
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            parts = f.read().strip().split("|")
        ts   = float(parts[0])
        name = parts[1] if len(parts) > 1 else "Bot"
        age  = time.time() - ts
        if age < 60:
            return jsonify({"online": True, "name": name, "age": round(age)})
        return jsonify({"online": False, "reason": f"heartbeat stale ({round(age)}s ago)"})
    except FileNotFoundError:
        return jsonify({"online": False, "reason": "heartbeat file not found"})
    except Exception as e:
        return jsonify({"online": False, "reason": str(e)})


# ── Helper: get active guild_id or abort ──────────────────────────────────

def _active_guild_id():
    g = session.get("active_guild")
    if not g:
        return None
    return g["id"]


def _require_guild():
    gid = _active_guild_id()
    if not gid:
        return redirect(url_for("servers"))
    return gid


# ── Settings ───────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "auto_role_id"), request.form.get("auto_role_id", ""))
        db.set_setting_sync(_gkey(guild_id, "bad_words"),    request.form.get("bad_words", ""))
        db.set_setting_sync(_gkey(guild_id, "spam_limit"),   request.form.get("spam_limit", ""))
        db.set_setting_sync(_gkey(guild_id, "spam_window"),  request.form.get("spam_window", ""))
        db.set_setting_sync(_gkey(guild_id, "spam_timeout"), request.form.get("spam_timeout", ""))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id": db.get_setting_sync(_gkey(guild_id, "auto_role_id"), ""),
        "bad_words":    db.get_setting_sync(_gkey(guild_id, "bad_words"),    os.getenv("BAD_WORDS", "")),
        "spam_limit":   db.get_setting_sync(_gkey(guild_id, "spam_limit"),   os.getenv("SPAM_MESSAGE_LIMIT", "6")),
        "spam_window":  db.get_setting_sync(_gkey(guild_id, "spam_window"),  os.getenv("SPAM_WINDOW_SECONDS", "8")),
        "spam_timeout": db.get_setting_sync(_gkey(guild_id, "spam_timeout"), os.getenv("SPAM_TIMEOUT_MINUTES", "5")),
        "client_id":    DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }
    return render_template("settings.html", cfg=cfg)


# ── Moderation ─────────────────────────────────────────────────────────────

@app.route("/moderation", methods=["GET", "POST"])
@login_required
def moderation():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_word":
            word = request.form.get("word", "").strip()
            if word:
                db.add_blacklisted_word_sync(guild_id, word, "dashboard")
                flash(f"Word '{word}' added.")
        elif action == "remove_word":
            db.remove_blacklisted_word_sync(guild_id, request.form.get("word", ""))
            flash("Word removed.")
        elif action == "add_user":
            uid    = request.form.get("user_id", "").strip()
            reason = request.form.get("reason", "").strip()
            if uid:
                db.add_blacklisted_user_sync(guild_id, uid, reason, "dashboard")
                flash(f"User {uid} blacklisted.")
        elif action == "remove_user":
            uid = request.form.get("user_id", "").strip()
            db.remove_blacklisted_user_sync(guild_id, uid)
            flash(f"User {uid} removed.")
        return redirect(url_for("moderation"))
    words = db.get_blacklisted_words_sync(guild_id)
    users = db.get_blacklisted_users_sync(guild_id)
    return render_template("moderation.html", words=words, users=users)


# ── Logging ────────────────────────────────────────────────────────────────

@app.route("/logging", methods=["GET", "POST"])
@login_required
def logging_page():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "log_channel_id"), request.form.get("log_channel_id", ""))
        for key in LOG_TOGGLE_KEYS:
            val = "1" if request.form.get(key) else "0"
            db.set_setting_sync(_gkey(guild_id, key), val)
        flash("Log settings saved.")
        return redirect(url_for("logging_page"))
    cfg = {"log_channel_id": db.get_setting_sync(_gkey(guild_id, "log_channel_id"), "")}
    for key in LOG_TOGGLE_KEYS:
        raw = db.get_setting_sync(_gkey(guild_id, key), None)
        cfg[key] = (raw == "1") if raw is not None else True
    return render_template("logging.html", cfg=cfg)


# ── Reaction Roles ─────────────────────────────────────────────────────────

@app.route("/reaction-roles", methods=["GET", "POST"])
@login_required
def reaction_roles():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            db.add_reaction_role_sync(
                guild_id,
                request.form.get("message_id", "").strip(),
                request.form.get("channel_id", "").strip(),
                request.form.get("emoji", "").strip(),
                request.form.get("role_id", "").strip(),
            )
            flash("Reaction role added.")
        elif action == "remove":
            db.remove_reaction_role_sync(request.form.get("rr_id"))
            flash("Reaction role removed.")
        return redirect(url_for("reaction_roles"))
    return render_template("reaction_roles.html", rr_list=db.get_reaction_roles_sync(guild_id))


# ── Autoroles ──────────────────────────────────────────────────────────────

@app.route("/autoroles", methods=["GET", "POST"])
@login_required
def autoroles():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            role_id = request.form.get("role_id", "").strip()
            if role_id:
                db.add_autorole_sync(guild_id, role_id)
                flash(f"Autorole {role_id} added.")
        elif action == "remove":
            db.remove_autorole_sync(guild_id, request.form.get("role_id", ""))
            flash("Autorole removed.")
        return redirect(url_for("autoroles"))
    return render_template("autoroles.html", autoroles=db.get_autoroles_sync(guild_id))


# ── Tags ───────────────────────────────────────────────────────────────────

@app.route("/tags", methods=["GET", "POST"])
@login_required
def tags():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name    = request.form.get("tag_name", "").strip()
            content = request.form.get("tag_content", "").strip()
            if name and content:
                db.add_tag_sync(guild_id, name, content)
                flash(f"Tag '{name}' saved.")
        elif action == "remove":
            db.remove_tag_sync(guild_id, request.form.get("tag_name", ""))
            flash("Tag deleted.")
        return redirect(url_for("tags"))
    return render_template("tags.html", tags=db.get_tags_sync(guild_id))


# ── Triggers ───────────────────────────────────────────────────────────────

@app.route("/triggers", methods=["GET", "POST"])
@login_required
def triggers():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            phrase   = request.form.get("trigger_phrase", "").strip()
            response = request.form.get("trigger_response", "").strip()
            if phrase and response:
                db.add_trigger_sync(guild_id, phrase, response)
                flash(f"Trigger '{phrase}' added.")
        elif action == "remove":
            db.remove_trigger_sync(request.form.get("trigger_id"))
            flash("Trigger deleted.")
        return redirect(url_for("triggers"))
    return render_template("triggers.html", triggers=db.get_triggers_sync(guild_id))


# ── Greetings ──────────────────────────────────────────────────────────────

_GREETING_KEYS = [
    "welcome_enabled", "welcome_channel", "welcome_message",
    "farewell_enabled", "farewell_channel", "farewell_message",
]


@app.route("/greetings", methods=["GET", "POST"])
@login_required
def greetings():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        for k in _GREETING_KEYS:
            if k.endswith("_enabled"):
                db.set_setting_sync(_gkey(guild_id, k), "1" if request.form.get(k) else "0")
            else:
                db.set_setting_sync(_gkey(guild_id, k), request.form.get(k, ""))
        flash("Greetings saved.")
        return redirect(url_for("greetings"))
    class Cfg: pass
    cfg = Cfg()
    for k in _GREETING_KEYS:
        setattr(cfg, k, db.get_setting_sync(_gkey(guild_id, k), ""))
    return render_template("greetings.html", cfg=cfg)


# ── Starboard ──────────────────────────────────────────────────────────────

_STARBOARD_KEYS = ["starboard_enabled", "starboard_channel", "starboard_min", "starboard_emoji"]


@app.route("/starboard", methods=["GET", "POST"])
@login_required
def starboard():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        for k in _STARBOARD_KEYS:
            if k == "starboard_enabled":
                db.set_setting_sync(_gkey(guild_id, k), "1" if request.form.get(k) else "0")
            else:
                db.set_setting_sync(_gkey(guild_id, k), request.form.get(k, ""))
        flash("Starboard settings saved.")
        return redirect(url_for("starboard"))
    class Cfg: pass
    cfg = Cfg()
    for k in _STARBOARD_KEYS:
        setattr(cfg, k, db.get_setting_sync(_gkey(guild_id, k), ""))
    return render_template("starboard.html", cfg=cfg)


# ── Suggestions ────────────────────────────────────────────────────────────

@app.route("/suggestions", methods=["GET", "POST"])
@login_required
def suggestions():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "suggestions_enabled"), "1" if request.form.get("suggestions_enabled") else "0")
        db.set_setting_sync(_gkey(guild_id, "suggestions_channel"), request.form.get("suggestions_channel", ""))
        flash("Suggestions settings saved.")
        return redirect(url_for("suggestions"))
    class Cfg: pass
    cfg = Cfg()
    cfg.suggestions_enabled = db.get_setting_sync(_gkey(guild_id, "suggestions_enabled"), "0")
    cfg.suggestions_channel = db.get_setting_sync(_gkey(guild_id, "suggestions_channel"), "")
    return render_template("suggestions.html", cfg=cfg, suggestions=db.get_suggestions_sync(guild_id))


# ── Command settings ───────────────────────────────────────────────────────

@app.route("/command-settings", methods=["GET", "POST"])
@login_required
def command_settings():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        for cmd in db.ALL_COMMANDS:
            enabled         = 1 if request.form.get(f"{cmd}_enabled") == "on" else 0
            whitelist_roles = request.form.get(f"{cmd}_whitelist_roles", "").strip()
            blacklist_mods  = request.form.get(f"{cmd}_blacklist_mods",  "").strip()
            whitelist_users = request.form.get(f"{cmd}_whitelist_users", "").strip()
            blacklist_users = request.form.get(f"{cmd}_blacklist_users", "").strip()
            db.set_command_setting_sync(
                guild_id, cmd,
                enabled=enabled, whitelist_roles=whitelist_roles,
                blacklist_mods=blacklist_mods, whitelist_users=whitelist_users,
                blacklist_users=blacklist_users,
            )
        flash("Command settings saved.")
        return redirect(url_for("command_settings"))
    cmd_settings = db.get_command_settings_sync(guild_id)
    return render_template("command_settings.html", commands=db.ALL_COMMANDS, cmd_settings=cmd_settings)


# ── User lookup ────────────────────────────────────────────────────────────

def _build_lookup_result(guild_id: str, user_id: str) -> tuple[dict | None, str]:
    user_data, err = _fetch_discord_user(user_id)
    if not user_data.get("id"):
        return None, err or "User not found"
    member_data = _fetch_guild_member(guild_id, user_id)
    av = _avatar_url(user_data, member_data, guild_id)
    return {
        "target_id":     user_id,
        "username":      user_data.get("username", user_id),
        "display_name":  user_data.get("global_name") or user_data.get("username", user_id),
        "discriminator": user_data.get("discriminator", "0"),
        "avatar_url":    av,
    }, ""


@app.route("/user-lookup")
@login_required
def user_lookup():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    query     = request.args.get("q", "").strip()
    results   = []
    api_error = ""
    if query:
        seen = set()
        if query.isdigit():
            r, err = _build_lookup_result(guild_id, query)
            if r:
                results.append(r); seen.add(query)
            elif err:
                api_error = err
        if not query.isdigit() or not results:
            try:
                cached = db.search_profile_cache_sync(query)
                for row in cached:
                    uid = row.get("user_id", "")
                    if uid and uid not in seen:
                        av = row.get("avatar_url") or ""
                        if not av:
                            ud, _ = _fetch_discord_user(uid)
                            md    = _fetch_guild_member(guild_id, uid)
                            av    = _avatar_url(ud, md, guild_id)
                        results.append({
                            "target_id":    uid,
                            "username":     row.get("username") or uid,
                            "display_name": row.get("global_name") or row.get("username") or uid,
                            "discriminator": "0",
                            "avatar_url":   av,
                        })
                        seen.add(uid)
            except Exception as e:
                if not api_error:
                    api_error = f"Cache search error: {e}"
        try:
            members = _search_guild_members(guild_id, query)
            for m in members:
                u = m.get("user", {})
                uid = u.get("id", "")
                if uid and uid not in seen:
                    results.append({
                        "target_id":    uid,
                        "username":     u.get("username", uid),
                        "display_name": u.get("global_name") or u.get("username", uid),
                        "discriminator": u.get("discriminator", "0"),
                        "avatar_url":   _avatar_url(u, m, guild_id),
                    })
                    seen.add(uid)
        except Exception:
            pass
    return render_template("user_lookup.html", query=query, results=results, api_error=api_error)


@app.route("/user-lookup/<user_id>")
@login_required
def user_profile(user_id):
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    try:    warns = db.get_warnings_sync(guild_id, user_id)
    except: warns = []
    try:    logs  = db.logs_for_user_sync(guild_id, user_id)
    except: logs  = []
    bl = False
    try:    bl = db.is_user_blacklisted_sync(guild_id, user_id)
    except: pass

    user_data, api_err = _fetch_discord_user(user_id)
    member_data        = _fetch_guild_member(guild_id, user_id)
    guild_roles        = _fetch_guild_roles(guild_id)
    role_map           = {r["id"]: r for r in guild_roles}
    member_role_ids    = member_data.get("roles", [])
    member_roles = [
        {
            "id":    rid,
            "name":  role_map.get(rid, {}).get("name", rid),
            "color": f"#{role_map.get(rid, {}).get('color', 0x36393f):06x}",
        }
        for rid in member_role_ids
    ]
    member_roles.sort(key=lambda r: role_map.get(r["id"], {}).get("position", 0), reverse=True)
    profile = {
        "id":            user_id,
        "username":      user_data.get("username", user_id),
        "global_name":   user_data.get("global_name") or user_data.get("username", user_id),
        "discriminator": user_data.get("discriminator", "0"),
        "avatar_url":    _avatar_url(user_data, member_data, guild_id),
        "banner_url":    _banner_url(user_data, member_data, guild_id),
        "accent_color":  _accent_hex(user_data),
        "badges":        _get_badges(user_data.get("public_flags", 0)),
        "bio":           _bio(user_data),
        "bot":           user_data.get("bot", False),
        "nick":          member_data.get("nick") or "",
        "joined_at":     member_data.get("joined_at", "")[:10] if member_data.get("joined_at") else "",
        "roles":         member_roles,
        "api_error":     api_err,
    }
    return render_template("user_profile.html", user_id=user_id, profile=profile,
                           warns=warns, logs=logs, blacklisted=bl)


# ── API ────────────────────────────────────────────────────────────────────

@app.route("/api/profile/<user_id>")
@login_required
def api_profile(user_id):
    try:
        cached = db.get_cached_profile_sync(user_id)
        if cached:
            return jsonify(cached)
    except Exception:
        pass
    return jsonify({"error": "not_found"}), 404


@app.route("/api/debug/<user_id>")
@login_required
def api_debug(user_id):
    guild_id = _active_guild_id() or "0"
    user_data, err = _fetch_discord_user(user_id)
    member_data    = _fetch_guild_member(guild_id, user_id)
    return jsonify({
        "env": {"bot_token_set": bool(BOT_TOKEN), "active_guild": guild_id},
        "api_error": err,
        "user":   user_data,
        "member": member_data,
        "resolved": {
            "avatar_url":   _avatar_url(user_data, member_data, guild_id),
            "banner_url":   _banner_url(user_data, member_data, guild_id),
            "accent_color": _accent_hex(user_data),
            "bio":          _bio(user_data),
        }
    })


if __name__ == "__main__":
    db.init_db_sync()
    app.run(host="0.0.0.0", port=5000, debug=False)
