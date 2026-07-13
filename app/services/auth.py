import hashlib
import secrets
from datetime import datetime, timedelta
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
    expires = (datetime.utcnow() + timedelta(days=30)).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_user_by_token(token: str) -> dict | None:
    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now),
        ).fetchone()
        if not row:
            return None
        user = conn.execute(
            "SELECT id, username, credits, is_admin FROM users WHERE id = ?",
            (row["user_id"],),
        ).fetchone()
        return dict(user) if user else None
    finally:
        conn.close()


def login_user(username: str, password: str) -> tuple[str | None, str, int, bool]:
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, username, password_hash, salt, credits, is_admin FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not user:
            return None, "", 0, False
        if not verify_password(password, user["salt"], user["password_hash"]):
            return None, "", 0, False
    finally:
        conn.close()
    token = create_session(user["id"])
    return token, user["username"], user["credits"], bool(user["is_admin"])
