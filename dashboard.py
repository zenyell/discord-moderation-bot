import os
import json
import time
import traceback
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

# Load .env from the same directory as this file, regardless of cwd
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

import database as db

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "modpanel-secret-key-change-me")
app.config["PROPAGATE_EXCEPTIONS"] = False

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "admin123")
GUILD_ID       = os.getenv("GUILD_ID", "0")
BOT_TOKEN      = os.getenv("DISCORD_BOT_TOKEN", "")
HEARTBEAT_FILE = os.getenv("HEARTBEAT_PATH", "/tmp/bot_heartbeat")

DISCORD_API = "https://discord.com/api/v10"

print(f"[Dashboard] GUILD_ID={GUILD_ID!r}  BOT_TOKEN present={bool(BOT_TOKEN)}", flush=True)

db.init_db_sync()


# ── Context processor ──

@app.context_processor
def inject_user():
    if session.get("logged_in"):
        current_user = {
            "username": session.get("username", DASHBOARD_USER),
            "avatar":   session.get("avatar", ""),
            "logged_in": True,
        }
    else:
        current_user = {"username": "", "avatar": "", "logged_in": False}
    return {"current_user": current_user}


# ── Error handlers ──

@app.errorhandler(500)
def internal_error(e):
    tb = traceback.format_exc()
    return (
        f"""<!DOCTYPE html><html><head><title>500 Error</title>
        <style>
          body{{font-family:monospace;background:#1e2124;color:#dcddde;padding:32px;}}
          pre{{background:#2f3136;border:1px solid rgba(255,255,255,.08);border-radius:8px;
               padding:20px;overflow-x:auto;white-space:pre-wrap;font-size:13px;line-height:1.6;}}
          h2{{color:#ed4245;margin-bottom:16px;}}
          p{{color:#72767d;margin-bottom:12px;font-size:13px;}}
        </style></head><body>
        <h2>&#x26A0; 500 Internal Server Error</h2>
        <p>Real traceback:</p>
        <pre>{tb}</pre>
        </body></html>""",
        500,
    )


@app.errorhandler(Exception)
def unhandled_exception(e):
    tb = traceback.format_exc()
    return (
        f"""<!DOCTYPE html><html><head><title>Unhandled Exception</title>
        <style>
          body{{font-family:monospace;background:#1e2124;color:#dcddde;padding:32px;}}
          pre{{background:#2f3136;border:1px solid rgba(255,255,255,.08);border-radius:8px;
               padding:20px;overflow-x:auto;white-space:pre-wrap;font-size:13px;line-height:1.6;}}
          h2{{color:#ed4245;margin-bottom:16px;}}
          p{{color:#72767d;margin-bottom:12px;font-size:13px;}}
        </style></head><body>
        <h2>&#x26A0; Unhandled Exception: {type(e).__name__}</h2>
        <p>Real traceback:</p>
        <pre>{tb}</pre>
        </body></html>""",
        500,
    )


# ── Discord API helpers ──

def _discord_headers():
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def _fetch_discord_user(user_id: str) -> tuple[dict, str]:
    """Returns (data_dict, error_string). error_string is '' on success."""
    if not BOT_TOKEN:
        return {}, "DISCORD_BOT_TOKEN is not set in environment"
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/{user_id}",
            headers=_discord_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        return {}, f"Discord API HTTP {e.code}: {body}"
    except Exception as e:
        return {}, f"{type(e).__name__}: {e}"


def _fetch_guild_member(user_id: str) -> dict:
    if not BOT_TOKEN or not GUILD_ID or GUILD_ID == "0":
        return {}
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{GUILD_ID}/members/{user_id}",
            headers=_discord_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _fetch_guild_roles() -> list:
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{GUILD_ID}/roles",
            headers=_discord_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def _search_guild_members(query: str) -> list:
    """Search guild members by username prefix via Discord API (requires GUILD_MEMBERS intent)."""
    if not BOT_TOKEN or not GUILD_ID or GUILD_ID == "0":
        return []
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"{DISCORD_API}/guilds/{GUILD_ID}/members/search?query={encoded}&limit=10",
            headers=_discord_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def _avatar_url(user_data: dict, member_data: dict) -> str:
    uid = user_data.get("id", "")
    guild_av = member_data.get("avatar", "")
    if guild_av:
        ext = "gif" if guild_av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/guilds/{GUILD_ID}/users/{uid}/avatars/{guild_av}.{ext}?size=256"
    av = user_data.get("avatar", "")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{av}.{ext}?size=256"
    disc = user_data.get("discriminator", "0")
    idx  = (int(disc) % 5) if disc and disc != "0" else ((int(uid) >> 22) % 6) if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _banner_url(user_data: dict, member_data: dict) -> str:
    uid = user_data.get("id", "")
    guild_banner = member_data.get("banner", "") or ""
    if guild_banner:
        ext = "gif" if guild_banner.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/guilds/{GUILD_ID}/users/{uid}/banners/{guild_banner}.{ext}?size=1024"
    banner = user_data.get("banner", "") or ""
    if banner:
        ext = "gif" if banner.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/banners/{uid}/{banner}.{ext}?size=1024"
    return ""


def _accent_hex(user_data: dict) -> str:
    color = user_data.get("accent_color")
    if color:
        return f"#{color:06x}"
    return "#5865F2"


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


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Core routes ──

@app.route("/", methods=["GET"])
@login_required
def index():
    try:
        stats = db.log_stats_sync(GUILD_ID)
    except Exception:
        stats = {"total": 0, "kicks": 0, "bans": 0, "timeouts": 0, "warnings": 0, "purges": 0}
    try:
        logs = db.recent_logs_sync(GUILD_ID, 15)
    except Exception:
        logs = []
    return render_template("dashboard.html", stats=stats, logs=logs)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == DASHBOARD_USER and \
           request.form.get("password") == DASHBOARD_PASS:
            session["logged_in"] = True
            session["username"]   = DASHBOARD_USER
            return redirect(url_for("index"))
        flash("Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
        else:
            return jsonify({"online": False, "reason": f"heartbeat stale ({round(age)}s ago)"})
    except FileNotFoundError:
        return jsonify({"online": False, "reason": "heartbeat file not found"})
    except Exception as e:
        return jsonify({"online": False, "reason": str(e)})


# ── Settings ──

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        db.set_setting_sync("auto_role_id", request.form.get("auto_role_id", ""))
        db.set_setting_sync("bad_words",    request.form.get("bad_words", ""))
        db.set_setting_sync("spam_limit",   request.form.get("spam_limit", ""))
        db.set_setting_sync("spam_window",  request.form.get("spam_window", ""))
        db.set_setting_sync("spam_timeout", request.form.get("spam_timeout", ""))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id": db.get_setting_sync("auto_role_id", ""),
        "bad_words":    db.get_setting_sync("bad_words", os.getenv("BAD_WORDS", "")),
        "spam_limit":   db.get_setting_sync("spam_limit",  os.getenv("SPAM_MESSAGE_LIMIT", "6")),
        "spam_window":  db.get_setting_sync("spam_window", os.getenv("SPAM_WINDOW_SECONDS", "8")),
        "spam_timeout": db.get_setting_sync("spam_timeout", os.getenv("SPAM_TIMEOUT_MINUTES", "5")),
        "client_id":    os.getenv("DISCORD_CLIENT_ID", ""),
        "redirect_uri": os.getenv("DISCORD_REDIRECT_URI", ""),
    }
    return render_template("settings.html", cfg=cfg)


# ── Moderation ──

@app.route("/moderation", methods=["GET", "POST"])
@login_required
def moderation():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_word":
            word = request.form.get("word", "").strip()
            if word:
                db.add_blacklisted_word_sync(GUILD_ID, word, "dashboard")
                flash(f"Word '{word}' added to blacklist.")
        elif action == "remove_word":
            word = request.form.get("word", "").strip()
            db.remove_blacklisted_word_sync(GUILD_ID, word)
            flash(f"Word '{word}' removed.")
        elif action == "add_user":
            uid    = request.form.get("user_id", "").strip()
            reason = request.form.get("reason", "").strip()
            if uid:
                db.add_blacklisted_user_sync(GUILD_ID, uid, reason, "dashboard")
                flash(f"User {uid} blacklisted.")
        elif action == "remove_user":
            uid = request.form.get("user_id", "").strip()
            db.remove_blacklisted_user_sync(GUILD_ID, uid)
            flash(f"User {uid} removed from blacklist.")
        return redirect(url_for("moderation"))
    words = db.get_blacklisted_words_sync(GUILD_ID)
    users = db.get_blacklisted_users_sync(GUILD_ID)
    return render_template("moderation.html", words=words, users=users)


# ── Logging ──

LOG_EVENTS = [
    {"key": "ban",     "label": "Bans",            "desc": "Member bans"},
    {"key": "kick",    "label": "Kicks",           "desc": "Member kicks"},
    {"key": "timeout", "label": "Timeouts",        "desc": "Timeouts issued"},
    {"key": "warn",    "label": "Warnings",        "desc": "Warnings issued"},
    {"key": "unban",   "label": "Unbans",          "desc": "Bans lifted"},
    {"key": "purge",   "label": "Purges",          "desc": "Bulk message deletes"},
    {"key": "join",    "label": "Member Join",     "desc": "New members joining"},
    {"key": "leave",   "label": "Member Leave",    "desc": "Members leaving"},
    {"key": "edit",    "label": "Message Edits",   "desc": "Edited messages"},
    {"key": "delete",  "label": "Message Deletes", "desc": "Deleted messages"},
]


@app.route("/logging", methods=["GET", "POST"])
@login_required
def logging_page():
    if request.method == "POST":
        db.set_setting_sync("log_channel_id", request.form.get("log_channel_id", ""))
        for e in LOG_EVENTS:
            val = "1" if request.form.get(f"log_{e['key']}") else "0"
            db.set_setting_sync(f"log_{e['key']}", val)
        flash("Logging settings saved.")
        return redirect(url_for("logging_page"))
    cfg = {"log_channel_id": db.get_setting_sync("log_channel_id", "")}
    for e in LOG_EVENTS:
        cfg[f"log_{e['key']}"] = db.get_setting_sync(f"log_{e['key']}", "1")
    return render_template("logging.html", cfg=cfg, events=LOG_EVENTS)


# ── Reaction Roles ──

@app.route("/reaction-roles", methods=["GET", "POST"])
@login_required
def reaction_roles():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            db.add_reaction_role_sync(
                GUILD_ID,
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
    return render_template("reaction_roles.html", rr_list=db.get_reaction_roles_sync(GUILD_ID))


# ── Autoroles ──

@app.route("/autoroles", methods=["GET", "POST"])
@login_required
def autoroles():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            role_id = request.form.get("role_id", "").strip()
            if role_id:
                db.add_autorole_sync(GUILD_ID, role_id)
                flash(f"Autorole {role_id} added.")
        elif action == "remove":
            db.remove_autorole_sync(GUILD_ID, request.form.get("role_id", ""))
            flash("Autorole removed.")
        return redirect(url_for("autoroles"))
    return render_template("autoroles.html", autoroles=db.get_autoroles_sync(GUILD_ID))


# ── Tags ──

@app.route("/tags", methods=["GET", "POST"])
@login_required
def tags():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name    = request.form.get("tag_name", "").strip()
            content = request.form.get("tag_content", "").strip()
            if name and content:
                db.add_tag_sync(GUILD_ID, name, content)
                flash(f"Tag '{name}' saved.")
        elif action == "remove":
            db.remove_tag_sync(GUILD_ID, request.form.get("tag_name", ""))
            flash("Tag deleted.")
        return redirect(url_for("tags"))
    return render_template("tags.html", tags=db.get_tags_sync(GUILD_ID))


# ── Triggers ──

@app.route("/triggers", methods=["GET", "POST"])
@login_required
def triggers():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            phrase   = request.form.get("trigger_phrase", "").strip()
            response = request.form.get("trigger_response", "").strip()
            if phrase and response:
                db.add_trigger_sync(GUILD_ID, phrase, response)
                flash(f"Trigger '{phrase}' added.")
        elif action == "remove":
            db.remove_trigger_sync(request.form.get("trigger_id"))
            flash("Trigger deleted.")
        return redirect(url_for("triggers"))
    return render_template("triggers.html", triggers=db.get_triggers_sync(GUILD_ID))


# ── Greetings ──

_GREETING_KEYS = [
    "welcome_enabled", "welcome_channel", "welcome_message",
    "farewell_enabled", "farewell_channel", "farewell_message",
]


@app.route("/greetings", methods=["GET", "POST"])
@login_required
def greetings():
    if request.method == "POST":
        for k in _GREETING_KEYS:
            if k.endswith("_enabled"):
                db.set_setting_sync(k, "1" if request.form.get(k) else "0")
            else:
                db.set_setting_sync(k, request.form.get(k, ""))
        flash("Greetings saved.")
        return redirect(url_for("greetings"))

    class Cfg:
        pass
    cfg = Cfg()
    for k in _GREETING_KEYS:
        setattr(cfg, k, db.get_setting_sync(k, ""))
    return render_template("greetings.html", cfg=cfg)


# ── Starboard ──

_STARBOARD_KEYS = ["starboard_enabled", "starboard_channel", "starboard_min", "starboard_emoji"]


@app.route("/starboard", methods=["GET", "POST"])
@login_required
def starboard():
    if request.method == "POST":
        for k in _STARBOARD_KEYS:
            if k == "starboard_enabled":
                db.set_setting_sync(k, "1" if request.form.get(k) else "0")
            else:
                db.set_setting_sync(k, request.form.get(k, ""))
        flash("Starboard settings saved.")
        return redirect(url_for("starboard"))

    class Cfg:
        pass
    cfg = Cfg()
    for k in _STARBOARD_KEYS:
        setattr(cfg, k, db.get_setting_sync(k, ""))
    return render_template("starboard.html", cfg=cfg)


# ── Suggestions ──

@app.route("/suggestions", methods=["GET", "POST"])
@login_required
def suggestions():
    if request.method == "POST":
        db.set_setting_sync("suggestions_enabled", "1" if request.form.get("suggestions_enabled") else "0")
        db.set_setting_sync("suggestions_channel", request.form.get("suggestions_channel", ""))
        flash("Suggestions settings saved.")
        return redirect(url_for("suggestions"))

    class Cfg:
        pass
    cfg = Cfg()
    cfg.suggestions_enabled = db.get_setting_sync("suggestions_enabled", "0")
    cfg.suggestions_channel = db.get_setting_sync("suggestions_channel", "")
    return render_template("suggestions.html", cfg=cfg, suggestions=db.get_suggestions_sync(GUILD_ID))


# ── Command settings ──

@app.route("/command-settings", methods=["GET", "POST"])
@login_required
def command_settings():
    if request.method == "POST":
        for cmd in db.ALL_COMMANDS:
            enabled         = 1 if request.form.get(f"{cmd}_enabled") == "on" else 0
            whitelist_roles = request.form.get(f"{cmd}_whitelist_roles", "").strip()
            blacklist_mods  = request.form.get(f"{cmd}_blacklist_mods",  "").strip()
            whitelist_users = request.form.get(f"{cmd}_whitelist_users", "").strip()
            blacklist_users = request.form.get(f"{cmd}_blacklist_users", "").strip()
            db.set_command_setting_sync(
                GUILD_ID, cmd,
                enabled=enabled,
                whitelist_roles=whitelist_roles,
                blacklist_mods=blacklist_mods,
                whitelist_users=whitelist_users,
                blacklist_users=blacklist_users,
            )
        flash("Command settings saved. Changes take effect immediately on the bot.")
        return redirect(url_for("command_settings"))
    cmd_settings = db.get_command_settings_sync(GUILD_ID)
    return render_template("command_settings.html", commands=db.ALL_COMMANDS, cmd_settings=cmd_settings)


# ── User lookup ──

def _build_lookup_result(user_id: str) -> tuple[dict | None, str]:
    """Returns (result_dict_or_None, error_string)."""
    user_data, err = _fetch_discord_user(user_id)
    if not user_data.get("id"):
        return None, err or "User not found"
    member_data = _fetch_guild_member(user_id)
    av = _avatar_url(user_data, member_data)
    username      = user_data.get("username", user_id)
    global_name   = user_data.get("global_name") or username
    discriminator = user_data.get("discriminator", "0")
    return {
        "target_id":     user_id,
        "username":      username,
        "display_name":  global_name,
        "discriminator": discriminator,
        "avatar_url":    av,
    }, ""


@app.route("/user-lookup")
@login_required
def user_lookup():
    query   = request.args.get("q", "").strip()
    results = []
    api_error = ""

    if query:
        seen = set()

        # 1. If pure numeric — direct Discord user lookup by ID
        if query.isdigit():
            r, err = _build_lookup_result(query)
            if r:
                results.append(r)
                seen.add(query)
            elif err:
                api_error = err

        # 2. Search profile_cache by username / global_name
        if not query.isdigit() or not results:
            try:
                cached = db.search_profile_cache_sync(query)
                for row in cached:
                    uid = row.get("user_id", "")
                    if uid and uid not in seen:
                        av = row.get("avatar_url") or ""
                        if not av:
                            user_data, _ = _fetch_discord_user(uid)
                            member_data  = _fetch_guild_member(uid)
                            av = _avatar_url(user_data, member_data)
                        results.append({
                            "target_id":     uid,
                            "username":      row.get("username") or uid,
                            "display_name":  row.get("global_name") or row.get("username") or uid,
                            "discriminator": "0",
                            "avatar_url":    av,
                        })
                        seen.add(uid)
            except Exception as e:
                if not api_error:
                    api_error = f"Cache search error: {e}"

        # 3. Try Discord guild member search API
        try:
            members = _search_guild_members(query)
            for m in members:
                u = m.get("user", {})
                uid = u.get("id", "")
                if uid and uid not in seen:
                    av = _avatar_url(u, m)
                    username      = u.get("username", uid)
                    global_name   = u.get("global_name") or username
                    discriminator = u.get("discriminator", "0")
                    results.append({
                        "target_id":     uid,
                        "username":      username,
                        "display_name":  global_name,
                        "discriminator": discriminator,
                        "avatar_url":    av,
                    })
                    seen.add(uid)
        except Exception:
            pass

        # 4. Fallback: scan recent mod logs for partial ID match
        if query.isdigit() and not results:
            try:
                rows = db.recent_logs_sync(GUILD_ID, 500)
                for r in rows:
                    tid = r.get("target_id", "")
                    if query in tid and tid not in seen:
                        result, _ = _build_lookup_result(tid)
                        if result:
                            results.append(result)
                            seen.add(tid)
                        if len(results) >= 20:
                            break
            except Exception:
                pass

    return render_template("user_lookup.html", query=query, results=results, api_error=api_error)


@app.route("/user-lookup/<user_id>")
@login_required
def user_profile(user_id):
    try:
        warns = db.get_warnings_sync(GUILD_ID, user_id)
    except Exception:
        warns = []
    try:
        logs = db.logs_for_user_sync(GUILD_ID, user_id)
    except Exception:
        logs = []
    bl = False
    try:
        bl = db.is_user_blacklisted_sync(GUILD_ID, user_id)
    except Exception:
        pass

    user_data, api_err   = _fetch_discord_user(user_id)
    member_data = _fetch_guild_member(user_id)
    guild_roles = _fetch_guild_roles()

    role_map = {r["id"]: r for r in guild_roles}
    member_role_ids = member_data.get("roles", [])
    member_roles = [
        {
            "id":    rid,
            "name":  role_map.get(rid, {}).get("name", rid),
            "color": f"#{role_map.get(rid, {}).get('color', 0x36393f):06x}",
        }
        for rid in member_role_ids
    ]
    member_roles.sort(
        key=lambda r: role_map.get(r["id"], {}).get("position", 0),
        reverse=True
    )

    avatar_url   = _avatar_url(user_data, member_data)
    banner_url   = _banner_url(user_data, member_data)
    accent_color = _accent_hex(user_data)
    badges       = _get_badges(user_data.get("public_flags", 0))
    bio          = _bio(user_data)

    profile = {
        "id":            user_id,
        "username":      user_data.get("username", user_id),
        "global_name":   user_data.get("global_name") or user_data.get("username", user_id),
        "discriminator": user_data.get("discriminator", "0"),
        "avatar_url":    avatar_url,
        "banner_url":    banner_url,
        "accent_color":  accent_color,
        "badges":        badges,
        "bio":           bio,
        "bot":           user_data.get("bot", False),
        "nick":          member_data.get("nick") or "",
        "joined_at":     member_data.get("joined_at", "")[:10] if member_data.get("joined_at") else "",
        "roles":         member_roles,
        "api_error":     api_err,
    }

    return render_template(
        "user_profile.html",
        user_id=user_id,
        profile=profile,
        warns=warns,
        logs=logs,
        blacklisted=bl
    )


# ── API ──

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
    user_data, err = _fetch_discord_user(user_id)
    member_data    = _fetch_guild_member(user_id)
    return jsonify({
        "env": {
            "bot_token_set": bool(BOT_TOKEN),
            "guild_id": GUILD_ID,
        },
        "api_error": err,
        "user":   user_data,
        "member": member_data,
        "resolved": {
            "avatar_url":   _avatar_url(user_data, member_data),
            "banner_url":   _banner_url(user_data, member_data),
            "accent_color": _accent_hex(user_data),
            "bio":          _bio(user_data),
        }
    })


if __name__ == "__main__":
    db.init_db_sync()
    app.run(host="0.0.0.0", port=5000, debug=False)
