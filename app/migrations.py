"""Small, idempotent PostgreSQL schema migrations for the application."""


def _applied(conn, version: int) -> bool:
    row = conn.execute(
        "SELECT 1 AS applied FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    return bool(row)


def _mark(conn, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (version) VALUES (?) ON CONFLICT (version) DO NOTHING",
        (version,),
    )


def run_migrations(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               applied_at TIMESTAMP NOT NULL DEFAULT NOW()
           )"""
    )

    if not _applied(conn, 1):
        conn.execute("ALTER TABLE library_items ADD COLUMN IF NOT EXISTS image_path TEXT")
        conn.execute("ALTER TABLE library_items ADD COLUMN IF NOT EXISTS image_mime TEXT")
        conn.execute("ALTER TABLE library_items ADD COLUMN IF NOT EXISTS image_size_bytes INTEGER")
        conn.execute("ALTER TABLE library_items ADD COLUMN IF NOT EXISTS image_sha256 TEXT")
        conn.execute("ALTER TABLE library_items ALTER COLUMN image_b64 DROP NOT NULL")
        _mark(conn, 1)

    if not _applied(conn, 2):
        conn.execute(
            """CREATE TABLE IF NOT EXISTS generations (
                   id TEXT PRIMARY KEY,
                   user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                   mode TEXT NOT NULL,
                   prompt TEXT NOT NULL,
                   object_tags TEXT NOT NULL DEFAULT '[]',
                   output_path TEXT NOT NULL,
                   output_mime TEXT NOT NULL,
                   output_size_bytes INTEGER NOT NULL,
                   output_sha256 TEXT NOT NULL,
                   created_at TIMESTAMP DEFAULT NOW()
               )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_generations_user_created
               ON generations(user_id, created_at DESC, id DESC)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_generations_user_mode_created
               ON generations(user_id, mode, created_at DESC, id DESC)"""
        )
        _mark(conn, 2)
