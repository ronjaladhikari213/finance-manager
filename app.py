"""
Personal Finance Manager - web/mobile version.

Backend: Flask + SQLite by default (stdlib, no DB server needed, great for
running on your own computer or phone-on-same-Wi-Fi). Set DATABASE_URL to a
postgres:// URL to use Postgres instead -- recommended on hosts with an
ephemeral filesystem (e.g. a free Render web service), where a local SQLite
file gets wiped on every redeploy. See db.py for the small adapter that makes
the rest of this file identical either way, and README.md for setup.

Every user has a private account; all queries are scoped by user_id.
"""
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, g, jsonify, render_template, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import db as dbmod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FINANCE_DB", os.path.join(BASE_DIR, "finance.db"))
KEY_PATH = os.path.join(BASE_DIR, ".secret_key")

app = Flask(__name__)


def _load_secret_key():
    """Use env var if set, otherwise generate once and persist so sessions survive restarts."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(KEY_PATH, "w") as f:
        f.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


app.config.update(
    SECRET_KEY=_load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=60),
    MAX_CONTENT_LENGTH=64 * 1024,
)

CURRENCY = os.environ.get("CURRENCY", "Rs")  # e.g. Rs, NPR, $, €, ₹

DEFAULT_CATEGORIES = {
    "expense": ["Food", "Transportation", "Entertainment", "Others"],
    "income": ["Salary", "Allowance", "Freelance", "Others"],
}


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = dbmod.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = dbmod.connect(DB_PATH)
    db.executescript(dbmod.SCHEMA_PG if dbmod.IS_PG else dbmod.SCHEMA_SQLITE)
    db.close()


# ---------------------------------------------------------------- helpers
def err(message, status=400):
    return jsonify({"error": message}), status


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return err("Not logged in", 401)
        return fn(*args, **kwargs)

    return wrapper


def csrf_protect(fn):
    """Reject state-changing requests that lack the per-session CSRF token."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            sent = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not secrets.compare_digest(sent, expected):
                return err("Invalid CSRF token", 403)
        return fn(*args, **kwargs)

    return wrapper


def parse_amount(raw):
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number")
    if val != val or val in (float("inf"), float("-inf")):
        raise ValueError("Amount must be a valid number")
    if val <= 0:
        raise ValueError("Amount must be greater than 0")
    if val > 1e12:
        raise ValueError("Amount is too large")
    return round(val, 2)


def parse_date(raw):
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError("Date must be YYYY-MM-DD")


def parse_month(raw):
    if not raw:
        return datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
        raise ValueError("Month must be YYYY-MM")
    return raw


def clean_category(raw):
    cat = " ".join((raw or "").split())  # collapse whitespace
    if not cat:
        raise ValueError("Category is required")
    if len(cat) > 40:
        raise ValueError("Category is too long (max 40 chars)")
    return cat.title()  # 'food' / 'FOOD' / 'Food' all become 'Food' (fixes case-sensitivity bug)


def tx_row(r):
    return {
        "id": r["id"],
        "type": r["type"],
        "category": r["category"],
        "amount": r["amount"],
        "note": r["note"],
        "date": r["date"],
    }


# ---------------------------------------------------------------- pages / PWA
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- auth
@app.route("/api/session")
def api_session():
    if "user_id" not in session:
        return jsonify({"authenticated": False, "currency": CURRENCY})
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return jsonify({"authenticated": True, "username": session["username"], "csrf": session["csrf"], "currency": CURRENCY})


def _start_session(user_id, username):
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["username"] = username
    session["csrf"] = secrets.token_hex(16)


@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
        return err("Username must be 3-30 chars: letters, numbers, _ . -")
    if len(password) < 6:
        return err("Password must be at least 6 characters")
    if len(password) > 200:
        return err("Password is too long")
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        db.commit()
    except db.integrity_error():
        return err("That username is already taken", 409)
    _start_session(cur.lastrowid, username)
    return jsonify({"username": username, "csrf": session["csrf"], "currency": CURRENCY}), 201


# Basic brute-force throttle: 8 failures per (ip, username) per 5 minutes (in-memory).
_FAILS = {}
_WINDOW, _MAX_FAILS = 300, 8


def _throttled(key):
    now = datetime.now().timestamp()
    _FAILS[key] = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
    return len(_FAILS[key]) >= _MAX_FAILS


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    key = (request.remote_addr, username.lower())
    if _throttled(key):
        return err("Too many attempts. Try again in a few minutes.", 429)
    db = get_db()
    # case-insensitive lookup on both engines: SQLite's column is COLLATE NOCASE already;
    # on Postgres we compare lower() explicitly since the column itself is case-sensitive.
    row = (
        db.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
        if db.engine == "postgres"
        else db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    )
    if not row or not check_password_hash(row["password_hash"], password):
        _FAILS.setdefault(key, []).append(datetime.now().timestamp())
        return err("Wrong username or password", 401)
    _FAILS.pop(key, None)
    _start_session(row["id"], row["username"])
    return jsonify({"username": row["username"], "csrf": session["csrf"], "currency": CURRENCY})


@app.route("/api/logout", methods=["POST"])
@csrf_protect
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- transactions
@app.route("/api/transactions", methods=["GET"])
@login_required
def list_transactions():
    """Feature 3 (view) + Feature 7 (search). All filters optional."""
    q = request.args
    db = get_db()
    sql = "SELECT * FROM transactions WHERE user_id = ?"
    params = [session["user_id"]]

    if q.get("type") in ("income", "expense"):
        sql += " AND type = ?"
        params.append(q["type"])
    if q.get("category"):
        if db.engine == "postgres":
            sql += " AND lower(category) = lower(?)"
        else:
            sql += " AND category = ? COLLATE NOCASE"
        params.append(" ".join(q["category"].split()))
    if q.get("month"):
        try:
            sql += f" AND {db.month_expr('date')} = ?"
            params.append(parse_month(q["month"]))
        except ValueError as e:
            return err(str(e))
    if q.get("text"):
        like = "%" + q["text"].strip().replace("%", r"\%").replace("_", r"\_") + "%"
        like_op = "ILIKE" if db.engine == "postgres" else "LIKE"
        sql += rf" AND (category {like_op} ? ESCAPE '\' OR note {like_op} ? ESCAPE '\')"
        params += [like, like]
    for key, op in (("min", ">="), ("max", "<=")):
        if q.get(key):
            try:
                sql += f" AND amount {op} ?"
                params.append(float(q[key]))
            except ValueError:
                return err(f"'{key}' must be a number")
    if q.get("from"):
        sql += " AND date >= ?"
        params.append(q["from"])
    if q.get("to"):
        sql += " AND date <= ?"
        params.append(q["to"])

    sql += " ORDER BY date DESC, id DESC LIMIT 500"
    rows = db.execute(sql, params).fetchall()
    return jsonify([tx_row(r) for r in rows])


@app.route("/api/transactions", methods=["POST"])
@login_required
@csrf_protect
def add_transaction():
    """Features 1 & 2 (add income / add expense)."""
    data = request.get_json(silent=True) or {}
    if data.get("type") not in ("income", "expense"):
        return err("Type must be 'income' or 'expense'")
    try:
        amount = parse_amount(data.get("amount"))
        category = clean_category(data.get("category"))
        date = parse_date(data.get("date"))
    except ValueError as e:
        return err(str(e))
    note = (data.get("note") or "").strip()[:200]

    db = get_db()
    cur = db.execute(
        "INSERT INTO transactions (user_id, type, category, amount, note, date) VALUES (?,?,?,?,?,?)",
        (session["user_id"], data["type"], category, amount, note, date),
    )
    db.commit()
    row = db.execute("SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(tx_row(row)), 201


@app.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_transaction(tx_id):
    """Feature 8. Deletes exactly ONE transaction (the original deleted all matches)."""
    db = get_db()
    cur = db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, session["user_id"]))
    db.commit()
    if cur.rowcount == 0:
        return err("Transaction not found", 404)
    return jsonify({"ok": True})


@app.route("/api/categories")
@login_required
def categories():
    """Category suggestions: defaults + anything the user has used before."""
    rows = get_db().execute(
        "SELECT DISTINCT type, category FROM transactions WHERE user_id = ?", (session["user_id"],)
    ).fetchall()
    out = {t: list(v) for t, v in DEFAULT_CATEGORIES.items()}
    for r in rows:
        if r["category"] not in out[r["type"]]:
            out[r["type"]].append(r["category"])
    return jsonify(out)


# ---------------------------------------------------------------- budget
@app.route("/api/budget", methods=["GET"])
@login_required
def get_budget():
    month = parse_month(request.args.get("month"))
    row = get_db().execute(
        "SELECT amount FROM budgets WHERE user_id = ? AND month = ?", (session["user_id"], month)
    ).fetchone()
    return jsonify({"month": month, "amount": row["amount"] if row else 0})


@app.route("/api/budget", methods=["PUT"])
@login_required
@csrf_protect
def set_budget():
    """Feature 4."""
    data = request.get_json(silent=True) or {}
    try:
        month = parse_month(data.get("month"))
        amount = float(data.get("amount"))
        if amount < 0 or amount > 1e12 or amount != amount:
            raise ValueError
    except (TypeError, ValueError):
        return err("Budget must be a number (0 or more)")
    db = get_db()
    db.execute(
        db.upsert_budget_sql(),
        (session["user_id"], month, round(amount, 2)),
    )
    db.commit()
    return jsonify({"month": month, "amount": round(amount, 2)})


# ---------------------------------------------------------------- reports
def _month_totals(user_id, month):
    db = get_db()
    rows = db.execute(
        "SELECT type, category, SUM(amount) AS total, COUNT(*) AS n FROM transactions "
        f"WHERE user_id = ? AND {db.month_expr('date')} = ? GROUP BY type, category ORDER BY total DESC",
        (user_id, month),
    ).fetchall()
    income = sum(r["total"] for r in rows if r["type"] == "income")
    expenses = sum(r["total"] for r in rows if r["type"] == "expense")
    return rows, income, expenses


@app.route("/api/categories/summary")
@login_required
def category_summary():
    """Feature 5: spending by category (all categories, not just 4 hard-coded ones)."""
    try:
        month = parse_month(request.args.get("month"))
    except ValueError as e:
        return err(str(e))
    rows, _inc, expenses = _month_totals(session["user_id"], month)
    cats = [
        {
            "category": r["category"],
            "total": round(r["total"], 2),
            "count": r["n"],
            "percent": round(r["total"] / expenses * 100, 1) if expenses else 0,
        }
        for r in rows
        if r["type"] == "expense"
    ]
    return jsonify({"month": month, "total_expenses": round(expenses, 2), "categories": cats})


@app.route("/api/summary")
@login_required
def summary():
    """Feature 6: financial summary. Safe against zero income / no budget (original crashed)."""
    try:
        month = parse_month(request.args.get("month"))
    except ValueError as e:
        return err(str(e))
    uid = session["user_id"]
    rows, income, expenses = _month_totals(uid, month)
    b = get_db().execute("SELECT amount FROM budgets WHERE user_id = ? AND month = ?", (uid, month)).fetchone()
    budget = b["amount"] if b else 0

    remaining = income - expenses
    saving_rate = round((income - expenses) / income * 100, 1) if income > 0 else None
    out = {
        "month": month,
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "remaining": round(remaining, 2),
        "saving_rate": saving_rate,
        "budget": round(budget, 2),
        "budget_remaining": round(budget - expenses, 2) if budget > 0 else None,
        "budget_used_percent": round(expenses / budget * 100, 1) if budget > 0 else None,
        "over_budget": bool(budget > 0 and expenses > budget),
        "categories": [
            {"category": r["category"], "total": round(r["total"], 2)} for r in rows if r["type"] == "expense"
        ],
    }
    return jsonify(out)


@app.route("/api/export")
@login_required
def export_csv():
    """Bonus: download your data (useful as a backup)."""
    import csv
    import io

    rows = get_db().execute(
        "SELECT date, type, category, amount, note FROM transactions WHERE user_id = ? ORDER BY date, id",
        (session["user_id"],),
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "type", "category", "amount", "note"])
    for r in rows:
        w.writerow([r["date"], r["type"], r["category"], r["amount"], r["note"]])
    return app.response_class(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
