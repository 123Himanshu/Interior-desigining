from app.database import get_db
from app.services.auth import hash_password


def create_user(username: str, password: str, credits: int = 100, is_admin: bool = False) -> dict:
    pw_hash, salt = hash_password(password)
    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, credits, is_admin) VALUES (?, ?, ?, ?, ?)",
        (username, pw_hash, salt, credits, is_admin),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    uid = row["id"]
    # First user auto-gets admin
    if uid == 1:
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = 1")
        conn.commit()
        is_admin = True
    conn.close()
    return {"id": uid, "username": username, "credits": credits, "is_admin": is_admin}


def get_user_credits(user_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else 0


def charge_credit(user_id: int) -> int | None:
    """Atomically deduct 1 credit. Returns new balance or None if insufficient."""
    conn = get_db()
    conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0", (user_id,))
    conn.commit()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else None


def refund_credit(user_id: int):
    conn = get_db()
    conn.execute("UPDATE users SET credits = credits + 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def deduct_credit(user_id: int) -> int | None:
    conn = get_db()
    conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0", (user_id,))
    conn.commit()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else None


def set_credits(username: str, credits: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("UPDATE users SET credits = ? WHERE id = ?", (credits, row["id"]))
    conn.commit()
    conn.close()
    return {"username": username, "credits": credits}


def set_admin(username: str, is_admin: bool) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (is_admin, row["id"]))
    conn.commit()
    conn.close()
    return {"username": username, "is_admin": is_admin}


def list_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT username, credits, is_admin, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [
        {
            "username": r["username"],
            "credits": r["credits"],
            "is_admin": bool(r["is_admin"]) if r["is_admin"] is not None else False,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def user_exists(username: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row is not None


def is_user_admin(user_id: int) -> bool:
    conn = get_db()
    row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row["is_admin"]) if row else False
