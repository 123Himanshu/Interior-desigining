import json as _json
from app.database import get_db
from app.config import HF_TOKEN, HF_DATASET_REPO, SEED_USERNAME

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
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, name,
                      room_tag AS "roomTag",
                      obj_tag AS "objectTag",
                      image_b64 AS "dataUrl",
                      created_at AS "createdAt"
               FROM library_items
               WHERE user_id = ?
               ORDER BY created_at""",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return rows


def save_user_library(user_id: int, assets: list[dict]):
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
        conn.execute(
            "DELETE FROM library_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
