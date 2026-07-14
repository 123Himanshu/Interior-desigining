import sqlite3
import sys
from pathlib import Path
from app.config import DATABASE_URL

_USE_PG = bool(DATABASE_URL)
_pg_failed = False
DB_PATH = Path("/data/roomai.db") if Path("/data").exists() else Path("roomai.db")

try:
    import psycopg2.extras
    _HAVE_PSYCOPG2_EXTRAS = True
except ImportError:
    _HAVE_PSYCOPG2_EXTRAS = False


def _sql(pg: str, sqlite: str) -> str:
    return pg if _USE_PG else sqlite


class _PgConn:
    def __init__(self, conn):
        self._c = conn
        self._cur = None

    def execute(self, sql, params=()):
        if self._cur:
            try:
                self._cur.close()
            except Exception:
                pass
        if _HAVE_PSYCOPG2_EXTRAS:
            self._cur = self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            self._cur = self._c.cursor()
        self._cur.execute(sql.replace("?", "%s"), params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        try:
            return dict(row) if row else None
        except Exception:
            if row and self._cur.description:
                return dict(zip([d[0] for d in self._cur.description], row))
            return None

    def fetchall(self):
        rows = self._cur.fetchall()
        try:
            return [dict(r) for r in rows] if rows else []
        except Exception:
            if rows and self._cur.description:
                cols = [d[0] for d in self._cur.description]
                return [dict(zip(cols, row)) for row in rows]
            return []

    @property
    def rowcount(self):
        return self._cur.rowcount

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        if self._cur:
            try:
                self._cur.close()
            except Exception:
                pass
        self._c.close()


def get_db():
    global _pg_failed
    if _USE_PG and not _pg_failed:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            return _PgConn(conn)
        except Exception as e:
            _pg_failed = True
            print(f"[WARN] PostgreSQL connection failed ({e}), falling back to SQLite", file=sys.stderr)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    try:
        if _USE_PG and isinstance(conn, _PgConn):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            try:
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
            except Exception:
                pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '30 days'),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_items (
                    id TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL,
                    room_tag TEXT DEFAULT '',
                    obj_tag TEXT DEFAULT '',
                    image_b64 TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (id, user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS library_seeded (
                    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1)
                )
            """)
        else:
            for stmt in [
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            ]:
                conn.execute(stmt)
            try:
                conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            for stmt in [
                """CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TIMESTAMP NOT NULL DEFAULT (datetime('now', '+30 days')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id))""",
                """CREATE TABLE IF NOT EXISTS library_items (
                    id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    room_tag TEXT DEFAULT '',
                    obj_tag TEXT DEFAULT '',
                    image_b64 TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id, user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id))""",
                """CREATE TABLE IF NOT EXISTS library_seeded (
                    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1))""",
            ]:
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
