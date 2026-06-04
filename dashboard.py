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
print(f"[OAuth] DISCORD_REDIRECT_URI={DISCORD_REDIRECT_URI!r}", flush=True)

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


@app.context_processor
def inject_globals():
    guild_id = session.get("active_guild", "")
    cfg = {}
    if guild_id:
        cfg = {
            "prefix":            db.get_setting_sync(_gkey(guild_id, "prefix"),          "!"),
            "mod_log_channel":   db.get_setting_sync(_gkey(guild_id, "mod_log_channel"), ""),
            "mute_role":         db.get_setting_sync(_gkey(guild_id, "mute_role"),        ""),
            "dm_on_punish":      db.get_setting_sync(_gkey(guild_id, "dm_on_punish"),     "0") == "1",
            "delete_commands":   db.get_setting_sync(_gkey(guild_id, "delete_commands"),  "0") == "1",
        }
    return dict(
        session=session,
        guild_id=guild_id,
        config=cfg,
        bot_invite_url=BOT_INVITE_URL,
    )


@app.errorhandler(500)
def internal_error(e):
    tb = traceback.format_exc()
    print(f"[500] {tb}", flush=True)
    return render_template("500.html", traceback=tb) if app.debug else (
        "<h1>500 — Internal Server Error</h1><p>Something went wrong.</p>", 500
    )


@app.errorhandler(Exception)
def unhandled_exception(e):
    tb = traceback.format_exc()
    print(f"[Unhandled] {tb}", flush=True)
    short = str(e)
    return render_template(
        "error.html", error=short, traceback=tb
    ) if app.debug else (f"<h2>Error</h2><pre>{short}</pre>", 500)


def _bot_headers():
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    _UA,
    }


def _bot_guilds() -> set:
    """Return set of guild IDs the bot is currently in."""
    if not BOT_TOKEN:
        return set()
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return {g["id"] for g in data}
    except Exception as exc:
        print(f"[_bot_guilds] {exc}", flush=True)
        return set()


def _bot_guilds_detailed() -> list:
    """Return list of guild dicts the bot is currently in."""
    if not BOT_TOKEN:
        return []
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return data
    except Exception as exc:
        print(f"[_bot_guilds_detailed] {exc}", flush=True)
        return []


def _send_channel_message(channel_id: str, content: str) -> tuple[bool, str]:
    if not BOT_TOKEN:
        return False, "No bot token configured."
    try:
        payload = json.dumps({"content": content}).encode()
        req = urllib.request.Request(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=payload,
            headers=_bot_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True, ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as exc:
        return False, str(exc)


def _create_dm_channel(user_id: str) -> tuple[str, str]:
    if not BOT_TOKEN:
        return "", "No bot token configured."
    try:
        payload = json.dumps({"recipient_id": user_id}).encode()
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/channels",
            data=payload,
            headers=_bot_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("id", ""), ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return "", f"HTTP {e.code}: {body}"
    except Exception as exc:
        return "", str(exc)


def _leave_guild(guild_id: str) -> tuple[bool, str]:
    if not BOT_TOKEN:
        return False, "No bot token configured."
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/@me/guilds/{guild_id}",
            headers=_bot_headers(),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True, ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return False, f"HTTP {e.code}: {body}"
    except Exception as exc:
        return False, str(exc)


def _fetch_discord_user(user_id: str) -> tuple[dict, str]:
    if not BOT_TOKEN:
        return {}, "No bot token."
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/{user_id}",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return {}, f"HTTP {e.code}: {body}"
    except Exception as exc:
        return {}, str(exc)


def _fetch_guild_member(guild_id: str, user_id: str) -> dict:
    if not BOT_TOKEN:
        return {}
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _fetch_guild_roles(guild_id: str) -> list:
    if not BOT_TOKEN:
        return []
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/roles",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            roles = json.loads(resp.read())
        roles.sort(key=lambda r: -r.get("position", 0))
        return roles
    except Exception:
        return []


def _fetch_guild_member_count(guild_id: str) -> int:
    if not BOT_TOKEN:
        return 0
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}?with_counts=true",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return data.get("approximate_member_count", 0)
    except Exception:
        return 0


def _search_guild_members(guild_id: str, query: str) -> list:
    if not BOT_TOKEN or not query:
        return []
    try:
        q = urllib.parse.urlencode({"query": query, "limit": 10})
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{guild_id}/members/search?{q}",
            headers=_bot_headers(),
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def _avatar_url(user_data: dict, member_data: dict, guild_id: str = "") -> str:
    uid = user_data.get("id", "")
    # guild avatar
    if guild_id and member_data:
        ga = member_data.get("avatar", "")
        if ga:
            ext = "gif" if ga.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/guilds/{guild_id}/users/{uid}/avatars/{ga}.{ext}?size=256"
    # user avatar
    av = user_data.get("avatar", "")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{av}.{ext}?size=256"
    # default
    disc = user_data.get("discriminator", "0")
    idx  = (int(uid) >> 22) % 6 if disc in ("0", "") else int(disc) % 5
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _banner_url(user_data: dict, member_data: dict, guild_id: str = "") -> str:
    uid = user_data.get("id", "")
    # guild banner
    if guild_id and member_data:
        gb = member_data.get("banner", "")
        if gb:
            ext = "gif" if gb.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/guilds/{guild_id}/users/{uid}/banners/{gb}.{ext}?size=480"
    # user banner
    b = user_data.get("banner", "")
    if b:
        ext = "gif" if b.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/banners/{uid}/{b}.{ext}?size=480"
    return ""


def _accent_hex(user_data: dict) -> str:
    c = user_data.get("accent_color")
    if c:
        return f"#{c:06x}"
    return ""


def _bio(user_data: dict) -> str:
    return user_data.get("bio", "") or ""


def _get_badges(public_flags: int) -> list:
    BADGE_MAP = {
        1 << 0:  ("Staff",                  "discord-staff"),
        1 << 1:  ("Partner",                "partnered-server-owner"),
        1 << 2:  ("HypeSquad Events",       "hypesquad-events"),
        1 << 3:  ("Bug Hunter Lvl 1",       "bug-hunter"),
        1 << 6:  ("HypeSquad Bravery",      "bravery"),
        1 << 7:  ("HypeSquad Brilliance",   "brilliance"),
        1 << 8:  ("HypeSquad Balance",      "balance"),
        1 << 9:  ("Early Supporter",        "early-supporter"),
        1 << 14: ("Bug Hunter Lvl 2",       "bug-hunter-2"),
        1 << 17: ("Early Verified Bot Dev", "verified-bot-developer"),
        1 << 18: ("Moderator Alumni",       "moderator-alumni"),
        1 << 22: ("Active Developer",       "active-developer"),
    }
    return [{"label": label, "key": key}
            for flag, (label, key) in BADGE_MAP.items()
            if public_flags & flag]


# ── Auth decorators ────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = kwargs.get("token") or request.view_args.get("token", "")
        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def guild_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        guild_id = session.get("active_guild", "")
        if not guild_id:
            return redirect(url_for("servers"))
        # verify the bot is still in the guild
        bot_guild_ids = _bot_guilds()
        if bot_guild_ids and guild_id not in bot_guild_ids:
            session.pop("active_guild", None)
            flash("The bot is no longer in that server. Please select another.")
            return redirect(url_for("servers"))
        channels_raw = []
        roles_raw    = []
        if BOT_TOKEN:
            try:
                req = urllib.request.Request(
                    f"{DISCORD_API}/guilds/{guild_id}/channels",
                    headers=_bot_headers(),
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    channels_raw = json.loads(resp.read())
            except Exception:
                pass
            roles_raw = _fetch_guild_roles(guild_id)
        text_channels = sorted(
            [c for c in channels_raw if c.get("type") == 0],
            key=lambda c: c.get("position", 0),
        )
        kwargs["guild_id"]  = guild_id
        kwargs["channels"]  = text_channels
        kwargs["roles"]     = roles_raw
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


# ── Server selection ───────────────────────────────────────────────────────

@app.route("/servers")
@login_required
def servers():
    bot_guild_ids = _bot_guilds()
    user_guilds   = session.get("user_guilds", {})
    guilds = []
    for gid, g in user_guilds.items():
        bot_in = gid in bot_guild_ids
        guilds.append({**g, "bot_in": bot_in})
    guilds.sort(key=lambda g: (not g["bot_in"], g["name"].lower()))
    total_members = sum(
        _fetch_guild_member_count(g["id"]) for g in guilds if g["bot_in"]
    )
    return render_template(
        "servers.html",
        guilds=guilds,
        bot_invite_url=BOT_INVITE_URL,
        total_members=total_members,
    )


@app.route("/servers/select/<guild_id>")
@login_required
def select_guild(guild_id):
    user_guilds = session.get("user_guilds", {})
    if guild_id not in user_guilds:
        flash("You don't have access to that server.")
        return redirect(url_for("servers"))
    bot_guild_ids = _bot_guilds()
    if bot_guild_ids and guild_id not in bot_guild_ids:
        flash("The bot isn't in that server yet.")
        return redirect(url_for("servers"))
    session["active_guild"] = guild_id
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def index():
    guild_id = session.get("active_guild", "")
    if not guild_id:
        return redirect(url_for("servers"))
    stats = {
        "members": _fetch_guild_member_count(guild_id),
        "warnings": 0,
        "bans": 0,
        "kicks": 0,
    }
    try:
        stats["warnings"] = db.count_warnings_sync(guild_id)
    except Exception:
        pass
    try:
        stats["bans"] = db.count_bans_sync(guild_id)
    except Exception:
        pass
    try:
        stats["kicks"] = db.count_kicks_sync(guild_id)
    except Exception:
        pass
    logs = []
    try:
        logs = db.get_recent_logs_sync(guild_id, limit=10)
    except Exception:
        pass
    total_members = stats["members"]
    cfg = {
        "prefix":          db.get_setting_sync(_gkey(guild_id, "prefix"),          "!"),
        "mod_log_channel": db.get_setting_sync(_gkey(guild_id, "mod_log_channel"), ""),
    }
    return render_template("dashboard.html", stats=stats, logs=logs,
                           total_members=total_members, guild_id=guild_id, cfg=cfg)


@app.route("/api/status")
@login_required
def api_status():
    return jsonify({"status": "ok", "bot_token": bool(BOT_TOKEN)})


@app.route("/api/debug/bot-guilds")
@login_required
def api_debug_bot_guilds():
    bot_guild_ids = _bot_guilds()
    user_guilds   = session.get("user_guilds", {})
    result = {}
    for gid, g in user_guilds.items():
        result[gid] = {
            "name":   g.get("name"),
            "bot_in": gid in bot_guild_ids,
        }
    return jsonify({
        "bot_guilds":  list(bot_guild_ids),
        "user_guilds": result,
    })


# ── Utility ────────────────────────────────────────────────────────────────

def _active_guild_id():
    return session.get("active_guild", "")


def _require_guild():
    gid = _active_guild_id()
    if not gid:
        return redirect(url_for("servers")), None
    return None, gid


# ── Superadmin panel ───────────────────────────────────────────────────────

@app.route("/admin/<token>")
@superadmin_required
def admin_panel(token=None):
    guilds = _bot_guilds_detailed()
    return render_template("admin.html",
        token=token, guilds=guilds, message=None, error=None)


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
    guilds     = _bot_guilds_detailed()
    ok, err    = _send_channel_message(channel_id, content)
    return render_template("admin.html",
        token=token, guilds=guilds,
        message="Message sent!" if ok else None,
        error=err if not ok else None)


@app.route("/admin/<token>/dm-user", methods=["POST"])
@superadmin_required
def admin_dm_user(token=None):
    user_id = request.form.get("user_id", "").strip()
    content = request.form.get("content", "").strip()
    guilds  = _bot_guilds_detailed()
    ch_id, err = _create_dm_channel(user_id)
    if not ch_id:
        return render_template("admin.html", token=token, guilds=guilds,
            message=None, error=f"Could not create DM: {err}")
    ok, err2 = _send_channel_message(ch_id, content)
    return render_template("admin.html",
        token=token, guilds=guilds,
        message="DM sent!" if ok else None,
        error=err2 if not ok else None)


@app.route("/admin/<token>/broadcast", methods=["POST"])
@superadmin_required
def admin_broadcast(token=None):
    content = request.form.get("content", "").strip()
    guilds  = _bot_guilds_detailed()
    results = []
    for g in guilds:
        pass
    return render_template("admin.html",
        token=token, guilds=guilds,
        message="Broadcast complete.", error=None)


@app.route("/admin/<token>/leave-guild", methods=["POST"])
@superadmin_required
def admin_leave_guild(token=None):
    guild_id = request.form.get("guild_id", "").strip()
    guilds   = _bot_guilds_detailed()
    ok, err  = _leave_guild(guild_id)
    return render_template("admin.html",
        token=token, guilds=guilds,
        message=f"Left guild {guild_id}." if ok else None,
        error=err if not ok else None)


@app.route("/admin/<token>/wipe-guild", methods=["POST"])
@superadmin_required
def admin_wipe_guild(token=None):
    guild_id = request.form.get("guild_id", "").strip()
    guilds   = _bot_guilds_detailed()
    try:
        db.wipe_guild_sync(guild_id)
        msg = f"Guild {guild_id} data wiped."
        err = None
    except AttributeError:
        msg = None
        err = "db.wipe_guild_sync not implemented."
    except Exception as exc:
        msg = None
        err = str(exc)
    return render_template("admin.html",
        token=token, guilds=guilds,
        message=msg, error=err)


# ── Settings ───────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
@guild_required
def settings(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "prefix"),          request.form.get("prefix", "!"))
        db.set_setting_sync(_gkey(guild_id, "mod_log_channel"), request.form.get("mod_log_channel", ""))
        db.set_setting_sync(_gkey(guild_id, "mute_role"),       request.form.get("mute_role", ""))
        db.set_setting_sync(_gkey(guild_id, "dm_on_punish"),    "1" if request.form.get("dm_on_punish") else "0")
        db.set_setting_sync(_gkey(guild_id, "delete_commands"), "1" if request.form.get("delete_commands") else "0")
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "prefix":          db.get_setting_sync(_gkey(guild_id, "prefix"),          "!"),
        "mod_log_channel": db.get_setting_sync(_gkey(guild_id, "mod_log_channel"), ""),
        "mute_role":       db.get_setting_sync(_gkey(guild_id, "mute_role"),        ""),
        "dm_on_punish":    db.get_setting_sync(_gkey(guild_id, "dm_on_punish"),     "0") == "1",
        "delete_commands": db.get_setting_sync(_gkey(guild_id, "delete_commands"),  "0") == "1",
    }
    return render_template("settings.html", cfg=cfg, channels=channels, roles=roles)


# ── Moderation ─────────────────────────────────────────────────────────────

@app.route("/moderation", methods=["GET", "POST"])
@login_required
@guild_required
def moderation(guild_id, channels, roles):
    if request.method == "POST":
        action   = request.form.get("action")
        user_id  = request.form.get("user_id", "").strip()
        reason   = request.form.get("reason", "No reason provided").strip()
        duration = request.form.get("duration", "").strip()
        if action == "warn" and user_id:
            try:
                db.add_warning_sync(guild_id, user_id, reason, session.get("discord_id", ""))
                flash(f"Warning issued to <@{user_id}>.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "ban" and user_id:
            try:
                db.add_ban_sync(guild_id, user_id, reason, session.get("discord_id", ""))
                flash(f"Ban recorded for <@{user_id}>.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "kick" and user_id:
            try:
                db.add_kick_sync(guild_id, user_id, reason, session.get("discord_id", ""))
                flash(f"Kick recorded for <@{user_id}>.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "mute" and user_id:
            try:
                db.add_mute_sync(guild_id, user_id, reason, duration, session.get("discord_id", ""))
                flash(f"Mute recorded for <@{user_id}>.")
            except Exception as e:
                flash(f"Error: {e}")
        return redirect(url_for("moderation"))
    warnings = []
    bans     = []
    kicks    = []
    mutes    = []
    try: warnings = db.get_warnings_sync(guild_id)
    except Exception: pass
    try: bans     = db.get_bans_sync(guild_id)
    except Exception: pass
    try: kicks    = db.get_kicks_sync(guild_id)
    except Exception: pass
    try: mutes    = db.get_mutes_sync(guild_id)
    except Exception: pass
    return render_template("moderation.html",
        warnings=warnings, bans=bans, kicks=kicks, mutes=mutes,
        channels=channels, roles=roles)


# ── Logging ────────────────────────────────────────────────────────────────

@app.route("/logging", methods=["GET", "POST"])
@login_required
@guild_required
def logging_page(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "log_channel_id"), request.form.get("log_channel_id", ""))
        for key in LOG_TOGGLE_KEYS:
            db.set_setting_sync(_gkey(guild_id, key), "1" if request.form.get(key) else "0")
        flash("Logging settings saved.")
        return redirect(url_for("logging_page"))
    cfg = {
        "log_channel_id": db.get_setting_sync(_gkey(guild_id, "log_channel_id"), ""),
    }
    for key in LOG_TOGGLE_KEYS:
        cfg[key] = db.get_setting_sync(_gkey(guild_id, key), "0") == "1"
    return render_template("logging.html", cfg=cfg, channels=channels, roles=roles)


# ── Audit log ──────────────────────────────────────────────────────────────

@app.route("/audit-log")
@login_required
@guild_required
def audit_log(guild_id, channels, roles):
    logs = []
    try:
        logs = db.get_recent_logs_sync(guild_id, limit=50)
    except Exception:
        pass
    return render_template("audit_log.html", logs=logs)


# ── User lookup ────────────────────────────────────────────────────────────

@app.route("/user-lookup", methods=["GET", "POST"])
@login_required
@guild_required
def user_lookup(guild_id, channels, roles):
    profile     = None
    error_msg   = None
    search_query = request.form.get("query", "").strip() if request.method == "POST" else ""
    if search_query:
        # Try searching by username in the guild first
        members = _search_guild_members(guild_id, search_query)
        if members:
            member   = members[0]
            user_obj = member.get("user", {})
            uid      = user_obj.get("id", "")
        else:
            # Fall back: treat the query as a user ID
            uid      = search_query
            user_obj = {}
            member   = {}
        if uid:
            fetched_user, err = _fetch_discord_user(uid)
            if err:
                error_msg = err
            else:
                if not user_obj:
                    user_obj = fetched_user
                if not member:
                    member = _fetch_guild_member(guild_id, uid)
                warnings = []
                bans     = []
                kicks    = []
                mutes    = []
                try: warnings = db.get_warnings_sync(guild_id, uid)
                except Exception: pass
                try: bans     = db.get_bans_sync(guild_id, uid)
                except Exception: pass
                try: kicks    = db.get_kicks_sync(guild_id, uid)
                except Exception: pass
                try: mutes    = db.get_mutes_sync(guild_id, uid)
                except Exception: pass
                roles_list = []
                if member and BOT_TOKEN:
                    all_roles = _fetch_guild_roles(guild_id)
                    role_ids  = set(member.get("roles", []))
                    roles_list = [r for r in all_roles if r["id"] in role_ids]
                joined_at  = member.get("joined_at", "")
                nick       = member.get("nick", "")
                pf         = fetched_user.get("public_flags", 0) or 0
                badges     = _get_badges(pf)
                avatar_url = _avatar_url(fetched_user, member, guild_id)
                banner_url = _banner_url(fetched_user, member, guild_id)
                accent_hex = _accent_hex(fetched_user)
                bio        = _bio(fetched_user)
                profile = {
                    "user":      fetched_user,
                    "member":    member,
                    "uid":       uid,
                    "username":  fetched_user.get("global_name") or fetched_user.get("username", uid),
                    "nick":      nick,
                    "avatar":    avatar_url,
                    "banner":    banner_url,
                    "accent":    accent_hex,
                    "bio":       bio,
                    "badges":    badges,
                    "joined_at": joined_at,
                    "roles":     roles_list,
                    "warnings":  warnings,
                    "bans":      bans,
                    "kicks":     kicks,
                    "mutes":     mutes,
                }
        else:
            error_msg = "Could not resolve that user."
    return render_template("user_lookup.html",
        profile=profile, error=error_msg, query=search_query)


# ── Reaction roles ─────────────────────────────────────────────────────────

@app.route("/reaction-roles", methods=["GET", "POST"])
@login_required
@guild_required
def reaction_roles(guild_id, channels, roles):
    if request.method == "POST":
        action    = request.form.get("action")
        channel_id = request.form.get("channel_id", "").strip()
        message_id = request.form.get("message_id", "").strip()
        emoji      = request.form.get("emoji", "").strip()
        role_id    = request.form.get("role_id", "").strip()
        if action == "add" and channel_id and message_id and emoji and role_id:
            try:
                db.add_reaction_role_sync(guild_id, channel_id, message_id, emoji, role_id)
                flash("Reaction role added.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "remove":
            rr_id = request.form.get("rr_id", "").strip()
            if rr_id:
                try:
                    db.remove_reaction_role_sync(guild_id, rr_id)
                    flash("Reaction role removed.")
                except Exception as e:
                    flash(f"Error: {e}")
        return redirect(url_for("reaction_roles"))
    rr_list = []
    try:
        rr_list = db.get_reaction_roles_sync(guild_id)
    except Exception:
        pass
    return render_template("reaction_roles.html", rr_list=rr_list,
                           channels=channels, roles=roles)


# ── Autoroles ──────────────────────────────────────────────────────────────

@app.route("/autoroles", methods=["GET", "POST"])
@login_required
@guild_required
def autoroles(guild_id, channels, roles):
    if request.method == "POST":
        action  = request.form.get("action")
        role_id = request.form.get("role_id", "").strip()
        if action == "add" and role_id:
            try:
                db.add_autorole_sync(guild_id, role_id)
                flash("Autorole added.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "remove" and role_id:
            try:
                db.remove_autorole_sync(guild_id, role_id)
                flash("Autorole removed.")
            except Exception as e:
                flash(f"Error: {e}")
        return redirect(url_for("autoroles"))
    autorole_list = []
    try:
        autorole_list = db.get_autoroles_sync(guild_id)
    except Exception:
        pass
    return render_template("autoroles.html", autorole_list=autorole_list,
                           channels=channels, roles=roles)


# ── Greetings ──────────────────────────────────────────────────────────────

@app.route("/greetings", methods=["GET", "POST"])
@login_required
@guild_required
def greetings(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "welcome_channel_id"),  request.form.get("welcome_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "welcome_message"),     request.form.get("welcome_message", ""))
        db.set_setting_sync(_gkey(guild_id, "goodbye_channel_id"),  request.form.get("goodbye_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "goodbye_message"),     request.form.get("goodbye_message", ""))
        flash("Greeting settings saved.")
        return redirect(url_for("greetings"))
    cfg = {
        "welcome_channel_id": db.get_setting_sync(_gkey(guild_id, "welcome_channel_id"), ""),
        "welcome_message":    db.get_setting_sync(_gkey(guild_id, "welcome_message"),    ""),
        "goodbye_channel_id": db.get_setting_sync(_gkey(guild_id, "goodbye_channel_id"), ""),
        "goodbye_message":    db.get_setting_sync(_gkey(guild_id, "goodbye_message"),    ""),
    }
    return render_template("greetings.html", cfg=cfg, channels=channels, roles=roles)


# ── Tags ───────────────────────────────────────────────────────────────────

@app.route("/tags", methods=["GET", "POST"])
@login_required
@guild_required
def tags(guild_id, channels, roles):
    if request.method == "POST":
        action   = request.form.get("action")
        tag_name = request.form.get("name", "").strip()
        content  = request.form.get("content", "").strip()
        if action == "add" and tag_name and content:
            try:
                db.add_tag_sync(guild_id, tag_name, content, session.get("discord_id", ""))
                flash(f"Tag '{tag_name}' added.")
            except Exception as e:
                flash(f"Error: {e}")
        elif action == "remove" and tag_name:
            try:
                db.remove_tag_sync(guild_id, tag_name)
                flash(f"Tag '{tag_name}' removed.")
            except Exception as e:
                flash(f"Error: {e}")
        return redirect(url_for("tags"))
    tag_list = []
    try:
        tag_list = db.get_tags_sync(guild_id)
    except Exception:
        pass
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
@guild_required
def starboard(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "starboard_channel_id"), request.form.get("starboard_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "starboard_threshold"),  request.form.get("starboard_threshold", "3"))
        db.set_setting_sync(_gkey(guild_id, "starboard_enabled"),    "1" if request.form.get("starboard_enabled") else "0")
        flash("Starboard settings saved.")
        return redirect(url_for("starboard"))
    cfg = {
        "starboard_channel_id": db.get_setting_sync(_gkey(guild_id, "starboard_channel_id"), ""),
        "starboard_threshold":  db.get_setting_sync(_gkey(guild_id, "starboard_threshold"),  "3"),
        "starboard_enabled":    db.get_setting_sync(_gkey(guild_id, "starboard_enabled"),    "0") == "1",
    }
    return render_template("starboard.html", cfg=cfg, channels=channels, roles=roles)


# ── Suggestions ────────────────────────────────────────────────────────────

@app.route("/suggestions", methods=["GET", "POST"])
@login_required
@guild_required
def suggestions(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "suggestions_channel_id"), request.form.get("suggestions_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "suggestions_enabled"),    "1" if request.form.get("suggestions_enabled") else "0")
        flash("Suggestions settings saved.")
        return redirect(url_for("suggestions"))
    cfg = {
        "suggestions_channel_id": db.get_setting_sync(_gkey(guild_id, "suggestions_channel_id"), ""),
        "suggestions_enabled":    db.get_setting_sync(_gkey(guild_id, "suggestions_enabled"),    "0") == "1",
    }
    return render_template("suggestions.html", cfg=cfg, channels=channels, roles=roles)


# ── Command settings ───────────────────────────────────────────────────────

@app.route("/command-settings", methods=["GET", "POST"])
@login_required
@guild_required
def command_settings(guild_id, channels, roles):
    if request.method == "POST":
        db.set_setting_sync(_gkey(guild_id, "commands_prefix"),     request.form.get("commands_prefix", "!"))
        db.set_setting_sync(_gkey(guild_id, "commands_channel_id"), request.form.get("commands_channel_id", ""))
        db.set_setting_sync(_gkey(guild_id, "commands_restrict"),   "1" if request.form.get("commands_restrict") else "0")
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
