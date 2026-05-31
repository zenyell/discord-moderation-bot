import os
import json
import urllib.request
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "modpanel-secret-key-change-me")

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "admin123")
GUILD_ID       = os.getenv("GUILD_ID", "0")
BOT_TOKEN      = os.getenv("DISCORD_BOT_TOKEN", "")

DISCORD_API = "https://discord.com/api/v10"

db.init_db()


def _discord_headers():
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def _fetch_discord_user(user_id: str) -> dict:
    """
    GET /users/{id} with a Bot token.
    NOTE: Discord's Bot-token user endpoint returns only basic fields.
    'banner', 'bio', and 'accent_color' are included ONLY when the user
    has set them AND the API decides to return them (not guaranteed for
    all users via bot token — OAuth2 'identify' scope is the reliable way).
    We fetch it anyway and use whatever Discord gives us.
    """
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/users/{user_id}",
            headers=_discord_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _fetch_guild_member(user_id: str) -> dict:
    """
    GET /guilds/{guild}/members/{user}
    This returns the member object which includes:
    - avatar (guild-specific avatar hash — overrides global)
    - banner (guild-specific banner hash — overrides global)
    - nick, roles, joined_at, etc.
    """
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


def _avatar_url(user_data: dict, member_data: dict) -> str:
    """
    Priority: guild-specific avatar > global avatar > default avatar.
    Guild avatar is inside member_data['avatar'].
    """
    uid = user_data.get("id", "")

    # Guild-specific avatar takes priority
    guild_av = member_data.get("avatar", "")
    if guild_av:
        ext = "gif" if guild_av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/guilds/{GUILD_ID}/users/{uid}/avatars/{guild_av}.{ext}?size=256"

    # Global avatar
    av = user_data.get("avatar", "")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{av}.{ext}?size=256"

    # Default avatar
    disc = user_data.get("discriminator", "0")
    idx  = (int(disc) % 5) if disc and disc != "0" else ((int(uid) >> 22) % 6) if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _banner_url(user_data: dict, member_data: dict) -> str:
    """
    Priority: guild-specific banner > global banner.
    Guild banner is inside member_data['banner'].
    Global banner is inside user_data['banner'].
    """
    uid = user_data.get("id", "")

    # Guild-specific banner
    guild_banner = member_data.get("banner", "") or ""
    if guild_banner:
        ext = "gif" if guild_banner.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/guilds/{GUILD_ID}/users/{uid}/banners/{guild_banner}.{ext}?size=1024"

    # Global banner
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


@app.route("/", methods=["GET"])
@login_required
def index():
    try:
        stats = db.log_stats(GUILD_ID)
    except Exception:
        stats = {"total": 0, "kicks": 0, "bans": 0, "timeouts": 0, "warnings": 0, "purges": 0}
    try:
        logs = db.recent_logs(GUILD_ID, 15)
    except Exception:
        logs = []
    return render_template("dashboard.html", stats=stats, logs=logs)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == DASHBOARD_USER and \
           request.form.get("password") == DASHBOARD_PASS:
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        db.set_setting("auto_role_id", request.form.get("auto_role_id", ""))
        db.set_setting("bad_words",    request.form.get("bad_words", ""))
        db.set_setting("spam_limit",   request.form.get("spam_limit", ""))
        db.set_setting("spam_window",  request.form.get("spam_window", ""))
        db.set_setting("spam_timeout", request.form.get("spam_timeout", ""))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id": db.get_setting("auto_role_id", ""),
        "bad_words":    db.get_setting("bad_words", os.getenv("BAD_WORDS", "")),
        "spam_limit":   db.get_setting("spam_limit",  os.getenv("SPAM_MESSAGE_LIMIT", "6")),
        "spam_window":  db.get_setting("spam_window", os.getenv("SPAM_WINDOW_SECONDS", "8")),
        "spam_timeout": db.get_setting("spam_timeout", os.getenv("SPAM_TIMEOUT_MINUTES", "5")),
        "client_id":    os.getenv("DISCORD_CLIENT_ID", ""),
        "redirect_uri": os.getenv("DISCORD_REDIRECT_URI", ""),
    }
    return render_template("settings.html", cfg=cfg)


@app.route("/moderation", methods=["GET", "POST"])
@login_required
def moderation():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_word":
            word = request.form.get("word", "").strip()
            if word:
                db.add_blacklisted_word(GUILD_ID, word, "dashboard")
                flash(f"Word '{word}' added to blacklist.")
        elif action == "remove_word":
            word = request.form.get("word", "").strip()
            db.remove_blacklisted_word(GUILD_ID, word)
            flash(f"Word '{word}' removed.")
        elif action == "add_user":
            uid    = request.form.get("user_id", "").strip()
            reason = request.form.get("reason", "").strip()
            if uid:
                db.add_blacklisted_user(GUILD_ID, uid, reason, "dashboard")
                flash(f"User {uid} blacklisted.")
        elif action == "remove_user":
            uid = request.form.get("user_id", "").strip()
            db.remove_blacklisted_user(GUILD_ID, uid)
            flash(f"User {uid} removed from blacklist.")
        return redirect(url_for("moderation"))
    words = db.get_blacklisted_words(GUILD_ID)
    users = db.get_blacklisted_users(GUILD_ID)
    return render_template("moderation.html", words=words, users=users)


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
            db.set_command_setting(
                GUILD_ID, cmd,
                enabled=enabled,
                whitelist_roles=whitelist_roles,
                blacklist_mods=blacklist_mods,
                whitelist_users=whitelist_users,
                blacklist_users=blacklist_users,
            )
        flash("Command settings saved. Changes take effect immediately on the bot.")
        return redirect(url_for("command_settings"))
    cmd_settings = db.get_command_settings(GUILD_ID)
    return render_template("command_settings.html", commands=db.ALL_COMMANDS, cmd_settings=cmd_settings)


@app.route("/user-lookup")
@login_required
def user_lookup():
    query   = request.args.get("q", "").strip()
    results = []
    if query:
        try:
            import sqlite3 as _sql
            with _sql.connect(os.getenv("DATABASE_PATH", "/tmp/bot_data.db")) as con:
                con.row_factory = _sql.Row
                results = con.execute(
                    "SELECT DISTINCT target_id FROM mod_logs WHERE guild_id=? AND (target_id LIKE ? OR target_id=?) LIMIT 20",
                    (GUILD_ID, f"%{query}%", query)
                ).fetchall()
        except Exception:
            results = []
    return render_template("user_lookup.html", query=query, results=results)


@app.route("/user-lookup/<user_id>")
@login_required
def user_profile(user_id):
    try:
        warns = db.get_warnings(GUILD_ID, user_id)
    except Exception:
        warns = []
    try:
        logs = db.logs_for_user(GUILD_ID, user_id)
    except Exception:
        logs = []
    bl = False
    try:
        bl = db.is_user_blacklisted(GUILD_ID, user_id)
    except Exception:
        pass

    user_data   = _fetch_discord_user(user_id)
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

    # Pass both user_data AND member_data so guild-specific assets take priority
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
    }

    try:
        db.cache_profile(
            user_id, profile["username"], profile["global_name"],
            avatar_url, banner_url, accent_color, bio,
            ", ".join(b[0] for b in badges)
        )
    except Exception:
        pass

    return render_template(
        "user_profile.html",
        user_id=user_id,
        profile=profile,
        warns=warns,
        logs=logs,
        blacklisted=bl
    )


@app.route("/api/profile/<user_id>")
@login_required
def api_profile(user_id):
    try:
        cached = db.get_cached_profile(user_id)
        if cached:
            return jsonify(cached)
    except Exception:
        pass
    return jsonify({"error": "not_found"}), 404


@app.route("/api/debug/<user_id>")
@login_required
def api_debug(user_id):
    """
    Debug route: returns raw Discord API responses for a user.
    Visit /api/debug/<user_id> in your browser to see exactly what
    Discord is sending back — useful for diagnosing missing banner/bio.
    REMOVE this route before deploying to production.
    """
    user_data   = _fetch_discord_user(user_id)
    member_data = _fetch_guild_member(user_id)
    return jsonify({
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
    db.init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
