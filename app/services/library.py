import json as _json
from app.database import get_db
from app.config import HF_TOKEN, HF_DATASET_REPO, SEED_USERNAME
from app.services.storage import upload_image, signed_url, delete_image, is_configured, validate_image_bytes

LIBRARY_FILE = "library.json"
_hf_enabled = bool(HF_TOKEN and HF_DATASET_REPO)


def _hf_load_assets() -> list:
    if not _hf_enabled:
        return []
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=HF_DATASET_REPO, filename=LIBRARY_FILE,
            repo_type="dataset", token=HF_TOKEN,
        )
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


_UPSERT = """
INSERT INTO library_items (id, user_id, name, room_tag, obj_tag, image_b64)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (id, user_id) DO UPDATE SET
  name = EXCLUDED.name,
  room_tag = EXCLUDED.room_tag,
  obj_tag = EXCLUDED.obj_tag,
  image_b64 = EXCLUDED.image_b64
"""


def seed_library(user_id: int, username: str):
    """Seed HF library assets only for SEED_USERNAME, once."""
    if not _hf_enabled or username != SEED_USERNAME:
        return
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM library_seeded LIMIT 1").fetchone()
        if row:
            return
        assets = _hf_load_assets()
        for idx, a in enumerate(assets):
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or f"hf_seed_{idx}")
            conn.execute(
                _UPSERT,
                (
                    aid, user_id,
                    a.get("name", ""),
                    a.get("room_tag", a.get("roomTag", "")),
                    a.get("obj_tag", a.get("objectTag", "")),
                    a.get("image_b64", a.get("dataUrl", "")),
                ),
            )
        conn.execute(
            "INSERT INTO library_seeded (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def get_user_library(user_id: int) -> list[dict]:
    migrate_legacy_items(user_id)
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, name,
                       room_tag AS "roomTag",
                       obj_tag AS "objectTag",
                       image_b64 AS "legacyDataUrl",
                       image_path, image_mime,
                       created_at AS "createdAt"
               FROM library_items
               WHERE user_id = ?
               ORDER BY created_at""",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        item = dict(row)
        if item.get("image_path"):
            item["dataUrl"] = signed_url(item["image_path"])
        else:
            item["dataUrl"] = item.get("legacyDataUrl", "")
        if hasattr(item.get("createdAt"), "isoformat"):
            item["createdAt"] = item["createdAt"].isoformat()
        item.pop("legacyDataUrl", None)
        item.pop("image_path", None)
        item.pop("image_mime", None)
        result.append(item)
    return result


def migrate_legacy_items(user_id: int):
    """Move old base64 assets to Storage lazily and safely per user."""
    if not is_configured():
        return
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, image_b64 FROM library_items WHERE user_id = ? AND image_path IS NULL AND image_b64 IS NOT NULL AND image_b64 <> ''",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        raw = row["image_b64"]
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            import base64
            data = base64.b64decode(raw, validate=False)
            meta = validate_image_bytes(data)
            path = f"users/{user_id}/library/{row['id']}{meta[1]}"
            uploaded = upload_image(path, data, meta[0])
            try:
                conn = get_db()
                conn.execute(
                    "UPDATE library_items SET image_path = ?, image_mime = ?, image_size_bytes = ?, image_sha256 = ?, image_b64 = NULL WHERE id = ? AND user_id = ?",
                    (uploaded["path"], uploaded["mime"], uploaded["size"], uploaded["sha256"], row["id"], user_id),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                finally:
                    conn.close()
                delete_image(uploaded["path"])
                raise
            else:
                conn.close()
        except Exception:
            continue


def add_library_image(user_id: int, item_id: str, name: str, room_tag: str, obj_tag: str, data: bytes, content_type: str | None) -> dict:
    if not is_configured():
        raise RuntimeError("Supabase Storage is not configured")
    meta = validate_image_bytes(data, content_type)
    path = f"users/{user_id}/library/{item_id}{meta[1]}"
    uploaded = upload_image(path, data, meta[0])
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO library_items
               (id, user_id, name, room_tag, obj_tag, image_path, image_mime, image_size_bytes, image_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, user_id, name, room_tag, obj_tag, uploaded["path"], uploaded["mime"], uploaded["size"], uploaded["sha256"]),
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
    conn.close()
    return {
        "id": item_id,
        "name": name,
        "roomTag": room_tag,
        "objectTag": obj_tag,
        "dataUrl": signed_url(uploaded["path"]),
    }


def save_user_library(user_id: int, assets: list[dict]):
    if is_configured():
        raise RuntimeError("Bulk library saves are disabled when Storage is enabled")
    conn = get_db()
    try:
        conn.execute("DELETE FROM library_items WHERE user_id = ?", (user_id,))
        for a in assets:
            aid = str(a.get("id", "")).strip()
            if not aid:
                continue
            conn.execute(
                _UPSERT,
                (
                    aid, user_id,
                    a.get("name", ""),
                    a.get("roomTag", a.get("room_tag", "")),
                    a.get("objectTag", a.get("obj_tag", "")),
                    a.get("dataUrl", a.get("image_b64", "")),
                ),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def add_library_item(user_id: int, item_id: str, name: str, room_tag: str, obj_tag: str, data_url: str):
    conn = get_db()
    try:
        conn.execute(_UPSERT, (item_id, user_id, name, room_tag, obj_tag, data_url))
        conn.commit()
    finally:
        conn.close()


def delete_library_item(user_id: int, item_id: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT image_path FROM library_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM library_items WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn.commit()
    finally:
        conn.close()
    if row.get("image_path"):
        delete_image(row["image_path"])
    return True
