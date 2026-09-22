"""
Tiny database adapter so app.py can run on SQLite (default -- no setup, a single
finance.db file next to the code) or Postgres (recommended for hosts with an
ephemeral filesystem, e.g. a free Render web service, where a local SQLite file
would be wiped on every redeploy/restart).

Switch by setting DATABASE_URL to a postgres:// URL. Nothing else in app.py changes:
this module normalizes placeholder style, upsert syntax, case-insensitive text
compare, "YYYY-MM" month extraction, and cursor.lastrowid/rowcount so the calling
code is one code path for both engines.
"""
import os
import re

DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# Imported unconditionally (not gated behind `if IS_PG`) so that `psycopg` is always
# a real module-level name -- even if something checks/sets DATABASE_URL after this
# module is first imported, or a test imports db.py without Postgres configured.
# psycopg[binary] is an optional extra (see requirements.txt); it's only required
# at connect() time when DATABASE_URL actually points at Postgres.
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


def connect(sqlite_path):
    if IS_PG:
        if psycopg is None:
            raise RuntimeError(
                "DATABASE_URL is set to a Postgres URL but the 'psycopg' package isn't "
                "installed. Run: pip install -r requirements.txt"
            )
        # Render/Heroku-style URLs sometimes use postgres://; psycopg wants postgresql://
        url = re.sub(r"^postgres://", "postgresql://", DATABASE_URL, count=1)
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        return PgConn(conn)
    import sqlite3

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return SqliteConn(conn)


def _qmark_to_pct_s(sql):
    """Translate '?' placeholders to psycopg's '%s' (naive but fine: our SQL has no literal '?')."""
    return sql.replace("?", "%s")


class SqliteConn:
    """Thin pass-through; SQLite already speaks the dialect app.py is written in."""

    engine = "sqlite"

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        return SqliteCursor(cur)

    def executescript(self, sql):
        self.conn.executescript(sql)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    # dialect helpers used by app.py so one query string works on both engines
    def collate_nocase(self):
        return "COLLATE NOCASE"

    def month_expr(self, column):
        return f"substr({column}, 1, 7)"

    def upsert_budget_sql(self):
        return (
            "INSERT INTO budgets (user_id, month, amount) VALUES (?,?,?) "
            "ON CONFLICT(user_id, month) DO UPDATE SET amount = excluded.amount"
        )

    def integrity_error(self):
        import sqlite3

        return sqlite3.IntegrityError


class SqliteCursor:
    def __init__(self, cur):
        self.cur = cur

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    @property
    def lastrowid(self):
        return self.cur.lastrowid

    @property
    def rowcount(self):
        return self.cur.rowcount


class PgConn:
    engine = "postgres"

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        sql = _qmark_to_pct_s(sql)
        # RETURNING id lets us recover lastrowid-equivalent behavior on Postgres
        if sql.strip().upper().startswith("INSERT INTO USERS") and "RETURNING" not in sql.upper():
            sql += " RETURNING id"
        elif sql.strip().upper().startswith("INSERT INTO TRANSACTIONS") and "RETURNING" not in sql.upper():
            sql += " RETURNING id"
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return PgCursor(cur)

    def executescript(self, sql):
        cur = self.conn.cursor()
        cur.execute(sql)
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def collate_nocase(self):
        return ""  # Postgres TEXT compares are already case-sensitive; app.py uses ILIKE instead where needed

    def month_expr(self, column):
        return f"substr({column}, 1, 7)"  # works the same on text-typed date columns in both engines

    def upsert_budget_sql(self):
        return (
            "INSERT INTO budgets (user_id, month, amount) VALUES (?,?,?) "
            "ON CONFLICT (user_id, month) DO UPDATE SET amount = excluded.amount"
        )

    def integrity_error(self):
        return psycopg.errors.UniqueViolation


class PgCursor:
    """Eagerly drains the result (if any) so callers can use it like sqlite3's cursor:
    fetchone()/fetchall() are safe to call even on a DELETE/UPDATE with no RETURNING.
    psycopg3 raises on fetch* when there's no result set at all, so we gate on
    cur.description (None for statements that return no rows) rather than try/except,
    since fetchone() after e.g. a bare UPDATE is expected, not exceptional, here."""

    def __init__(self, cur):
        self.cur = cur
        self._rowcount = cur.rowcount
        if cur.description is not None:
            self._rows = cur.fetchall()
        else:
            self._rows = []
        self._pos = 0

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    @property
    def lastrowid(self):
        return self._rows[0]["id"] if self._rows else None

    @property
    def rowcount(self):
        return self._rowcount


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('income','expense')),
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    note TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, date);
CREATE TABLE IF NOT EXISTS budgets (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (user_id, month)
);
"""

# Postgres: no COLLATE NOCASE (case-insensitive uniqueness done via a lower(username) unique index instead)
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (lower(username));
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('income','expense')),
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    note TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, date);
CREATE TABLE IF NOT EXISTS budgets (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (user_id, month)
);
"""
