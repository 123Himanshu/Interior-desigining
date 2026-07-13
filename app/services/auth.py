import hashlib
import secrets
from app.database import get_db


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return pw_hash, salt


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    computed, _ = hash_password(password, salt)
    return secrets.compare_digest(computed, stored_hash)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    return token


def get_user_by_token(token: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT user_id FROM sessions WHERE token = ?", (token,)).fetchone()
    if not row:
        conn.close()
        return None
    user = conn.execute("SELECT id, username, credits, is_admin FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    conn.close()
    return dict(user) if user else None


def login_user(username: str, password: str) -> tuple[str | None, str, int, bool]:
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, password_hash, salt, credits, is_admin FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not user:
        conn.close()
        return None, "", 0, False
    if not verify_password(password, user["salt"], user["password_hash"]):
        conn.close()
        return None, "", 0, False
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))
    conn.commit()
    conn.close()
    return token, user["username"], user["credits"], bool(user["is_admin"])
