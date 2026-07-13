import sqlite3
from pathlib import Path
from app.config import DATABASE_URL

_USE_PG = bool(DATABASE_URL)
DB_PATH = Path("roomai.db")


class _PgConn:
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, params=()):
        self._cur = self._c.cursor()
        self._cur.execute(sql.replace("?", "%s"), params)
        self._lastrowid = None
        self._rowcount = self._cur.rowcount
        if "returning" in sql.lower():
            row = self._cur.fetchone()
            if row:
                self._lastrowid = row[0]
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row and self._cur.description:
            return dict(zip([d[0] for d in self._cur.description], row))
        return None

    def fetchall(self):
        rows = self._cur.fetchall()
        if rows and self._cur.description:
            cols = [d[0] for d in self._cur.description]
            return [dict(zip(cols, row)) for row in rows]
        return []

    @property
    def lastrowid(self):
        if hasattr(self, '_lastrowid') and self._lastrowid:
            return self._lastrowid
        if hasattr(self, '_cur'):
            try:
                self._cur.execute("SELECT lastval()")
                return self._cur.fetchone()[0]
            except Exception:
                pass
        return 0

    def commit(self):
        self._c.commit()

    def close(self):
        self._c.close()

    def cursor(self):
        return self._c.cursor()


def get_db():
    if _USE_PG:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            return _PgConn(conn)
        except Exception:
            pass
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    if _USE_PG:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, salt TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 100,
            created_at TIMESTAMP DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS library_items (
            id TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL, room_tag TEXT DEFAULT '', obj_tag TEXT DEFAULT '',
            image_b64 TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (id, user_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS library_seeded (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1))""")
    else:
        for stmt in [
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id))""",
            """CREATE TABLE IF NOT EXISTS library_items (
                id TEXT NOT NULL, user_id INTEGER NOT NULL,
                name TEXT NOT NULL, room_tag TEXT DEFAULT '', obj_tag TEXT DEFAULT '',
                image_b64 TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, user_id),
                FOREIGN KEY (user_id) REFERENCES users(id))""",
            """CREATE TABLE IF NOT EXISTS library_seeded (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1))""",
        ]:
            conn.execute(stmt)
    conn.commit()
    conn.close()
