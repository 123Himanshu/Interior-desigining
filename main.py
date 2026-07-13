import os
import base64
import uuid
import math
import hashlib
import secrets
import sqlite3
import json as _json
from pathlib import Path
from io import BytesIO

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from dotenv import load_dotenv
from PIL import Image, ImageDraw
import openai

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_LABS_KEY = os.getenv("MODEL_LABS_KEY", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# ── Model toggle ──  True = ModelsLab Interior-Mixer | False = OpenAI GPT-Image-1.5 ──
USE_MODEL_LABS = True

if not USE_MODEL_LABS and not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found. Set the secret or switch USE_MODEL_LABS to True.")

# ── Database ────────────────────────────────────────────────────
DB_PATH = Path("roomai.db")

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = _db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS library_items (
            id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            room_tag TEXT DEFAULT '',
            obj_tag TEXT DEFAULT '',
            image_b64 TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, user_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS library_seeded (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1)
        );
    """)
    conn.commit()
    conn.close()

init_db()

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return pw_hash, salt

def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    computed, _ = hash_password(password, salt)
    return secrets.compare_digest(computed, stored_hash)

def get_user_credits(user_id: int) -> int:
    conn = _db()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["credits"] if row else 0

# ── HuggingFace dataset sync (optional) ───────────────────────
HF_TOKEN       = os.getenv("HF_TOKEN", "")
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "")
LIBRARY_FILE   = "library.json"
_hf_enabled    = bool(HF_TOKEN and HF_DATASET_REPO)


def _hf_load() -> list:
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename=LIBRARY_FILE,
            repo_type="dataset",
            token=HF_TOKEN,
        )
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


client = openai.OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="AI Interior Visualizer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = Path("uploads")
OUTPUTS_DIR = Path("outputs")
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

MAX_SIZE_BYTES = 4 * 1024 * 1024
MAX_DIMENSION  = 1024


def prepare_image(file_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(file_bytes)).convert("RGBA")
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    scale = 1.0
    while len(data) > MAX_SIZE_BYTES and scale > 0.2:
        scale -= 0.1
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="PNG")
        data = buf.getvalue()
    return data


def build_object_composite(pil_images: list, tags: list) -> bytes:
    n = len(pil_images)
    cols = 2
    rows = math.ceil(n / cols)
    cell_w, cell_h = 512, 512
    composite = Image.new("RGBA", (cell_w * cols, cell_h * rows), (28, 28, 28, 255))
    draw = ImageDraw.Draw(composite)
    for i, img in enumerate(pil_images):
        col = i % cols
        row = i // cols
        thumb = img.copy()
        thumb.thumbnail((cell_w - 16, cell_h - 40), Image.LANCZOS)
        x = col * cell_w + (cell_w - thumb.width) // 2
        y = row * cell_h + 8
        if thumb.mode == "RGBA":
            composite.paste(thumb, (x, y), thumb)
        else:
            composite.paste(thumb, (x, y))
        label = f"Obj {i + 1}: {tags[i] if i < len(tags) else 'item'}"
        draw.text((col * cell_w + 8, row * cell_h + cell_h - 28), label, fill=(255, 200, 80))
    buf = BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()


def build_edit_prompt(user_prompt: str, object_tags: list) -> str:
    n = len(object_tags)
    if n == 0:
        reference_instruction = (
            "Do not introduce unrelated new furniture, decor, or humans unless explicitly requested."
        )
    elif n == 1:
        reference_instruction = (
            f"CRITICAL: Use the second uploaded image as the exact visual reference for the '{object_tags[0]}'. "
            "Match its shape, proportions, and silhouette precisely. "
            "Extract and faithfully reproduce its materiality, surface textures, color, and finish. "
            "Scale it correctly relative to the room and surrounding furniture. "
            "Ground it with realistic contact shadows that follow the room's primary light direction."
        )
    else:
        guide_lines = "; ".join(
            f"Cell {i + 1}: the reference '{tag}'" for i, tag in enumerate(object_tags)
        )
        reference_instruction = (
            f"OBJECT REFERENCE GUIDE: The second uploaded image is a 2-column labelled grid containing {n} separate reference objects. "
            f"{guide_lines}. "
            "Each cell is an independent visual reference for a different object. "
            "Extract each object's exact shape, proportions, materiality, and surface textures from its cell. "
            "Place each one into the scene as instructed in the USER DIRECTIVE, ensuring correct relative scale between them. "
            "Ground every object with physically accurate contact and ambient shadows that follow the room's primary light direction."
        )
    return (
        "You are an expert architectural visualization AI and senior interior designer. "
        "Your task is to execute the user's request with strict adherence to photorealism.\n\n"
        "CORE CONSTRAINTS:\n"
        "- POSITION & PLACEMENT (Handling all scenarios):\n"
        "   * IF REPLACING of SIMILAR SIZE: The new item MUST strictly occupy the exact same spatial location and footprint. Do NOT arbitrarily shift its position.\n"
        "   * IF REPLACING with DIFFERENT SIZE: Anchor the new item to the original origin point, but scale it naturally. Adjust bounding box gracefully while maintaining perspective.\n"
        "   * IF ADDING to OPEN SPACE: Place the item naturally as described, strictly adhering to the room's vanishing points and realistic scale.\n"
        "   * IF REMOVING: Flawlessly synthesize and inpaint the newly exposed background (flooring, walls) to match the surrounding texture and lighting.\n"
        "- OCCLUSIONS & OVERLAPS: Carefully preserve any foreground objects (plants, blankets, pillars) that blocked the original object. The new object must sit behind them logically.\n"
        "- REFLECTIONS & SHADOWS: Ensure new objects cast physically accurate ground shadows, receive correct key light, and respect ambient occlusion. Update any mirrors or glossy floors to reflect the new state.\n"
        "- ANCHOR & PRESERVE: Do NOT alter the room's overarching geometry or camera angle. The rest of the room (walls, flooring, unaffected furniture) MUST remain identical.\n"
        "- MATERIALITY: Render with tactile, high-fidelity materiality (micro-imperfections, realistic reflections).\n"
        "- TEXTILES & SOFT FURNISHINGS: Ensure natural draping, folding, and realistic light transmission or opacity (especially for curtains, rugs, and bedding).\n"
        f"- {reference_instruction}\n"
        "- CLEANLINESS: Output a single realistic edited image with NO text, NO watermarks, and NO borders.\n\n"
        f"USER DIRECTIVE: {user_prompt.strip()}"
    )


# ── Auth ─────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:]
    conn = _db()
    row = conn.execute("SELECT user_id FROM sessions WHERE token = ?", (token,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid session")
    user = conn.execute("SELECT id, username, credits FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


# ── Auth endpoints ───────────────────────────────────────────────

def admin_only(request: Request):
    if not ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key not configured on server.")
    key = (request.headers.get("x-admin-key") or
           (request.query_params.get("admin_key") if hasattr(request, "query_params") else None) or
           "")
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key.")


@app.post("/register")
async def register(request: Request):
    admin_only(request)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    credits = body.get("credits", 100)
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    if len(username) < 2 or len(username) > 32:
        raise HTTPException(status_code=400, detail="Username must be 2-32 characters.")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")
    try:
        credits = int(credits)
    except (ValueError, TypeError):
        credits = 100
    if credits < 0:
        credits = 0

    pw_hash, salt = hash_password(password)
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, credits) VALUES (?, ?, ?, ?)",
            (username, pw_hash, salt, credits),
        )
        conn.commit()
        user_id = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=409, detail="Username already taken.")
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"token": token, "username": username, "credits": credits})


@app.post("/admin/set-credits")
async def admin_set_credits(request: Request):
    admin_only(request)
    body = await request.json()
    username = (body.get("username") or "").strip()
    credits = body.get("credits")
    if not username or credits is None:
        raise HTTPException(status_code=400, detail="username and credits required.")
    try:
        credits = int(credits)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="credits must be an integer.")
    conn = _db()
    row = conn.execute("SELECT id, username, credits FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found.")
    conn.execute("UPDATE users SET credits = ? WHERE id = ?", (credits, row["id"]))
    conn.commit()
    conn.close()
    return JSONResponse({"username": username, "credits": credits})


@app.get("/admin/users")
async def admin_list_users(request: Request):
    admin_only(request)
    conn = _db()
    rows = conn.execute("SELECT username, credits, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return JSONResponse([{"username": r["username"], "credits": r["credits"], "created_at": r["created_at"]} for r in rows])


@app.post("/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")

    conn = _db()
    user = conn.execute("SELECT id, username, password_hash, salt, credits FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if not verify_password(password, user["salt"], user["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))
    conn.commit()
    conn.close()
    return JSONResponse({
        "token": token,
        "username": user["username"],
        "credits": user["credits"],
    })


@app.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return JSONResponse({
        "username": user["username"],
        "credits": user["credits"],
    })


# ── Admin Panel ─────────────────────────────────────────────────

@app.get("/admin", include_in_schema=False)
async def admin_panel():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Admin Panel</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: #0a0a0a;
  color: #e0e0e0;
  padding: 40px;
  max-width: 700px;
  margin: 0 auto;
}
h1 { font-size: 24px; margin-bottom: 8px; }
h2 { font-size: 16px; color: #888; font-weight: 400; margin-bottom: 28px; }
.card {
  background: #1a1a1a;
  border: 1px solid #2a2a2a;
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 16px;
}
label { display: block; font-size: 12px; color: #888; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
input {
  width: 100%;
  padding: 10px 12px;
  background: #111;
  border: 1px solid #2a2a2a;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  margin-bottom: 10px;
  outline: none;
}
input:focus { border-color: #ea2804; }
button {
  padding: 10px 20px;
  background: #ea2804;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
}
button:hover { background: #c01f00; }
button.secondary { background: #2a2a2a; }
button.secondary:hover { background: #3a3a3a; }
.msg { padding: 8px 12px; border-radius: 6px; margin-top: 10px; font-size: 13px; }
.msg.ok { background: #1a3a1a; color: #4ade80; }
.msg.err { background: #3a1a1a; color: #f87171; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
td, th { padding: 10px 12px; text-align: left; border-bottom: 1px solid #2a2a2a; font-size: 14px; }
th { color: #888; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
.actions { display: flex; gap: 8px; }
.inline-input { width: 80px; padding: 6px 8px; margin: 0; font-size: 13px; }
.inline-btn { padding: 6px 12px; font-size: 12px; }
</style>
</head>
<body>
<h1>Admin Panel</h1>
<h2>Invite-only user management</h2>

<div class="card">
  <label>Create New User</label>
  <input id="newUser" placeholder="Username" />
  <input id="newPass" type="password" placeholder="Password" />
  <input id="newCredits" placeholder="Credits (default 100)" value="100" />
  <button onclick="createUser()">Create User</button>
  <div id="createMsg"></div>
</div>

<div class="card">
  <label>Registered Users</label>
  <button class="secondary" onclick="loadUsers()" style="margin-bottom:12px">Refresh List</button>
  <div id="userList">Click "Refresh List" to load users.</div>
</div>

<script>
const KEY = localStorage.getItem('admin_key') || '';
if (!KEY) {
  const k = prompt('Enter admin key:');
  if (k) localStorage.setItem('admin_key', k);
  else document.body.innerHTML = '<h1 style="color:#f87171;text-align:center;margin-top:100px">Admin key required</h1>';
}

function h(k) { return { 'X-Admin-Key': localStorage.getItem('admin_key') || k || '' }; }

async function createUser() {
  const u = document.getElementById('newUser').value.trim();
  const p = document.getElementById('newPass').value.trim();
  const c = document.getElementById('newCredits').value.trim();
  const m = document.getElementById('createMsg');
  if (!u || !p) { m.innerHTML = '<div class="msg err">Username and password required</div>'; return; }
  try {
    const r = await fetch('/register', { method:'POST', headers: {...h(), 'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p,credits:c||100}) });
    const d = await r.json();
    if (r.ok) {
      m.innerHTML = `<div class="msg ok">Created: ${d.username} — ${d.credits} credits</div>`;
      loadUsers();
    } else {
      m.innerHTML = `<div class="msg err">${d.detail || 'Error'}</div>`;
    }
  } catch(e) { m.innerHTML = `<div class="msg err">${e.message}</div>`; }
}

async function loadUsers() {
  const div = document.getElementById('userList');
  try {
    const r = await fetch('/admin/users', { headers: h() });
    const users = await r.json();
    if (!r.ok) { div.innerHTML = `<div class="msg err">${users.detail || 'Error'}</div>`; return; }
    let html = '<table><tr><th>Username</th><th>Credits</th><th>Actions</th></tr>';
    for (const u of users) {
      html += `<tr>
        <td>${u.username}</td>
        <td><input class="inline-input" id="cr_${u.username}" value="${u.credits}" /></td>
        <td><button class="inline-btn" onclick="setCredits('${u.username}')">Save</button></td>
      </tr>`;
    }
    html += '</table>';
    if (users.length === 0) html = 'No users yet.';
    div.innerHTML = html;
  } catch(e) { div.innerHTML = `<div class="msg err">${e.message}</div>`; }
}

async function setCredits(username) {
  const inp = document.getElementById('cr_' + username);
  const val = inp.value.trim();
  try {
    const r = await fetch('/admin/set-credits', { method:'POST', headers: {...h(), 'Content-Type':'application/json'}, body: JSON.stringify({username, credits: val}) });
    const d = await r.json();
    if (r.ok) { inp.value = d.credits; } else { alert(d.detail || 'Error'); }
  } catch(e) { alert(e.message); }
}
</script>
</body>
</html>
""")


# ── Health ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "hf_sync": _hf_enabled}


# ── Library endpoints (per-user) ────────────────────────────────

def _seed_library_from_hf(user_id: int):
    if not _hf_enabled:
        return
    if user_id != 1:
        return
    conn = _db()
    row = conn.execute("SELECT id FROM library_seeded LIMIT 1").fetchone()
    if row:
        conn.close()
        return
    try:
        assets = _hf_load()
        for idx, a in enumerate(assets):
            if isinstance(a, dict):
                asset_id = a.get("id") or f"hf_seed_{idx}"
                conn.execute(
                    "INSERT OR REPLACE INTO library_items (id, user_id, name, room_tag, obj_tag, image_b64) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(asset_id), user_id, a.get("name", ""), a.get("room_tag", ""), a.get("obj_tag", ""), a.get("image_b64", "")),
                )
        conn.execute("INSERT OR REPLACE INTO library_seeded (id) VALUES (1)")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


@app.get("/library")
async def get_library(user: dict = Depends(get_current_user)):
    _seed_library_from_hf(user["id"])
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, room_tag AS roomTag, obj_tag AS objectTag, image_b64 AS dataUrl, created_at AS createdAt FROM library_items WHERE user_id = ? ORDER BY createdAt",
        (user["id"],),
    ).fetchall()
    conn.close()
    assets = [dict(r) for r in rows]
    return JSONResponse({"enabled": True, "assets": assets})


@app.post("/library")
async def save_library(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    assets = body.get("assets")
    if not isinstance(assets, list):
        raise HTTPException(status_code=400, detail="assets must be an array")
    conn = _db()
    conn.execute("DELETE FROM library_items WHERE user_id = ?", (user["id"],))
    for a in assets:
        aid = str(a.get("id", ""))
        if not aid:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO library_items (id, user_id, name, room_tag, obj_tag, image_b64) VALUES (?, ?, ?, ?, ?, ?)",
            (aid, user["id"], a.get("name", ""), a.get("roomTag", a.get("room_tag", "")), a.get("objectTag", a.get("obj_tag", "")), a.get("dataUrl", a.get("image_b64", ""))),
        )
    conn.commit()
    conn.close()
    return JSONResponse({"enabled": True, "saved": True, "count": len(assets)})


@app.post("/library/add")
async def add_library_item(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    image_b64 = (body.get("dataUrl") or body.get("image_b64") or "").strip()
    if not name or not image_b64:
        raise HTTPException(status_code=400, detail="name and image required")
    item_id = str(body.get("id") or secrets.token_hex(8))
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO library_items (id, user_id, name, room_tag, obj_tag, image_b64) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, user["id"], name, body.get("roomTag", body.get("room_tag", "")), body.get("objectTag", body.get("obj_tag", "")), image_b64),
    )
    conn.commit()
    conn.close()
    return JSONResponse({"id": item_id, "name": name})


@app.delete("/library/{item_id}")
async def delete_library_item(item_id: str, user: dict = Depends(get_current_user)):
    conn = _db()
    conn.execute("DELETE FROM library_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    conn.commit()
    conn.close()
    return JSONResponse({"deleted": True})


# ── Model API functions ──────────────────────────────────────────

def call_openai_edit(room_png: bytes, reference_png: bytes | None, final_prompt: str, room_fname: str) -> str:
    if not client:
        raise RuntimeError("OpenAI API key not configured. Set OPENAI_API_KEY secret.")
    if reference_png:
        response = client.images.edit(
            model="gpt-image-1.5",
            image=[
                (room_fname, room_png, "image/png"),
                ("reference.png", reference_png, "image/png"),
            ],
            prompt=final_prompt,
            n=1,
            size="1024x1024",
        )
    else:
        response = client.images.edit(
            model="gpt-image-1.5",
            image=(room_fname, room_png, "image/png"),
            prompt=final_prompt,
            n=1,
            size="1024x1024",
        )
    for img_data in response.data:
        if hasattr(img_data, "b64_json") and img_data.b64_json:
            return img_data.b64_json
        if hasattr(img_data, "url") and img_data.url:
            import requests as req
            r = req.get(img_data.url, timeout=30)
            return base64.b64encode(r.content).decode()
    raise RuntimeError("No image data returned from OpenAI.")


def call_models_labs_edit(room_png: bytes, object_png: bytes | None, prompt: str, width: int = 0, height: int = 0) -> str:
    room_b64 = base64.b64encode(room_png).decode()
    if width < 512:
        width = Image.open(BytesIO(room_png)).width
    if height < 512:
        height = Image.open(BytesIO(room_png)).height
    width  = max(512, min(2048, width  - (width  % 8)))
    height = max(512, min(2048, height - (height % 8)))
    payload = {
        "key": MODEL_LABS_KEY,
        "model_id": "Interior-Mixer",
        "init_image": room_b64,
        "prompt": prompt,
        "width": str(width),
        "height": str(height),
        "base64": True,
    }
    if object_png:
        obj_b64 = base64.b64encode(object_png).decode()
        payload["object_image"] = obj_b64

    import requests as req
    import time

    resp = req.post(
        "https://modelslab.com/api/v6/interior/interior_mixer",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ModelsLab {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    if data.get("status") == "error":
        raise RuntimeError(f"ModelsLab error: {data.get('message','unknown')}")

    future_links = data.get("future_links", [])
    if not future_links:
        raise RuntimeError(f"ModelsLab: no future_links. Response: {_json.dumps(data)[:200]}")

    image_url = future_links[0]

    for _ in range(30):
        time.sleep(8)
        ir = req.get(image_url, timeout=30)
        if ir.status_code == 200 and len(ir.content) > 100:
            body = ir.content.decode("ascii", errors="ignore")
            return body
    raise RuntimeError("ModelsLab: timed out waiting for image")


# ── Edit endpoint ────────────────────────────────────────────────

@app.post("/edit")
async def edit_room(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    object_image_1: UploadFile = File(None),
    object_image_2: UploadFile = File(None),
    object_image_3: UploadFile = File(None),
    object_image_4: UploadFile = File(None),
    object_image_5: UploadFile = File(None),
    object_tags: str = Form("[]"),
):
    username = user["username"]

    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if room_image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Room image must be JPEG, PNG, WebP, or GIF.")

    try:
        tags: list = _json.loads(object_tags)
    except Exception:
        tags = []

    current_credits = get_user_credits(user["id"])
    if current_credits <= 0:
        raise HTTPException(status_code=402, detail="Insufficient credits. Generate limit reached.")

    room_bytes = await room_image.read()
    room_png   = prepare_image(room_bytes)
    room_id    = uuid.uuid4().hex
    room_path  = UPLOADS_DIR / f"{room_id}_room.png"
    room_path.write_bytes(room_png)

    raw_objects = [object_image_1, object_image_2, object_image_3, object_image_4, object_image_5]
    pil_objects = []
    for i, obj_upload in enumerate(raw_objects):
        if obj_upload and obj_upload.filename:
            obj_bytes = await obj_upload.read()
            obj_png   = prepare_image(obj_bytes)
            (UPLOADS_DIR / f"{room_id}_obj{i + 1}.png").write_bytes(obj_png)
            pil_objects.append(Image.open(BytesIO(obj_png)).convert("RGBA"))

    reference_png: bytes | None = None
    if len(pil_objects) > 1:
        reference_png = build_object_composite(pil_objects, tags)
        (UPLOADS_DIR / f"{room_id}_composite.png").write_bytes(reference_png)
    elif len(pil_objects) == 1:
        buf = BytesIO()
        pil_objects[0].save(buf, format="PNG")
        reference_png = buf.getvalue()

    try:
        if USE_MODEL_LABS:
            ml_prompt = prompt.strip()
            if reference_png and tags:
                ml_prompt = f"Add {tags[0]} from the object image to the room image. {ml_prompt}"
            room_img = Image.open(BytesIO(room_png))
            result_b64 = call_models_labs_edit(room_png, reference_png, ml_prompt, room_img.width, room_img.height)
        else:
            final_prompt = build_edit_prompt(prompt, tags[:len(pil_objects)])
            result_b64 = call_openai_edit(room_png, reference_png, final_prompt, room_path.name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    result_bytes = base64.b64decode(result_b64)
    (OUTPUTS_DIR / f"{room_id}_result_1.png").write_bytes(result_bytes)

    conn = _db()
    conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()
    new_credits = current_credits - 1

    return JSONResponse({
        "image_b64": result_b64,
        "images_b64": [result_b64],
        "format": "png",
        "credits": new_credits,
    })


# ── Static files ─────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
