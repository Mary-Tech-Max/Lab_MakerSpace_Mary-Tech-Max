"""
database.py is the only file that talks to SQLite directly.

Everything else in the project asks this module for rows and never opens a
connection itself. That keeps SQL in one place and makes it easy to explain
during the demo: "models hold behaviour, database.py holds storage".
"""

import sqlite3
from contextlib import contextmanager

# The schema is created on first run, so the repository needs no separate .sql step.
# Three core tables + a small `categories` lookup so category names are stored
# once instead of being repeated on every equipment row (normalisation).
SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    email     TEXT    NOT NULL UNIQUE,
    joined_on TEXT    NOT NULL,                 -- ISO date, e.g. 2026-09-21
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS equipment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    status      TEXT    NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'on_loan', 'maintenance', 'retired')),
    note        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS loans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER NOT NULL REFERENCES members(id),
    equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    loaned_on    TEXT    NOT NULL,
    due_on       TEXT    NOT NULL,
    returned_on  TEXT,                           -- NULL means "still out"
    renewals     INTEGER NOT NULL DEFAULT 0
);

-- Speeds up the two lookups the reports hammer: "open loans" and "who has what".
CREATE INDEX IF NOT EXISTS idx_loans_open   ON loans(returned_on);
CREATE INDEX IF NOT EXISTS idx_loans_member ON loans(member_id);
"""


class Database:
    """Thin wrapper around one SQLite connection."""

    def __init__(self, path="makerspace.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        # Row objects let us read columns by name: row["email"].
        self.conn.row_factory = sqlite3.Row
        # SQLite ignores foreign keys unless you switch them on per connection.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """
        Tiny schema upgrade: databases created before renewals existed lack the
        column. CREATE TABLE IF NOT EXISTS won't add it, so we check and ALTER.
        """
        columns = [row["name"] for row in self.query("PRAGMA table_info(loans)")]
        if "renewals" not in columns:
            with self.transaction() as conn:
                conn.execute("ALTER TABLE loans ADD COLUMN renewals INTEGER NOT NULL DEFAULT 0")

    # ---- simple helpers -------------------------------------------------
    def query(self, sql, params=()):
        """Run a SELECT and return every row."""
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        """Run a SELECT and return the first row (or None)."""
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql, params=()):
        """Run a single INSERT/UPDATE/DELETE, commit, return the new row id."""
        with self.transaction() as conn:
            return conn.execute(sql, params).lastrowid

    # ---- multi-step safety ---------------------------------------------
    @contextmanager
    def transaction(self):
        """
        All-or-nothing block. A checkout must insert a loan AND flip the
        equipment status; if either fails, neither should stick.
        """
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()
