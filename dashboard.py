import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "admin123")
GUILD_ID       = os.getenv("GUILD_ID", "0")


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
    stats = db.log_stats(GUILD_ID)
    logs  = db.recent_logs(GUILD_ID, 15)
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
        db.set_setting("bad_words", request.form.get("bad_words", ""))
        db.set_setting("spam_limit", request.form.get("spam_limit", ""))
        db.set_setting("spam_window", request.form.get("spam_window", ""))
        db.set_setting("spam_timeout", request.form.get("spam_timeout", ""))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id": db.get_setting("auto_role_id", ""),
        "bad_words":    db.get_setting("bad_words", os.getenv("BAD_WORDS", "")),
        "spam_limit":   db.get_setting("spam_limit",  os.getenv("SPAM_MESSAGE_LIMIT", "6")),
        "spam_window":  db.get_setting("spam_window", os.getenv("SPAM_WINDOW_SECONDS", "8")),
        "spam_timeout": db.get_setting("spam_timeout",os.getenv("SPAM_TIMEOUT_MINUTES", "5")),
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
            blacklist_mods  = request.form.get(f"{cmd}_blacklist_mods", "").strip()
            db.set_command_setting(GUILD_ID, cmd,
                enabled=enabled,
                whitelist_roles=whitelist_roles,
                blacklist_mods=blacklist_mods
            )
        flash("Command settings saved. Changes take effect immediately.")
        return redirect(url_for("command_settings"))
    cmd_settings = db.get_command_settings(GUILD_ID)
    return render_template("command_settings.html", commands=db.ALL_COMMANDS, cmd_settings=cmd_settings)


@app.route("/user-lookup")
@login_required
def user_lookup():
    query   = request.args.get("q", "").strip()
    results = []
    if query:
        with __import__("sqlite3").connect(os.getenv("DATABASE_PATH", "bot_data.db")) as con:
            con.row_factory = __import__("sqlite3").Row
            results = con.execute(
                "SELECT DISTINCT target_id FROM mod_logs WHERE guild_id=? AND (target_id LIKE ? OR target_id=?) LIMIT 20",
                (GUILD_ID, f"%{query}%", query)
            ).fetchall()
    return render_template("user_lookup.html", query=query, results=results)


@app.route("/user-lookup/<user_id>")
@login_required
def user_profile(user_id):
    warns = db.get_warnings(GUILD_ID, user_id)
    logs  = db.logs_for_user(GUILD_ID, user_id)
    bl    = db.is_user_blacklisted(GUILD_ID, user_id)
    return render_template("user_profile.html", user_id=user_id, warns=warns, logs=logs, blacklisted=bl)


if __name__ == "__main__":
    db.init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
