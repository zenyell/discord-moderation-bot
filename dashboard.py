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
        db.set_setting("bad_words",    request.form.get("bad_words", ""))
        flash("Settings saved.")
        return redirect(url_for("settings"))
    cfg = {
        "auto_role_id":  db.get_setting("auto_role_id", os.getenv("AUTO_ROLE_ID", "")),
        "bad_words":     db.get_setting("bad_words",    os.getenv("BAD_WORDS", "")),
        "client_id":     os.getenv("DISCORD_CLIENT_ID", ""),
        "redirect_uri":  os.getenv("DISCORD_REDIRECT_URI", ""),
    }
    return render_template("settings.html", cfg=cfg)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
