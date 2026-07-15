"""Persistent metadata and output-file handling for generated images."""
import base64
import hashlib
import json
import uuid
from io import BytesIO

from PIL import Image

from app.database import get_db
from app.services.storage import StorageError, upload_image, signed_url, delete_image, is_configured


def _decode_image(image_b64: str) -> tuple[bytes, str, str, int, str]:
    try:
        raw = base64.b64decode(image_b64, validate=False)
        image = Image.open(BytesIO(raw))
        image.verify()
        image = Image.open(BytesIO(raw))
        mime = image.get_format_mimetype() or "image/png"
    except Exception as exc:
        raise StorageError(f"Generated output is not a valid image: {exc}") from exc
    return raw, mime, image.format.lower(), len(raw), hashlib.sha256(raw).hexdigest()


def save_generation(user_id: int, mode: str, prompt: str, object_tags: list, image_b64: str) -> dict:
    """Persist one output atomically enough to clean up Storage on DB failure."""
    if not is_configured():
        raise StorageError("Supabase Storage is not configured")
    raw, mime, extension, size, sha256 = _decode_image(image_b64)
    generation_id = str(uuid.uuid4())
    path = f"users/{user_id}/generations/{generation_id}/output.{extension}"
    uploaded = upload_image(path, raw, mime)
    try:
        output_url = signed_url(uploaded["path"])
    except Exception:
        try:
            delete_image(uploaded["path"])
        except Exception:
            pass
        raise
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO generations
               (id, user_id, mode, prompt, object_tags, output_path,
                output_mime, output_size_bytes, output_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                generation_id, user_id, mode, prompt,
                json.dumps(object_tags or [], ensure_ascii=True),
                uploaded["path"], mime, size, sha256,
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        finally:
            conn.close()
        try:
            delete_image(uploaded["path"])
        except Exception:
            pass
        raise
    else:
        conn.close()
    return {
        "id": generation_id,
        "output_url": output_url,
        "format": extension.lstrip("."),
    }


def list_generations(user_id: int, limit: int = 24, mode: str | None = None, cursor: str | None = None) -> tuple[list[dict], str | None]:
    limit = max(1, min(50, int(limit)))
    conn = get_db()
    try:
        params = [user_id]
        where = "WHERE user_id = ?"
        if mode:
            where += " AND mode = ?"
            params.append(mode)
        if cursor:
            where += " AND (created_at, id) < (SELECT created_at, id FROM generations WHERE id = ? AND user_id = ?)"
            params.extend([cursor, user_id])
        params.append(limit + 1)
        rows = conn.execute(
            f"""SELECT id, mode, prompt, object_tags, output_path,
                       output_mime, created_at
                FROM generations {where}
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        try:
            tags = json.loads(row["object_tags"] or "[]")
        except Exception:
            tags = []
        items.append({
            "id": row["id"],
            "mode": row["mode"],
            "prompt": row["prompt"],
            "object_tags": tags,
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            "output_url": signed_url(row["output_path"]),
            "format": row["output_mime"].split("/", 1)[-1],
        })
    return items, (items[-1]["id"] if has_more and items else None)


def get_generation(user_id: int, generation_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, mode, prompt, object_tags, output_path, output_mime, created_at FROM generations WHERE id = ? AND user_id = ?",
            (generation_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        tags = json.loads(row["object_tags"] or "[]")
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "mode": row["mode"],
        "prompt": row["prompt"],
        "object_tags": tags,
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        "output_url": signed_url(row["output_path"]),
        "format": row["output_mime"].split("/", 1)[-1],
    }


def delete_generation(user_id: int, generation_id: str) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT output_path FROM generations WHERE id = ? AND user_id = ?",
            (generation_id, user_id),
        ).fetchone()
        if not row:
            return False
        delete_image(row["output_path"])
        conn.execute("DELETE FROM generations WHERE id = ? AND user_id = ?", (generation_id, user_id))
        conn.commit()
    finally:
        conn.close()
    return True
