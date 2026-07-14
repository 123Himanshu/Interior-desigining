from app.database import get_db
from app.services.auth import hash_password


def create_user(username: str, password: str, credits: int = 100, is_admin: bool = False) -> dict:
    pw_hash, salt = hash_password(password)
    conn = get_db()
    try:
        # First user in the system is always admin
        cnt = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        if cnt and cnt["cnt"] == 0:
            is_admin = True

        row = conn.execute(
            """INSERT INTO users (username, password_hash, salt, credits, is_admin)
               VALUES (?, ?, ?, ?, ?)
               RETURNING id, username, credits, is_admin""",
            (username, pw_hash, salt, credits, is_admin),
        ).fetchone()
        conn.commit()
        if not row:
            raise RuntimeError("Failed to create user")
        return {
            "id": row["id"],
            "username": row["username"],
            "credits": row["credits"],
            "is_admin": bool(row["is_admin"]),
        }
    finally:
        conn.close()


def get_user_credits(user_id: int) -> int:
    conn = get_db()
    try:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        return int(row["credits"]) if row else 0
    finally:
        conn.close()


def charge_credit(user_id: int) -> int | None:
    """Atomically deduct 1 credit. Returns new balance or None if insufficient."""
    conn = get_db()
    try:
        row = conn.execute(
            """UPDATE users SET credits = credits - 1
               WHERE id = ? AND credits > 0
               RETURNING credits""",
            (user_id,),
        ).fetchone()
        conn.commit()
        return int(row["credits"]) if row else None
    finally:
        conn.close()


def refund_credit(user_id: int):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET credits = credits + 1 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def set_credits(username: str, credits: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "UPDATE users SET credits = ? WHERE username = ? RETURNING username, credits",
            (credits, username),
        ).fetchone()
        conn.commit()
        if not row:
            return None
        return {"username": row["username"], "credits": int(row["credits"])}
    finally:
        conn.close()


def set_admin(username: str, is_admin: bool) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "UPDATE users SET is_admin = ? WHERE username = ? RETURNING username, is_admin",
            (is_admin, username),
        ).fetchone()
        conn.commit()
        if not row:
            return None
        return {"username": row["username"], "is_admin": bool(row["is_admin"])}
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT username, credits, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "username": r["username"],
            "credits": int(r["credits"]),
            "is_admin": bool(r["is_admin"]),
            "created_at": str(r["created_at"]) if r["created_at"] else None,
        }
        for r in rows
    ]


def user_exists(username: str) -> bool:
    conn = get_db()
    try:
        row = conn.execute("SELECT 1 AS ok FROM users WHERE username = ?", (username,)).fetchone()
        return row is not None
    finally:
        conn.close()
