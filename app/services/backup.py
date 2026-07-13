import json as _json
import threading
from app.database import get_db
from app.config import HF_TOKEN, HF_DATASET_REPO

BACKUP_FILE = "db_backup.json"
_hf_enabled = bool(HF_TOKEN and HF_DATASET_REPO)
_backup_timer = None


def restore_from_hf():
    if not _hf_enabled:
        return
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=HF_DATASET_REPO, filename=BACKUP_FILE,
            repo_type="dataset", token=HF_TOKEN,
        )
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        conn = get_db()
        conn.execute("DELETE FROM library_items")
        conn.execute("DELETE FROM library_seeded")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        for u in data.get("users", []):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, salt, credits, created_at) VALUES (?,?,?,?,?,?)",
                (u["id"], u["username"], u["password_hash"], u["salt"], u["credits"], u.get("created_at")),
            )
        for s in data.get("sessions", []):
            conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                         (s["token"], s["user_id"], s.get("created_at")))
        for li in data.get("library_items", []):
            conn.execute(
                "INSERT INTO library_items (id, user_id, name, room_tag, obj_tag, image_b64, created_at) VALUES (?,?,?,?,?,?,?)",
                (li["id"], li["user_id"], li["name"], li.get("room_tag", ""), li.get("obj_tag", ""), li["image_b64"], li.get("created_at")),
            )
        for ls in data.get("library_seeded", []):
            conn.execute("INSERT OR IGNORE INTO library_seeded (id) VALUES (?)", (ls["id"],))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _backup_to_hf():
    if not _hf_enabled:
        return
    try:
        conn = get_db()
        users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]
        sessions = [dict(r) for r in conn.execute("SELECT * FROM sessions").fetchall()]
        library = [dict(r) for r in conn.execute("SELECT * FROM library_items").fetchall()]
        seeded = [dict(r) for r in conn.execute("SELECT * FROM library_seeded").fetchall()]
        conn.close()
        data = {"users": users, "sessions": sessions, "library_items": library, "library_seeded": seeded}
        content = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        from huggingface_hub import HfApi
        api = HfApi(token=HF_TOKEN)
        api.upload_file(
            path_or_fileobj=content, path_in_repo=BACKUP_FILE,
            repo_id=HF_DATASET_REPO, repo_type="dataset",
            commit_message="DB backup",
        )
    except Exception:
        pass


def schedule_backup():
    global _backup_timer
    if not _hf_enabled:
        return
    if _backup_timer:
        _backup_timer.cancel()
    _backup_timer = threading.Timer(3.0, _backup_to_hf)
    _backup_timer.start()
