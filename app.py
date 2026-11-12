from pathlib import Path
import sqlite3
from uuid import uuid4

from flask import Flask, abort, g, redirect, render_template, request, url_for

from translations import LANGUAGES, MESSAGES

app = Flask(__name__)
app.config["DATABASE"] = Path(app.instance_path) / "queue.sqlite3"
app.config["MAX_CONTENT_LENGTH"] = 4096

SERVICES = ("haircut", "beard", "combo")


def database():
    if "db" not in g:
        path = Path(app.config["DATABASE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY,
                token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                service TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            )
        """)
    return g.db


@app.teardown_appcontext
def close_database(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def select_language():
    requested = request.args.get("lang")
    if requested in LANGUAGES:
        g.lang = requested
        return

    g.lang = "en"
    for locale, quality in request.accept_languages:
        language = locale.lower().split("-", 1)[0]
        if quality > 0 and language in LANGUAGES:
            g.lang = language
            break


def page(template, **context):
    return render_template(
        template, t=MESSAGES[g.lang], languages=LANGUAGES, **context
    )


@app.route("/", methods=["GET", "POST"])
def join():
    if request.method == "GET":
        return page("join.html", services=SERVICES)

    name = request.form.get("name", "").strip()
    service = request.form.get("service", "")
    if not 1 <= len(name) <= 60 or service not in SERVICES:
        return page("join.html", services=SERVICES, name=name, service=service, error=True), 400

    token = uuid4().hex
    db = database()
    with db:
        db.execute(
            "INSERT INTO tickets (token, name, service) VALUES (?, ?, ?)",
            (token, name, service),
        )
    return redirect(url_for("ticket", token=token, lang=g.lang), code=303)


@app.get("/ticket/<token>")
def ticket(token):
    entry = database().execute("""
        SELECT name, service, completed,
            (SELECT COUNT(*) FROM tickets AS earlier
             WHERE earlier.id < current.id AND earlier.completed = 0) AS ahead
        FROM tickets AS current
        WHERE token = ?
    """, (token,)).fetchone()
    if entry is None:
        abort(404)
    return page("ticket.html", entry=entry)


@app.get("/barber")
def barber():
    entries = database().execute("""
        SELECT token, name, service
        FROM tickets WHERE completed = 0 ORDER BY id
    """).fetchall()
    return page("barber.html", entries=entries)


@app.post("/barber/complete/<token>")
def complete(token):
    db = database()
    with db:
        db.execute("UPDATE tickets SET completed = 1 WHERE token = ?", (token,))
    return redirect(url_for("barber", lang=g.lang), code=303)


@app.after_request
def prevent_queue_caching(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response
