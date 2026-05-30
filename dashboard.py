import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123")
GUILD_ID = os.getenv("GUILD_ID", "000000000000000000")

db.init_db()


def logged_in():
    return session.get("logged_in")


@app.route("/", methods=["GET", "POST"])
def login():
    if logged_in():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if request.form.get("username") == USERNAME and request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if not logged_in():
        return redirect(url_for("login"))
    logs    = db.recent_logs(GUILD_ID, 50)
    summary = db.log_summary(GUILD_ID)
    return render_template("dashboard.html", logs=logs, summary=summary)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not logged_in():
        return redirect(url_for("login"))
    if request.method == "POST":
        db.set_setting("auto_role_id", request.form.get("auto_role_id", ""))
        db.set_setting("spam_limit",   request.form.get("spam_limit", "6"))
        db.set_setting("spam_window",  request.form.get("spam_window", "8"))
        db.set_setting("spam_timeout", request.form.get("spam_timeout", "5"))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id":  db.get_setting("auto_role_id", os.getenv("AUTO_ROLE_ID", "")),
        "spam_limit":    db.get_setting("spam_limit",   os.getenv("SPAM_MESSAGE_LIMIT", "6")),
        "spam_window":   db.get_setting("spam_window",  os.getenv("SPAM_WINDOW_SECONDS", "8")),
        "spam_timeout":  db.get_setting("spam_timeout", os.getenv("SPAM_TIMEOUT_MINUTES", "5")),
        "client_id":     os.getenv("DISCORD_CLIENT_ID", ""),
        "redirect_uri":  os.getenv("DISCORD_REDIRECT_URI", ""),
    }
    return render_template("settings.html", cfg=cfg)


@app.route("/moderation", methods=["GET", "POST"])
def moderation():
    if not logged_in():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_word":
            word = request.form.get("word", "").strip().lower()
            if word:
                db.add_blacklisted_word(GUILD_ID, word, "dashboard")
                flash(f'Word "{word}" added to blacklist.')

        elif action == "remove_word":
            word = request.form.get("word", "").strip()
            db.remove_blacklisted_word(GUILD_ID, word)
            flash(f'Word "{word}" removed.')

        elif action == "add_user":
            user_id = request.form.get("user_id", "").strip()
            reason  = request.form.get("reason", "").strip() or "Blacklisted via dashboard"
            if user_id:
                db.add_blacklisted_user(GUILD_ID, user_id, reason, "dashboard")
                flash(f"User {user_id} blacklisted.")

        elif action == "remove_user":
            user_id = request.form.get("user_id", "").strip()
            db.remove_blacklisted_user(GUILD_ID, user_id)
            flash(f"User {user_id} removed from blacklist.")

        elif action == "delete_warning":
            warning_id = request.form.get("warning_id")
            db.delete_warning(warning_id)
            flash("Warning deleted.")

        return redirect(url_for("moderation"))

    bl_words = db.get_blacklisted_words(GUILD_ID)
    bl_users = db.get_blacklisted_users(GUILD_ID)
    return render_template("moderation.html", bl_words=bl_words, bl_users=bl_users)


@app.route("/user-lookup", methods=["GET", "POST"])
def user_lookup():
    if not logged_in():
        return redirect(url_for("login"))
    result = None
    if request.method == "POST":
        uid = request.form.get("user_id", "").strip()
        if uid:
            warns = db.get_warnings(GUILD_ID, uid)
            logs  = db.logs_for_user(GUILD_ID, uid)
            blacklisted = db.is_user_blacklisted(GUILD_ID, uid)
            result = {"user_id": uid, "warnings": warns, "logs": logs, "blacklisted": blacklisted}
    return render_template("user_lookup.html", result=result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
