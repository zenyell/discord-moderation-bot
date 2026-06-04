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

# Bot invite URL
BOT_INVITE_URL = (
    f"https://discord.com/oauth2/authorize"
    f"?client_id={DISCORD_CLIENT_ID}"
    f"&permissions=8"
    f"&scope=bot%20applications.commands"
) if DISCORD_CLIENT_ID else "#"

DISCORD_API    = "https://discord.com/api/v10"
DISCORD_OAUTH  = "https://discord.com/api/oauth2"
DISCORD_SCOPES = "identify guilds"

PERM_ADMINISTRATOR  = 0x8
PERM_MANAGE_GUILD   = 0x20
ADMIN_PERMS         = PERM_ADMINISTRATOR | PERM_MANAGE_GUILD

_UA = "DiscordBot (https://github.com/zenyell/discord-moderation-bot, 1.0) Python/3 urllib"

# ── Superadmin config ──────────────────────────────────────────────────────
SUPERADMIN_ID = "923615726940590150"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN") or secrets.token_urlsafe(32)
print(f"[Superadmin] Admin panel at: /admin/{ADMIN_TOKEN}", flush=True)

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
    active_guild = session.get("active_guild")
    return {
        "current_user": current_user,
        "active_guild": active_guild,
        "bot_invite_url": BOT_INVITE_URL,
        "admin_token": ADMIN_TOKEN,
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
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }


def _bot_guilds() -> set:
    if not BOT_TOKEN:
        return set()
    guild_ids = set()
    after = None
    page = 0
    while True:
        page += 1
        url = f"{DISCORD_API}/users/@me/guilds?limit=200"
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers=_bot_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                batch = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode()
            except Exception: pass
            print(f"[_bot_guilds] HTTP {e.code} on page {page}: {body}", flush=True)
            break
        except Exception as ex:
            print(f"[_bot_guilds] Error on page {page}: {ex}", flush=True)
            break
        if not batch:
            break
        for g in batch:
            guild_ids.add(g["id"])
        if len(batch) < 200:
            break
        after = batch[-1]["id"]
    return guild_ids


def _bot_guilds_detailed() -> list:
    """Return list of guild dicts (with id, name, approximate_member_count)."""
    if not BOT_TOKEN:
        return []
    guilds = []
    after = None
    while True:
        url = f"{DISCORD_API}/users/@me/guilds?limit=200"
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers=_bot_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                batch = json.loads(resp.read())
        except Exception:
            break
        if not batch:
            break
        guilds.extend(batch)
        if len(batch) < 200:
            break
        after = batch[-1]["id"]
    return guilds


def _send_channel_message(channel_id: str, content: str) -> tuple[bool, str]:
    """Send a message to a channel via the bot. Returns (success, info)."""
    if not BOT_TOKEN:
        return False, "BOT_TOKEN not set"
    payload = json.dumps({"content": content}).encode()
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=payload, headers=_bot_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return True, f"Message sent. ID: {data.get('id', '?')}"
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


def _create_dm_channel(user_id: str) -> tuple[str, str]:
    """Create a DM channel with a user. Returns (channel_id, error)."""
    if not BOT_TOKEN:
        return "", "BOT_TOKEN not set"
    payload = json.dumps({"recipient_id": user_id}).encode()
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/channels",
            data=payload, headers=_bot_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("id", ""), ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return "", f"HTTP {e.code}: {body}"
    except Exception as ex:
        return "", f"{type(ex).__name__}: {ex}"


def _leave_guild(guild_id: str) -> tuple[bool, str]:
    """Make the bot leave a guild."""
    if not BOT_TOKEN:
        return False, "BOT_TOKEN not set"
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds/{guild_id}",
            headers=_bot_headers(), method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"Left guild {guild_id}"
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


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


def superadmin_required(f):
    """Only the hardcoded SUPERADMIN_ID can access this route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if session.get("discord_id") != SUPERADMIN_ID:
            abort(404)
        token = kwargs.pop("token", None)
        if token != ADMIN_TOKEN:
            abort(404)
        return f(*args, **kwargs)
    return decorated


def guild_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        guild_id = (
            kwargs.get("guild_id")
            or request.args.get("guild_id")
            or session.get("active_guild", {}).get("id")
        )
        if not guild_id:
            return redirect(url_for("servers"))
        user_guilds = session.get("user_guilds", {})
        guild_info  = user_guilds.get(guild_id)
        if not guild_info:
            flash("You no longer have access to that server.")
            return redirect(url_for("servers"))
        perms = int(guild_info.get("permissions", 0))
        if not (perms & ADMIN_PERMS):
            flash("You need Administrator or Manage Server permission.")
            return redirect(url_for("servers"))
        if "guild_id" in f.__code__.co_varnames:
            kwargs["guild_id"] = guild_id
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
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent":   _UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_oauth_user(access_token: str) -> dict:
    req = urllib.request.Request(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_oauth_guilds(access_token: str) -> list:
    req = urllib.request.Request(
        f"{DISCORD_API}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": _UA},
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
            "Discord OAuth is not configured.", oauth_url=None)
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
    try:
        user = _fetch_oauth_user(access_token)
    except Exception as e:
        flash(f"Failed to fetch user: {e}")
        return redirect(url_for("login"))
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
    session["user_guilds"] = admin_guilds
    session.pop("active_guild", None)
    if user_id == SUPERADMIN_ID:
        return redirect(f"/admin/{ADMIN_TOKEN}")
    return redirect(url_for("servers"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# ── Server picker ──────────────────────────────────────────────────────────

@app.route("/servers")
@login_required
def servers():
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


# ── Dashboard ──────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def index():
    guild = session.get("active_guild")
    if not guild:
        return redirect(url_for("servers"))
    guild_id = guild["id"]
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
    raw_master = db.get_setting_sync(_gkey(guild_id, "logging_enabled"), None)
    cfg = {
        "logging_enabled": "0" if raw_master == "0" else "1",
        "log_channel_id":  db.get_setting_sync(_gkey(guild_id, "log_channel_id"), ""),
    }
    return render_template("dashboard.html", stats=stats, logs=logs,
                           total_members=total_members, guild_id=guild_id, cfg=cfg)


@app.route("/api/status")
@login_required
def api_status():
    return jsonify({"online": True, "name": "Mask", "age": 0})


@app.route("/api/debug/bot-guilds")
@login_required
def api_debug_bot_guilds():
    bot_token_set = bool(BOT_TOKEN)
    bot_guild_ids = set()
    api_error = None
    if bot_token_set:
        try:
            req = urllib.request.Request(
                f"{DISCORD_API}/users/@me/guilds?limit=200",
                headers=_bot_headers())
            with urllib.request.urlopen(req, timeout=10) as resp:
                batch = json.loads(resp.read())
                bot_guild_ids = {g["id"] for g in batch}
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode()
            except Exception: pass
            api_error = f"HTTP {e.code}: {body}"
        except Exception as ex:
            api_error = f"{type(ex).__name__}: {ex}"
    user_guilds = session.get("user_guilds", {})
    comparison = []
    for gid, g in user_guilds.items():
        comparison.append({"id": gid, "name": g.get("name", gid), "bot_sees": gid in bot_guild_ids})
    return jsonify({
        "bot_token_set": bot_token_set,
        "bot_token_prefix": BOT_TOKEN[:12] + "..." if BOT_TOKEN else None,
        "api_error": api_error,
        "bot_guild_count": len(bot_guild_ids),
        "your_guilds": comparison,
    })


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


# ══════════════════════════════════════════════════════════════════════════
# ── SUPERADMIN ROUTES  (/admin/<token>/...)  ──────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

@app.route("/admin/<token>")
@superadmin_required
def admin_panel(token=None):
    guilds = _bot_guilds_detailed()
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=None, dm_result=None,
        broadcast_result=None, leave_result=None, wipe_result=None,
    )


@app.route("/admin/<token>/api/guild-count")
@superadmin_required
def admin_guild_count(token=None):
    guilds = _bot_guilds_detailed()
    return jsonify({"count": len(guilds)})


@app.route("/admin/<token>/send-message", methods=["POST"])
@superadmin_required
def admin_send_message(token=None):
    channel_id = request.form.get("channel_id", "").strip()
    content    = request.form.get("content", "").strip()
    ok, info   = _send_channel_message(channel_id, content)
    guilds     = _bot_guilds_detailed()
    flash(info)
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=info, dm_result=None,
        broadcast_result=None, leave_result=None, wipe_result=None,
    )


@app.route("/admin/<token>/dm-user", methods=["POST"])
@superadmin_required
def admin_dm_user(token=None):
    user_id = request.form.get("user_id", "").strip()
    content = request.form.get("content", "").strip()
    ch_id, err = _create_dm_channel(user_id)
    if err:
        result = f"Failed to open DM: {err}"
    else:
        ok, result = _send_channel_message(ch_id, content)
    guilds = _bot_guilds_detailed()
    flash(result)
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=None, dm_result=result,
        broadcast_result=None, leave_result=None, wipe_result=None,
    )


@app.route("/admin/<token>/broadcast", methods=["POST"])
@superadmin_required
def admin_broadcast(token=None):
    content    = request.form.get("content", "").strip()
    channel_id = request.form.get("channel_id", "").strip()
    guilds     = _bot_guilds_detailed()
    ok, info   = _send_channel_message(channel_id, content)
    result     = f"Broadcast attempt to channel {channel_id}: {info}"
    flash(result)
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=None, dm_result=None,
        broadcast_result=result, leave_result=None, wipe_result=None,
    )


@app.route("/admin/<token>/leave-guild", methods=["POST"])
@superadmin_required
def admin_leave_guild(token=None):
    guild_id = request.form.get("guild_id", "").strip()
    ok, result = _leave_guild(guild_id)
    guilds = _bot_guilds_detailed()
    flash(result)
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=None, dm_result=None,
        broadcast_result=None, leave_result=result, wipe_result=None,
    )


@app.route("/admin/<token>/wipe-guild", methods=["POST"])
@superadmin_required
def admin_wipe_guild(token=None):
    guild_id = request.form.get("guild_id", "").strip()
    confirm  = request.form.get("confirm", "").strip()
    if confirm != "CONFIRM":
        result = "Aborted: you must type CONFIRM exactly."
    else:
        try:
            db.wipe_guild_sync(guild_id)
            result = f"All data wiped for guild {guild_id}."
        except AttributeError:
            result = "wipe_guild_sync not implemented in database.py yet."
        except Exception as e:
            result = f"Error: {e}"
    guilds = _bot_guilds_detailed()
    flash(result)
    return render_template("admin.html",
        guilds_list=guilds,
        send_result=None, dm_result=None,
        broadcast_result=None, leave_result=None, wipe_result=result,
    )


# ══════════════════════════════════════════════════════════════════════════
# ── Regular dashboard routes ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

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


@app.route("/logging", methods=["GET", "POST"])
@login_required
def logging_page():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        master = "1" if request.form.get("logging_enabled") else "0"
        db.set_setting_sync(_gkey(guild_id, "logging_enabled"), master)
        db.set_setting_sync(_gkey(guild_id, "log_channel_id"), request.form.get("log_channel_id", ""))
        for key in LOG_TOGGLE_KEYS:
            val = "1" if request.form.get(key) else "0"
            db.set_setting_sync(_gkey(guild_id, key), val)
        flash("Log settings saved.")
        return redirect(url_for("logging_page"))
    raw_master = db.get_setting_sync(_gkey(guild_id, "logging_enabled"), None)
    logging_enabled = (raw_master != "0")
    cfg = {
        "logging_enabled": logging_enabled,
        "log_channel_id":  db.get_setting_sync(_gkey(guild_id, "log_channel_id"), ""),
    }
    for key in LOG_TOGGLE_KEYS:
        raw = db.get_setting_sync(_gkey(guild_id, key), None)
        cfg[key] = (raw == "1") if raw is not None else True
    return render_template("logging.html", cfg=cfg)


@app.route("/audit-log")
@login_required
def audit_log():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = int(request.args.get("per_page", 50))
    per_page      = per_page if per_page in (25, 50, 100) else 50
    search        = request.args.get("q", "").strip()
    action_filter = request.args.get("action", "").strip().lower()
    try:
        result = db.all_logs_sync(guild_id, page=page, per_page=per_page,
                                  search=search, action_filter=action_filter)
    except Exception:
        result = {"logs": [], "total": 0, "page": 1, "per_page": per_page, "pages": 1}
    return render_template("audit_log.html", result=result, search=search,
                           action_filter=action_filter, per_page=per_page)


# ── User Lookup ────────────────────────────────────────────────────────────

@app.route("/user-lookup", methods=["GET", "POST"])
@login_required
def user_lookup():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))

    user_data   = None
    member_data = {}
    infraction_history = []
    badges      = []
    avatar      = ""
    banner      = ""
    accent      = "#5865F2"
    bio         = ""
    error       = None
    query       = ""
    search_results = []

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            # If it looks like a user ID, fetch directly
            if query.isdigit() and len(query) >= 15:
                user_data, err = _fetch_discord_user(query)
                if err:
                    error = err
                else:
                    member_data = _fetch_guild_member(guild_id, query)
                    try:
                        infraction_history = db.user_logs_sync(guild_id, query)
                    except Exception:
                        infraction_history = []
                    badges = _get_badges(user_data.get("public_flags", 0))
                    avatar = _avatar_url(user_data, member_data, guild_id)
                    banner = _banner_url(user_data, member_data, guild_id)
                    accent = _accent_hex(user_data)
                    bio    = _bio(user_data)
            else:
                # Search by username
                members = _search_guild_members(guild_id, query)
                if len(members) == 1:
                    uid = members[0].get("user", {}).get("id", "")
                    if uid:
                        user_data, err = _fetch_discord_user(uid)
                        if not err:
                            member_data = members[0]
                            try:
                                infraction_history = db.user_logs_sync(guild_id, uid)
                            except Exception:
                                infraction_history = []
                            badges = _get_badges(user_data.get("public_flags", 0))
                            avatar = _avatar_url(user_data, member_data, guild_id)
                            banner = _banner_url(user_data, member_data, guild_id)
                            accent = _accent_hex(user_data)
                            bio    = _bio(user_data)
                        else:
                            error = err
                elif len(members) > 1:
                    search_results = members
                else:
                    error = f"No members found matching '{query}'."

    return render_template(
        "user_lookup.html",
        user_data=user_data,
        member_data=member_data,
        infraction_history=infraction_history,
        badges=badges,
        avatar=avatar,
        banner=banner,
        accent=accent,
        bio=bio,
        error=error,
        query=query,
        search_results=search_results,
        guild_id=guild_id,
    )


@app.route("/reaction-roles", methods=["GET", "POST"])
@login_required
def reaction_roles():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            channel_id = request.form.get("channel_id", "").strip()
            message_id = request.form.get("message_id", "").strip()
            emoji      = request.form.get("emoji", "").strip()
            role_id    = request.form.get("role_id", "").strip()
            if channel_id and message_id and emoji and role_id:
                try:
                    db.add_reaction_role_sync(guild_id, channel_id, message_id, emoji, role_id)
                    flash("Reaction role added.")
                except Exception as e:
                    flash(f"Error: {e}")
        elif action == "remove":
            rr_id = request.form.get("rr_id", "").strip()
            if rr_id:
                try:
                    db.remove_reaction_role_sync(guild_id, int(rr_id))
                    flash("Reaction role removed.")
                except Exception as e:
                    flash(f"Error: {e}")
        return redirect(url_for("reaction_roles"))
    try:
        rr_list = db.get_reaction_roles_sync(guild_id)
    except Exception:
        rr_list = []
    return render_template("reaction_roles.html", rr_list=rr_list)


@app.route("/autoroles", methods=["GET", "POST"])
@login_required
def autoroles():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action  = request.form.get("action")
        role_id = request.form.get("role_id", "").strip()
        if action == "add" and role_id:
            try:
                db.add_autorole_sync(guild_id, role_id)
                flash(f"Auto-role {role_id} added.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "remove" and role_id:
            try:
                db.remove_autorole_sync(guild_id, role_id)
                flash(f"Auto-role {role_id} removed.")
            except Exception as e:
                flash(f"Error: {e}")
        return redirect(url_for("autoroles"))
    try:
        roles = db.get_autoroles_sync(guild_id)
    except Exception:
        roles = []
    guild_roles = _fetch_guild_roles(guild_id)
    return render_template("autoroles.html", roles=roles, guild_roles=guild_roles)


@app.route("/greetings", methods=["GET", "POST"])
@login_required
def greetings():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "welcome_channel_id"),  request.form.get("welcome_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "welcome_message"),     request.form.get("welcome_message", ""))
        db.set_setting_sync(_gkey(guild_id, "farewell_channel_id"), request.form.get("farewell_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "farewell_message"),    request.form.get("farewell_message", ""))
        flash("Greeting settings saved.")
        return redirect(url_for("greetings"))
    cfg = {
        "welcome_channel_id":  db.get_setting_sync(_gkey(guild_id, "welcome_channel_id"), ""),
        "welcome_message":     db.get_setting_sync(_gkey(guild_id, "welcome_message"),    "Welcome {user} to {server}!"),
        "farewell_channel_id": db.get_setting_sync(_gkey(guild_id, "farewell_channel_id"), ""),
        "farewell_message":    db.get_setting_sync(_gkey(guild_id, "farewell_message"),   "Goodbye {user}!"),
    }
    return render_template("greetings.html", cfg=cfg)


@app.route("/tags", methods=["GET", "POST"])
@login_required
def tags():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name    = request.form.get("name", "").strip().lower()
            content = request.form.get("content", "").strip()
            if name and content:
                try:
                    db.add_tag_sync(guild_id, name, content, session.get("discord_id", ""))
                    flash(f"Tag '{name}' added.")
                except Exception as e:
                    flash(f"Error: {e}")
        elif action == "remove":
            name = request.form.get("name", "").strip().lower()
            if name:
                try:
                    db.remove_tag_sync(guild_id, name)
                    flash(f"Tag '{name}' removed.")
                except Exception as e:
                    flash(f"Error: {e}")
        return redirect(url_for("tags"))
    try:
        tag_list = db.get_tags_sync(guild_id)
    except Exception:
        tag_list = []
    return render_template("tags.html", tag_list=tag_list)


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
            pattern  = request.form.get("pattern", "").strip()
            response = request.form.get("response", "").strip()
            if pattern and response:
                try:
                    db.add_trigger_sync(guild_id, pattern, response, session.get("discord_id", ""))
                    flash(f"Trigger '{pattern}' added.")
                except AttributeError:
                    flash("Trigger support not yet implemented in database.py.")
                except Exception as e:
                    flash(f"Error: {e}")
        elif action == "remove":
            pattern = request.form.get("pattern", "").strip()
            if pattern:
                try:
                    db.remove_trigger_sync(guild_id, pattern)
                    flash(f"Trigger '{pattern}' removed.")
                except AttributeError:
                    flash("Trigger support not yet implemented in database.py.")
                except Exception as e:
                    flash(f"Error: {e}")
        return redirect(url_for("triggers"))
    try:
        trigger_list = db.get_triggers_sync(guild_id)
    except AttributeError:
        trigger_list = []
    except Exception:
        trigger_list = []
    return render_template("triggers.html", trigger_list=trigger_list)


# ── Starboard ──────────────────────────────────────────────────────────────

@app.route("/starboard", methods=["GET", "POST"])
@login_required
def starboard():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "starboard_channel_id"), request.form.get("starboard_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "starboard_threshold"),  request.form.get("starboard_threshold", "3"))
        db.set_setting_sync(_gkey(guild_id, "starboard_enabled"),    "1" if request.form.get("starboard_enabled") else "0")
        flash("Starboard settings saved.")
        return redirect(url_for("starboard"))
    cfg = {
        "starboard_channel_id": db.get_setting_sync(_gkey(guild_id, "starboard_channel_id"), ""),
        "starboard_threshold":  db.get_setting_sync(_gkey(guild_id, "starboard_threshold"),  "3"),
        "starboard_enabled":    db.get_setting_sync(_gkey(guild_id, "starboard_enabled"),    "1") != "0",
    }
    return render_template("starboard.html", cfg=cfg)


# ── Suggestions ────────────────────────────────────────────────────────────

@app.route("/suggestions", methods=["GET", "POST"])
@login_required
def suggestions():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "suggestions_channel_id"), request.form.get("suggestions_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "suggestions_enabled"),    "1" if request.form.get("suggestions_enabled") else "0")
        flash("Suggestions settings saved.")
        return redirect(url_for("suggestions"))
    cfg = {
        "suggestions_channel_id": db.get_setting_sync(_gkey(guild_id, "suggestions_channel_id"), ""),
        "suggestions_enabled":    db.get_setting_sync(_gkey(guild_id, "suggestions_enabled"),    "1") != "0",
    }
    return render_template("suggestions.html", cfg=cfg)


# ── Command Settings ───────────────────────────────────────────────────────

@app.route("/command-settings", methods=["GET", "POST"])
@login_required
def command_settings():
    guild_id = _active_guild_id()
    if not guild_id:
        return redirect(url_for("servers"))
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "commands_prefix"),        request.form.get("commands_prefix", "!"))
        db.set_setting_sync(_gkey(guild_id, "commands_channel_id"),    request.form.get("commands_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "commands_restrict"),      "1" if request.form.get("commands_restrict") else "0")
        flash("Command settings saved.")
        return redirect(url_for("command_settings"))
    cfg = {
        "commands_prefix":     db.get_setting_sync(_gkey(guild_id, "commands_prefix"),     "!"),
        "commands_channel_id": db.get_setting_sync(_gkey(guild_id, "commands_channel_id"), ""),
        "commands_restrict":   db.get_setting_sync(_gkey(guild_id, "commands_restrict"),   "0") == "1",
    }
    return render_template("command_settings.html", cfg=cfg)


# ── Switch guild ───────────────────────────────────────────────────────────

@app.route("/switch-guild")
@login_required
def switch_guild():
    session.pop("active_guild", None)
    return redirect(url_for("servers"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
