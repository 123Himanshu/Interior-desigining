from app.database import get_db
from app.services.auth import hash_password


def create_user(username: str, password: str, credits: int = 100) -> dict:
    pw_hash, salt = hash_password(password)
    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, credits) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, credits),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return {"id": row["id"], "username": username, "credits": credits}


def get_user_credits(user_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else 0


def deduct_credit(user_id: int) -> int:
    conn = get_db()
    conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0", (user_id,))
    conn.commit()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else 0


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


def list_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT username, credits, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [{"username": r["username"], "credits": r["credits"], "created_at": r["created_at"]} for r in rows]


def user_exists(username: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row is not None
