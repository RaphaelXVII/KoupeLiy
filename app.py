from pathlib import Path
import os
import sqlite3
from uuid import uuid4

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from translations import LANGUAGES, MESSAGES

# Create the Flask application and configure where the SQLite file lives and
# how large an incoming request body is allowed to be (a small limit here since
# this app only accepts a short name + service selection).
app = Flask(__name__)
app.config["DATABASE"] = Path(app.instance_path) / "queue.sqlite3"
app.config["MAX_CONTENT_LENGTH"] = 4096

# Secret key signs the session cookie that remembers a logged-in barber.
# Set FLASK_SECRET_KEY in the environment for production; the fallback is only
# meant for local development.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-key-change-me")

# The barber signs in with a single shared password. We store only its hash,
# read from the BARBER_PASSWORD_HASH environment variable. If that isn't set we
# fall back to a dev password ("barber") so the app still runs locally.
BARBER_PASSWORD_HASH = os.environ.get(
    "BARBER_PASSWORD_HASH", generate_password_hash("barber")
)

# The services a customer is allowed to pick. Used to validate form input.
SERVICES = ("haircut", "beard", "combo")


def login_required(view):
    """Decorator that blocks a view unless the barber is logged in.

    If there's no `barber` flag in the session, redirect to the login page
    (remembering where they were headed via `?next=`) instead of running the
    view. Used to protect the dashboard and the "mark complete" action.
    """
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("barber"):
            return redirect(url_for("login", next=request.path, lang=g.lang))
        return view(*args, **kwargs)

    return wrapped


@app.template_filter("ordinal")
def ordinal(number):
    """Jinja filter: turn a plain number into its ordinal word form.

    Example: 1 -> "1st", 2 -> "2nd", 3 -> "3rd", 4 -> "4th".
    The 11/12/13 range is special-cased because those always take "th"
    (11th, 12th, 13th) even though they end in 1, 2, 3.
    Used in the templates to display queue positions.
    """
    number = int(number)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def database():
    """Return the request-scoped SQLite connection, creating it on first use.

    The connection is cached on Flask's `g` object so the same connection is
    reused for the whole request. On first access it also ensures the instance
    folder exists and creates the `tickets` table if it isn't there yet.
    Rows come back as dict-like `sqlite3.Row` objects.
    """
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
    """Close the SQLite connection at the end of each request.

    Flask calls this automatically when the application context tears down,
    so we never leak open database connections.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def select_language():
    """Decide which language to use for this request and store it on `g.lang`.

    Priority order:
      1. An explicit `?lang=` query parameter, if it's a supported language.
      2. Otherwise fall back to the browser's Accept-Language header, picking
         the first supported language it lists.
      3. Default to English if nothing matches.
    Runs before every request so all views and templates can read `g.lang`.
    """
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
    """Render a template with the shared context every page needs.

    Injects the translated message dictionary for the current language (`t`)
    and the list of available `languages`, then merges in any extra context
    passed by the caller. Keeps the individual views from repeating this setup.
    """
    return render_template(
        template, t=MESSAGES[g.lang], languages=LANGUAGES, **context
    )


@app.route("/", methods=["GET", "POST"])
def join():
    """Customer check-in page (the home route).

    GET  -> show the empty join form.
    POST -> validate the submitted name and service. On bad input, re-render
            the form with an error and a 400 status. On success, create a new
            ticket with a unique token and redirect the customer to their
            ticket page (303 so a refresh doesn't resubmit the form).
    """
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
    """Show a single customer's ticket and their current queue position.

    Looks up the ticket by its token and, in the same query, counts how many
    not-yet-completed tickets were created before it (`ahead`) — that number
    plus one is the customer's place in line. Returns 404 if the token is
    unknown.
    """
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


@app.route("/barber/login", methods=["GET", "POST"])
def login():
    """Barber login page.

    GET  -> show the password form.
    POST -> check the submitted password against the stored hash. On success,
            mark the session as logged in and redirect to the dashboard (or to
            the `next` page they were trying to reach). On failure, re-render
            the form with an error and a 401 status.
    """
    if session.get("barber"):
        return redirect(url_for("barber", lang=g.lang), code=303)

    if request.method == "POST":
        password = request.form.get("password", "")
        if check_password_hash(BARBER_PASSWORD_HASH, password):
            session["barber"] = True
            target = request.args.get("next") or url_for("barber", lang=g.lang)
            return redirect(target, code=303)
        return page("login.html", error=True), 401

    return page("login.html")


@app.post("/barber/logout")
def logout():
    """Log the barber out by clearing the session flag, then go to login."""
    session.pop("barber", None)
    return redirect(url_for("login", lang=g.lang), code=303)


@app.get("/barber")
@login_required
def barber():
    """Barber dashboard: list everyone still waiting, in arrival order.

    Protected by @login_required. Selects all tickets that haven't been
    completed yet, ordered by id (the order they joined), and hands them to
    the template to render the queue.
    """
    entries = database().execute("""
        SELECT token, name, service
        FROM tickets WHERE completed = 0 ORDER BY id
    """).fetchall()
    return page("barber.html", entries=entries)


@app.post("/barber/complete/<token>")
@login_required
def complete(token):
    """Mark a ticket as completed, then return to the barber dashboard.

    Protected by @login_required. Triggered when the barber presses "complete"
    on a customer. Flips the ticket's `completed` flag to 1 (which removes it
    from the waiting queue) and redirects back to /barber (303 to avoid
    resubmitting on refresh).
    """
    db = database()
    with db:
        db.execute("UPDATE tickets SET completed = 1 WHERE token = ?", (token,))
    return redirect(url_for("barber", lang=g.lang), code=303)


@app.after_request
def prevent_queue_caching(response):
    """Stop browsers from caching dynamic pages so positions stay current.

    Adds a `Cache-Control: no-store` header to every non-static response, so a
    customer or barber always sees the live queue state instead of a stale
    cached copy. Static assets (CSS) are left cacheable.
    """
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response
