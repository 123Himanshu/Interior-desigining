# AGENTS.md

## Commands

```bash
# Install
pip install -r requirements.txt

# Dev server (auto-reload)
uvicorn main:app --reload

# Frontend at http://localhost:8000
```

Requires `.env` with `OPENAI_API_KEY=sk-...`.

## Auth & Credit System

- **SQLite** database (`roomai.db`) stores users and sessions — no setup needed. Automatically created on first run.
- Passwords hashed with **pbkdf2_hmac** (sha256, 100k iterations) + per-user salt.
- New users start with **100 credits**. Each `/edit` call costs **1 credit** (deducted only on success).
- Endpoints: `POST /register`, `POST /login` (returns `token`), `GET /me` (requires `Authorization: Bearer <token>`).
- Frontend stores token in `localStorage` under `roomai_token`. Auth overlay shown until valid session exists.
- `/edit` and `/me` require auth. `/library`, `/health` are public.
- Credit check happens before OpenAI call; credit is deducted only after a successful generation.
- 402 status returned when credits exhausted. Frontend shows toast.

## Architecture

Two files do everything:

- **`main.py`** — FastAPI backend. `POST /edit` accepts multipart form: `room_image` (required), `prompt` (required), plus optional `object_image_1` through `object_image_5` and `object_tags` (JSON string array). Uses `client.images.edit()` with model `gpt-image-1.5`. Also has `GET/POST /library` for optional HuggingFace dataset sync.
- **`static/index.html`** — Entire frontend in a single 2000+ line vanilla HTML/CSS/JS file. Do not create separate JS/CSS files — keep everything in index.html.

## Gotchas

- **Model is `gpt-image-1.5`**, not `dall-e-3` or `gpt-image-1` (README is slightly stale).
- **Object tags** are sent as a JSON string form field: `object_tags='["sofa","lamp"]'`. Up to 5 object images.
- **Image pipeline**: converts to RGBA PNG, resizes to ≤1024px, iteratively scales down until <4 MB.
- **No test suite** — manual testing via browser.
- Docker targets **Railway**: reads `$PORT` env var (defaults to 8000).
- Optional **HuggingFace library sync** enabled via `HF_TOKEN` + `HF_DATASET_REPO` env vars.
- See `CLAUDE.md` for additional architecture notes.
