# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (auto-reload)
uvicorn main:app --reload

# Access frontend
# http://localhost:8000/static/index.html
```

Requires `.env` with `OPENAI_API_KEY=sk-...`.

## Architecture

Monolithic Python + vanilla JS app. Two files do everything:

- **`main.py`** — FastAPI backend. Single endpoint `POST /edit` accepts multipart form: `room_image` (required), `object_image` (optional), `prompt` (required). Calls `client.images.edit()` with model `gpt-image-1.5`. Returns `{ image_b64, images_b64, format }`.
- **`static/index.html`** — Entire frontend. Single-page, vanilla JS. Multi-step form → canvas preview → before/after comparison.

### Image pipeline (`main.py`)

1. `prepare_image()` — converts to RGBA PNG, resizes to ≤1024px, iteratively scales down until <4 MB.
2. `build_edit_prompt()` — wraps user prompt in detailed system instruction. If `has_object_reference=True`, instructs model to use second image as visual reference.
3. OpenAI call — passes 1 or 2 images as tuples `(filename, bytes, mimetype)`. Saves uploads to `uploads/`, results to `outputs/`.

### Static file serving

Both `/static` and `/` are mounted from the `static/` directory. The root mount uses `html=True` so `index.html` is served at `/`.

## Key constraints

- Model is `gpt-image-1.5` (not `dall-e-3`). The images.edit API expects PNG, ≤4 MB.
- No database, no auth, no sessions — stateless. Files in `uploads/` and `outputs/` are debug artifacts only.
- No test suite. Manual testing via browser at `localhost:8000`.
- Docker deployment targets Railway: `Dockerfile` reads `$PORT` env var (defaults to 8000).
