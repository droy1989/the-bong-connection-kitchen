"""
Database layer for the food-stall app.

Supports two backends behind one uniform interface, selected by the
DATABASE_URL environment variable:

- DATABASE_URL unset  -> local SQLite file (DB_PATH), good for local dev.
- DATABASE_URL set to a postgres:// / postgresql:// URL -> AWS RDS Postgres.

app.py always calls `db()` and uses `?` placeholders exactly as it did with
plain sqlite3; this module translates everything for Postgres so the rest
of the app doesn't need per-backend branching.
"""
import os
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras

DB_PATH = os.getenv("DB_PATH", "foodstall.db")


def _to_pg(sql: str) -> str:
    """Translate sqlite-style '?' placeholders to psycopg2's '%s'."""
    return sql.replace("?", "%s")


class Connection:
    """Uniform wrapper around a sqlite3 or psycopg2 connection."""

    def __init__(self):
        self.is_postgres = IS_POSTGRES
        if IS_POSTGRES:
            # RealDictCursor gives dict-like rows (row["col"], dict(row)),
            # matching how sqlite3.Row is used throughout app.py.
            self._conn = psycopg2.connect(
                DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
            )
        else:
            db_dir = os.path.dirname(DB_PATH)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._conn = sqlite3.connect(DB_PATH)
            self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_to_pg(sql) if self.is_postgres else sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(_to_pg(sql) if self.is_postgres else sql, seq_of_params)
        return cur

    def executescript(self, script):
        """Run a multi-statement DDL block (no bound parameters)."""
        if self.is_postgres:
            cur = self._conn.cursor()
            cur.execute(script)
            return cur
        return self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db() -> Connection:
    return Connection()