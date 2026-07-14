import hashlib
import secrets
from datetime import datetime, timedelta, timezone
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
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    conn = get_db()
    try:
        # Clean expired sessions for this user (keeps table small)
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND expires_at < NOW()", (user_id,))
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_user_by_token(token: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT u.id, u.username, u.credits, u.is_admin
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > NOW()""",
            (token,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "credits": int(row["credits"]),
            "is_admin": bool(row["is_admin"]),
        }
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
        uid = user["id"]
        uname = user["username"]
        credits = int(user["credits"])
        is_admin = bool(user["is_admin"])
    finally:
        conn.close()
    token = create_session(uid)
    return token, uname, credits, is_admin


def logout_token(token: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()
