"""PostgreSQL-only database layer via Supabase (psycopg2)."""
import sys

from app.config import DATABASE_URL
from app.migrations import run_migrations

_pool = None


class DatabaseError(RuntimeError):
    pass


_pool_failed = False


def _require_url():
    if not DATABASE_URL:
        raise DatabaseError(
            "DATABASE_URL is not set. Add your Supabase connection string to .env / HF secrets."
        )


def _get_pool():
    global _pool, _pool_failed
    if _pool is not None:
        return _pool
    if _pool_failed:
        raise DatabaseError("PostgreSQL pool previously failed to connect. Restart required.")
    _require_url()
    try:
        from psycopg2 import pool as pg_pool
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            connect_timeout=10,
        )
        return _pool
    except Exception as e:
        _pool_failed = True
        raise DatabaseError(f"Failed to connect to PostgreSQL: {e}") from e


class PgConn:
    """Thin wrapper so app code can use ? placeholders and dict rows."""

    def __init__(self, raw):
        import psycopg2.extras
        self._raw = raw
        self._cur = raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        # Convert SQLite-style ? to psycopg2 %s (only outside string literals is ideal;
        # our SQL is fully controlled so simple replace is safe).
        self._cur.execute(sql.replace("?", "%s"), params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        rows = self._cur.fetchall()
        return [dict(r) for r in rows] if rows else []

    @property
    def rowcount(self):
        return self._cur.rowcount

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            _get_pool().putconn(self._raw)
        except Exception:
            try:
                self._raw.close()
            except Exception:
                pass


def get_db() -> PgConn:
    raw = _get_pool().getconn()
    return PgConn(raw)


def init_db():
    """Create all tables on Supabase if they do not exist."""
    conn = get_db()
    try:
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
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '30 days'),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS library_items (
                id TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                room_tag TEXT DEFAULT '',
                obj_tag TEXT DEFAULT '',
                image_b64 TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (id, user_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_library_user_id ON library_items(user_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS library_seeded (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1)
            )
        """)
        run_migrations(conn)
        conn.commit()
        print("[DB] PostgreSQL tables ready", file=sys.stderr)
    finally:
        conn.close()


def health_check() -> dict:
    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            return {
                "db": "postgresql",
                "db_ok": True,
                "users": row["cnt"] if row else 0,
            }
        finally:
            conn.close()
    except Exception as e:
        return {"db": "postgresql", "db_ok": False, "db_error": str(e)[:200]}
