"""Optional HF backup of user data (safety net). Primary store is Supabase PostgreSQL."""
import json as _json
import threading
import sys
from app.database import get_db
from app.config import HF_TOKEN, HF_DATASET_REPO

BACKUP_FILE = "db_backup.json"
_hf_enabled = bool(HF_TOKEN and HF_DATASET_REPO)
_backup_timer = None


def _backup_to_hf():
    if not _hf_enabled:
        return
    try:
        conn = get_db()
        try:
            users = conn.execute(
                "SELECT id, username, password_hash, salt, credits, is_admin, created_at FROM users"
            ).fetchall()
            library = conn.execute(
                "SELECT id, user_id, name, room_tag, obj_tag, image_b64, created_at FROM library_items"
            ).fetchall()
            seeded = conn.execute("SELECT id FROM library_seeded").fetchall()
        finally:
            conn.close()

        def _ser(rows):
            out = []
            for r in rows:
                d = dict(r)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                out.append(d)
            return out

        data = {
            "users": _ser(users),
            "library_items": _ser(library),
            "library_seeded": _ser(seeded),
        }
        content = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        from huggingface_hub import HfApi
        api = HfApi(token=HF_TOKEN)
        api.upload_file(
            path_or_fileobj=content,
            path_in_repo=BACKUP_FILE,
            repo_id=HF_DATASET_REPO,
            repo_type="dataset",
            commit_message="DB backup",
        )
    except Exception as e:
        print(f"[WARN] HF backup upload failed: {e}", file=sys.stderr)


def schedule_backup():
    """Debounced backup to HF dataset (secondary safety net only)."""
    global _backup_timer
    if not _hf_enabled:
        return
    if _backup_timer:
        _backup_timer.cancel()
    _backup_timer = threading.Timer(3.0, _backup_to_hf)
    _backup_timer.daemon = True
    _backup_timer.start()
