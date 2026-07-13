from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from app.config import ADMIN_KEY
from app.services.users import create_user, set_credits, list_users, user_exists
from app.services.auth import create_session
from app.services.backup import schedule_backup

router = APIRouter()


def verify_admin(request: Request):
    if not ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin key not configured")
    key = request.headers.get("x-admin-key") or request.headers.get("X-Admin-Key") or ""
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.post("/register")
async def register(request: Request):
    verify_admin(request)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    credits = body.get("credits", 100)
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    if len(username) < 2 or len(username) > 32:
        raise HTTPException(status_code=400, detail="Username must be 2-32 characters")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    try:
        credits = int(credits)
    except (ValueError, TypeError):
        credits = 100
    if credits < 0:
        credits = 0
    if user_exists(username):
        raise HTTPException(status_code=409, detail="Username already taken")
    user = create_user(username, password, credits)
    token = create_session(user["id"])
    schedule_backup()
    return JSONResponse({"token": token, "username": username, "credits": credits})


@router.post("/admin/set-credits")
async def admin_set_credits(request: Request):
    verify_admin(request)
    body = await request.json()
    username = (body.get("username") or "").strip()
    credits = body.get("credits")
    if not username or credits is None:
        raise HTTPException(status_code=400, detail="username and credits required")
    try:
        credits = int(credits)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="credits must be an integer")
    result = set_credits(username, credits)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    schedule_backup()
    return JSONResponse(result)


@router.get("/admin/users")
async def admin_list_users(request: Request):
    verify_admin(request)
    return JSONResponse(list_users())


@router.get("/admin", include_in_schema=False)
async def admin_panel():
    return HTMLResponse(ADMIN_PANEL_HTML)


ADMIN_PANEL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Admin Panel</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:40px;max-width:700px;margin:0 auto}
h1{font-size:24px;margin-bottom:8px}
h2{font-size:16px;color:#888;font-weight:400;margin-bottom:28px}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;padding:20px;margin-bottom:16px}
label{display:block;font-size:12px;color:#888;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
input{width:100%;padding:10px 12px;background:#111;border:1px solid #2a2a2a;border-radius:6px;color:#fff;font-size:14px;margin-bottom:10px;outline:none}
input:focus{border-color:#ea2804}
button{padding:10px 20px;background:#ea2804;color:#fff;border:none;border-radius:6px;font-weight:600;font-size:14px;cursor:pointer}
button:hover{background:#c01f00}
button.secondary{background:#2a2a2a}
button.secondary:hover{background:#3a3a3a}
.msg{padding:8px 12px;border-radius:6px;margin-top:10px;font-size:13px}
.msg.ok{background:#1a3a1a;color:#4ade80}
.msg.err{background:#3a1a1a;color:#f87171}
table{width:100%;border-collapse:collapse;margin-top:12px}
td,th{padding:10px 12px;text-align:left;border-bottom:1px solid #2a2a2a;font-size:14px}
th{color:#888;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.inline-input{width:80px;padding:6px 8px;margin:0;font-size:13px}
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
  <div id="userList">Click Refresh to load users.</div>
</div>
<script>
const KEY = localStorage.getItem('admin_key') || '';
if (!KEY) { const k = prompt('Enter admin key:'); if (k) localStorage.setItem('admin_key', k); else document.body.innerHTML = '<h1 style=color:#f87171;text-align:center;margin-top:100px>Admin key required</h1>'; }
function hd() { return { 'X-Admin-Key': localStorage.getItem('admin_key') || '' }; }
async function createUser() {
  const u = document.getElementById('newUser').value.trim();
  const p = document.getElementById('newPass').value.trim();
  const c = document.getElementById('newCredits').value.trim();
  const m = document.getElementById('createMsg');
  if (!u || !p) { m.innerHTML = '<div class=msg.err>Username and password required</div>'; return; }
  try {
    const r = await fetch('/register', { method:'POST', headers:{...hd(),'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p,credits:c||100}) });
    const d = await r.json();
    if (r.ok) { m.innerHTML = `<div class=msg.ok>Created: ${d.username} &mdash; ${d.credits} credits</div>`; loadUsers(); }
    else { m.innerHTML = `<div class=msg.err>${d.detail||'Error'}</div>`; }
  } catch(e) { m.innerHTML = `<div class=msg.err>${e.message}</div>`; }
}
async function loadUsers() {
  const div = document.getElementById('userList');
  try {
    const r = await fetch('/admin/users', { headers: hd() });
    const users = await r.json();
    if (!r.ok) { div.innerHTML = `<div class=msg.err>${users.detail||'Error'}</div>`; return; }
    let html = '<table><tr><th>Username</th><th>Credits</th><th>Actions</th></tr>';
    for (const u of users) {
      html += `<tr><td>${u.username}</td><td><input class=inline-input id=cr_${u.username} value=${u.credits} /></td><td><button class=inline-input onclick="setCredits('${u.username}')" style=width:auto>Save</button></td></tr>`;
    }
    html += '</table>';
    div.innerHTML = html;
  } catch(e) { div.innerHTML = `<div class=msg.err>${e.message}</div>`; }
}
async function setCredits(username) {
  const inp = document.getElementById('cr_' + username);
  const val = inp.value.trim();
  try {
    const r = await fetch('/admin/set-credits', { method:'POST', headers:{...hd(),'Content-Type':'application/json'}, body: JSON.stringify({username, credits: val}) });
    const d = await r.json();
    if (r.ok) { inp.value = d.credits; } else { alert(d.detail || 'Error'); }
  } catch(e) { alert(e.message); }
}
</script>
</body>
</html>"""
