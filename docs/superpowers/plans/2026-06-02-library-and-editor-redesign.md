# Library + Editor Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the single-screen interior AI app into a two-screen app (Editor + Library) with localStorage-backed asset management, multi-object selection, and a single composite API call for up to 5 objects.

**Architecture:** All frontend lives in `static/index.html` (existing pattern — no split). Backend `main.py` gets a composite image builder so 1–5 object images are stitched into one grid before the OpenAI call. State flows: Library selection → JS memory → Editor pre-fill → `POST /edit` with room + composite.

**Tech Stack:** Python 3.11 / FastAPI / Pillow / OpenAI SDK (backend) · Vanilla JS / CSS custom properties / localStorage (frontend) · Bricolage Grotesque + Inter fonts · CSS tokens from existing theme.

---

## File Map

| File | Change |
|------|--------|
| `main.py` | Add `import math`, `build_object_composite()`, update `build_edit_prompt()` for multi-object, update `/edit` endpoint to accept `object_image_1..5` + `object_tags` |
| `static/index.html` | Full rewrite: two-screen HTML structure, extended CSS, new JS modules (LibraryStore, AppState, tag modal, library rendering, editor wiring, prompt builder, generate) |

---

## Task 1: Backend — multi-object composite builder

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add `import math` at top of main.py**

Open `main.py`. After `from io import BytesIO` add:

```python
import math
```

- [ ] **Step 2: Add `build_object_composite()` after `prepare_image()`**

Insert this function between `prepare_image()` and `build_edit_prompt()`:

```python
def build_object_composite(pil_images: list, tags: list) -> bytes:
    """Stitch up to 5 object images into a labelled 2-column grid PNG."""
    from PIL import ImageDraw
    n = len(pil_images)
    cols = 2
    rows = math.ceil(n / cols)
    cell_w, cell_h = 512, 512
    composite = Image.new("RGBA", (cell_w * cols, cell_h * rows), (28, 28, 28, 255))
    draw = ImageDraw.Draw(composite)

    for i, img in enumerate(pil_images):
        col, row = i % cols, i // cols
        thumb = img.copy()
        thumb.thumbnail((cell_w - 16, cell_h - 40), Image.LANCZOS)
        x = col * cell_w + (cell_w - thumb.width) // 2
        y = row * cell_h + 8
        composite.paste(thumb, (x, y), thumb if thumb.mode == "RGBA" else None)
        label = f"Obj {i + 1}: {tags[i] if i < len(tags) else 'item'}"
        draw.text((col * cell_w + 8, row * cell_h + cell_h - 28), label, fill=(255, 200, 80))

    buf = BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 3: Update `build_edit_prompt()` to accept `object_tags` list**

Replace the existing `build_edit_prompt` signature and `reference_instruction` block:

```python
def build_edit_prompt(user_prompt: str, object_tags: list[str]) -> str:
    """Build a structured prompt. object_tags is an ordered list of tag names (empty = no objects)."""
    n = len(object_tags)
    if n == 0:
        reference_instruction = (
            "Do not introduce unrelated new furniture, decor, or humans unless explicitly requested."
        )
    elif n == 1:
        reference_instruction = (
            f"CRITICAL: Use the second uploaded image as the specific visual reference for the '{object_tags[0]}'. "
            "Match its shape perfectly. Extract and apply its exact materiality and textures. "
            "Ground it with realistic contact shadows that follow the room's primary light direction."
        )
    else:
        guide_lines = ", ".join(
            f"Object {i+1} (position {i+1} in grid): '{tag}'" for i, tag in enumerate(object_tags)
        )
        reference_instruction = (
            f"OBJECT REFERENCE GUIDE: The second image is a {cols_for(n)}-column grid of {n} reference objects. "
            f"{guide_lines}. "
            "For each object: match its exact shape, materiality, and texture. "
            "Place each one where natural given the user directive and the room layout. "
            "Ground every object with physically accurate shadows following the room's primary light."
        )

    return (
        "You are an expert architectural visualization AI and senior interior designer. "
        "Your task is to execute the user's request with strict adherence to photorealism.\n\n"
        "CORE CONSTRAINTS:\n"
        "- POSITION & PLACEMENT:\n"
        "   * IF REPLACING of SIMILAR SIZE: occupy the exact same spatial location and footprint.\n"
        "   * IF REPLACING with DIFFERENT SIZE: anchor to original origin point, scale naturally.\n"
        "   * IF ADDING to OPEN SPACE: place naturally, strictly adhering to vanishing points and realistic scale.\n"
        "   * IF REMOVING: flawlessly inpaint the newly exposed background to match surroundings.\n"
        "- OCCLUSIONS & OVERLAPS: preserve foreground objects that blocked originals.\n"
        "- REFLECTIONS & SHADOWS: physically accurate ground shadows, correct key light, ambient occlusion.\n"
        "- ANCHOR & PRESERVE: do NOT alter room geometry or camera angle. Unaffected surfaces MUST remain identical.\n"
        "- MATERIALITY: render with tactile, high-fidelity materiality.\n"
        "- TEXTILES: ensure natural draping, folding, realistic light transmission.\n"
        f"- {reference_instruction}\n"
        "- CLEANLINESS: output a single realistic edited image with NO text, NO watermarks, NO borders.\n\n"
        f"USER DIRECTIVE: {user_prompt.strip()}"
    )


def cols_for(n: int) -> int:
    return 2 if n > 1 else 1
```

- [ ] **Step 4: Replace the `/edit` endpoint**

Replace the entire `@app.post("/edit")` function with:

```python
@app.post("/edit")
async def edit_room(
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    object_image_1: UploadFile = File(None),
    object_image_2: UploadFile = File(None),
    object_image_3: UploadFile = File(None),
    object_image_4: UploadFile = File(None),
    object_image_5: UploadFile = File(None),
    object_tags: str = Form("[]"),
):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if room_image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Room image must be JPEG, PNG, WebP, or GIF.")

    import json as _json
    try:
        tags: list[str] = _json.loads(object_tags)
    except Exception:
        tags = []

    # Prepare room image
    room_bytes = await room_image.read()
    room_png = prepare_image(room_bytes)
    room_id = uuid.uuid4().hex
    room_path = UPLOADS_DIR / f"{room_id}_room.png"
    room_path.write_bytes(room_png)

    # Collect object images
    raw_objects = [object_image_1, object_image_2, object_image_3, object_image_4, object_image_5]
    pil_objects = []
    for i, obj_upload in enumerate(raw_objects):
        if obj_upload and obj_upload.filename:
            obj_bytes = await obj_upload.read()
            obj_png = prepare_image(obj_bytes)
            (UPLOADS_DIR / f"{room_id}_obj{i+1}.png").write_bytes(obj_png)
            pil_objects.append(Image.open(BytesIO(obj_png)).convert("RGBA"))

    # Build prompt
    final_prompt = build_edit_prompt(prompt, tags[:len(pil_objects)])

    # Build reference image: composite if multiple objects, single if one
    reference_png: bytes | None = None
    if len(pil_objects) > 1:
        reference_png = build_object_composite(pil_objects, tags)
        comp_path = UPLOADS_DIR / f"{room_id}_composite.png"
        comp_path.write_bytes(reference_png)
    elif len(pil_objects) == 1:
        buf = BytesIO()
        pil_objects[0].save(buf, format="PNG")
        reference_png = buf.getvalue()

    # Call OpenAI
    try:
        if reference_png:
            response = client.images.edit(
                model="gpt-image-1",
                image=[
                    (room_path.name, room_png, "image/png"),
                    ("reference.png", reference_png, "image/png"),
                ],
                prompt=final_prompt,
                n=1,
                size="1024x1024",
            )
        else:
            response = client.images.edit(
                model="gpt-image-1",
                image=(room_path.name, room_png, "image/png"),
                prompt=final_prompt,
                n=1,
                size="1024x1024",
            )
    except openai.BadRequestError as e:
        raise HTTPException(status_code=400, detail=f"OpenAI rejected the request: {str(e)}")
    except openai.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid OpenAI API key. Check your .env file.")
    except openai.RateLimitError:
        raise HTTPException(status_code=429, detail="OpenAI rate limit reached. Please wait and try again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    # Decode result
    results_b64 = []
    for idx, image_data in enumerate(response.data):
        if hasattr(image_data, "b64_json") and image_data.b64_json:
            result_b64 = image_data.b64_json
            result_bytes = base64.b64decode(result_b64)
        elif hasattr(image_data, "url") and image_data.url:
            import requests as req
            result_bytes = req.get(image_data.url, timeout=30).content
            result_b64 = base64.b64encode(result_bytes).decode()
        else:
            continue
        (OUTPUTS_DIR / f"{room_id}_result_{idx+1}.png").write_bytes(result_bytes)
        results_b64.append(result_b64)

    if not results_b64:
        raise HTTPException(status_code=500, detail="No image data returned from OpenAI.")

    return JSONResponse({"image_b64": results_b64[0], "images_b64": results_b64, "format": "png"})
```

- [ ] **Step 5: Start server and verify it starts without errors**

```bash
uvicorn main:app --reload
```

Expected: `INFO: Application startup complete.` — no import errors.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(backend): multi-object composite builder, accept up to 5 object images"
```

---

## Task 2: HTML skeleton — two-screen structure

**Files:**
- Modify: `static/index.html` (replace entire file)

This task lays the HTML scaffold only — no JS, minimal inline CSS beyond what's needed to confirm structure. CSS and JS tasks follow.

- [ ] **Step 1: Replace `static/index.html` with the two-screen skeleton**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AI Room Visualizer</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
<style>
/* CSS PLACEHOLDER — full styles added in Task 3 */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', sans-serif; background: #f9f7f3; color: #202020; }
.screen { display: none; }
.screen.active { display: flex; }
</style>
</head>
<body>

<div id="app">

  <!-- ── HEADER ── -->
  <header id="app-header">
    <div class="header-logo">VISUALIZER</div>
    <div class="header-nav">
      <button class="nav-pill active" data-screen="editor">Editor</button>
      <button class="nav-pill" data-screen="library">Library</button>
    </div>
    <div class="header-meta">gpt-image-1</div>
  </header>

  <!-- ── EDITOR SCREEN ── -->
  <div id="screen-editor" class="screen active" role="main">

    <!-- Left column: inputs -->
    <div id="editor-inputs">

      <!-- Room image -->
      <section class="input-section" id="section-room">
        <div class="section-title">
          <span class="section-num">01</span>
          <span>Environment Architecture</span>
        </div>
        <div class="drop-zone" id="roomZone">
          <input type="file" id="roomInput" accept="image/*" />
          <div class="drop-text"><strong>UPLOAD SCENE</strong> Drop main room photo</div>
        </div>
        <div class="mini-preview hidden" id="roomMini">
          <img id="roomThumb" src="" alt="" />
          <div class="mini-preview-info">
            <div class="mini-preview-name">Base Layer Active</div>
            <div class="mini-preview-action" id="roomRemove">Replace Scene</div>
          </div>
        </div>
      </section>

      <!-- Subject assets -->
      <section class="input-section disabled" id="section-assets">
        <div class="section-title">
          <span class="section-num">02</span>
          <span>Subject Assets</span>
          <span class="asset-count-badge" id="assetCountBadge" style="display:none"></span>
        </div>

        <div class="asset-toggle">
          <button class="asset-toggle-btn active" data-mode="upload">⬆ Upload file</button>
          <button class="asset-toggle-btn" data-mode="library">📚 From Library</button>
        </div>

        <!-- Upload mode -->
        <div id="asset-upload-mode">
          <div class="drop-zone" id="objZone">
            <input type="file" id="objInput" accept="image/*" />
            <div class="drop-text"><strong>UPLOAD OBJECT</strong> Reference furniture (optional)</div>
          </div>
          <div class="mini-preview hidden" id="objMini">
            <img id="objThumb" src="" alt="" />
            <div class="mini-preview-info">
              <div class="mini-preview-name">Asset Attached</div>
              <div class="mini-preview-action" id="objRemove">Remove Asset</div>
            </div>
          </div>
        </div>

        <!-- Library mode -->
        <div id="asset-library-mode" style="display:none">
          <div id="editor-lib-grid">
            <!-- populated by JS renderEditorLibraryGrid() -->
          </div>
        </div>
      </section>

    </div>

    <!-- Right column: prompt panel -->
    <aside id="prompt-panel">
      <div class="panel-title">Prompt & Controls</div>

      <!-- Selected objects chips -->
      <div class="panel-block" id="block-chips">
        <div class="panel-label">Selected Objects</div>
        <div id="selected-chips" class="chips-row">
          <!-- populated by JS -->
        </div>
      </div>

      <!-- Action tags -->
      <div class="panel-block">
        <div class="panel-label">Action</div>
        <div class="tags-row" id="action-tags">
          <button class="tag active" data-group="action">Add to room</button>
          <button class="tag" data-group="action">Replace existing</button>
          <button class="tag" data-group="action">Remove</button>
          <button class="tag" data-group="action">Restyle</button>
        </div>
      </div>

      <!-- Style tags -->
      <div class="panel-block">
        <div class="panel-label">Style</div>
        <div class="tags-row" id="style-tags">
          <button class="tag" data-group="style">Modern</button>
          <button class="tag" data-group="style">Scandinavian</button>
          <button class="tag active" data-group="style">Minimalist</button>
          <button class="tag" data-group="style">Industrial</button>
          <button class="tag" data-group="style">Boho</button>
          <button class="tag" data-group="style">Luxury</button>
          <button class="tag" data-group="style">Japandi</button>
          <button class="tag" data-group="style">Coastal</button>
        </div>
      </div>

      <!-- Placement tags -->
      <div class="panel-block">
        <div class="panel-label">Placement</div>
        <div class="tags-row" id="placement-tags">
          <button class="tag" data-group="placement">Left corner</button>
          <button class="tag active" data-group="placement">Center wall</button>
          <button class="tag" data-group="placement">Right corner</button>
          <button class="tag" data-group="placement">Foreground</button>
          <button class="tag" data-group="placement">Background</button>
        </div>
      </div>

      <!-- Prompt textarea -->
      <div class="panel-block">
        <div class="panel-label">Directive — auto-built · edit freely</div>
        <textarea id="promptInput" class="prompt-textarea" placeholder="Select tags above or type a custom directive..."></textarea>
      </div>

      <!-- Cost note -->
      <div class="cost-note">~$0.06–0.10 per generation · 15–30s</div>

      <button class="btn-generate" id="generateBtn">Process Render</button>
    </aside>

    <!-- Canvas area -->
    <div id="canvas-area">
      <div class="empty-state" id="emptyState">
        <svg fill="currentColor" viewBox="0 0 24 24" width="64" height="64" style="opacity:.25;margin-bottom:24px"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
        <p>Upload a room scene to initialise workspace.</p>
      </div>

      <div class="viewport hidden" id="viewport">
        <div class="view-toggles hidden" id="viewToggles">
          <button id="btnViewScene" class="btn-toggle active">Environment</button>
          <button id="btnViewAsset" class="btn-toggle">Reference Asset</button>
        </div>
        <img id="canvasMainImg" class="main-img" src="" alt="Room" />
        <img id="canvasAssetImg" class="main-img" src="" alt="Asset" style="display:none" />
      </div>

      <div class="result-view hidden" id="resultView">
        <div class="result-header">
          <div class="result-title">Render Complete</div>
          <div class="result-actions">
            <button class="btn-ghost" id="refineBtn">Iterate Layer</button>
            <a class="btn-ghost btn-export" id="downloadBtn" href="#" download="synthesis-output.png">Export</a>
          </div>
        </div>
        <div class="comparison-grid">
          <div class="image-pane">
            <div class="pane-label">Archive (Source)</div>
            <div class="pane-img-wrap"><img id="beforeImg" src="" alt="Before" /></div>
          </div>
          <div class="image-pane">
            <div class="pane-label pane-label-result">Synthesis (Output)</div>
            <div class="pane-img-wrap"><img id="afterImg" src="" alt="After" /></div>
          </div>
        </div>
      </div>

      <div class="overlay-loader hidden" id="loader">
        <div class="spinner"></div>
        <div class="loader-title">Synthesizing Scene...</div>
        <div class="loader-desc">Applying photorealistic constraints. Expected: 15–30s</div>
      </div>
    </div>

  </div><!-- /screen-editor -->

  <!-- ── LIBRARY SCREEN ── -->
  <div id="screen-library" class="screen" role="main">
    <div id="library-body">

      <div class="library-topbar">
        <div class="library-topbar-left">
          <div class="library-title">Asset Library</div>
          <div class="library-subtitle">Tag and organise reference objects · select up to 5 to generate</div>
        </div>
        <div class="library-topbar-right">
          <button class="btn-secondary" id="libSearchBtn">🔍 Search</button>
          <button class="btn-upload-trigger" id="libUploadTrigger">
            <input type="file" id="libFileInput" accept="image/*" style="display:none" />
            + Add Asset
          </button>
        </div>
      </div>

      <!-- Tag info strip -->
      <div class="tag-info-strip">
        <span class="tag-info-icon">🏷</span>
        <div>
          <strong>Two tags per asset:</strong>
          <span class="room-tag-example">Room type</span> — where it belongs ·
          <span class="obj-tag-example">Object type</span> — what it is.
          Custom tags are saved and appear in future dropdowns.
        </div>
      </div>

      <!-- Library grid — populated by JS -->
      <div id="library-grid-container">
        <!-- renderLibrary() writes section elements here -->
      </div>

      <!-- Empty library state -->
      <div id="library-empty" class="library-empty-state hidden">
        <div class="library-empty-icon">📦</div>
        <div class="library-empty-title">No assets yet</div>
        <div class="library-empty-sub">Click "Add Asset" to upload your first reference image.</div>
      </div>

    </div>

    <!-- Floating selection bar — shown when ≥1 asset selected -->
    <div class="float-bar hidden" id="floatBar">
      <div class="float-chips" id="floatChips"></div>
      <div class="float-count" id="floatCount">0 / 5</div>
      <button class="float-clear" id="floatClear">✕ Clear</button>
      <button class="btn-generate-lib" id="libGenerateBtn">⚡ Generate → <span class="float-badge" id="floatBadge">0</span></button>
    </div>

  </div><!-- /screen-library -->

</div><!-- /app -->

<!-- ── TAG MODAL ── -->
<div class="modal-overlay hidden" id="tagModal">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">Tag this asset</div>
      <button class="modal-close" id="modalClose">✕</button>
    </div>
    <div class="modal-preview-wrap">
      <img id="modalPreviewImg" src="" alt="" />
    </div>
    <div class="modal-field">
      <label class="modal-label">Asset name</label>
      <input type="text" id="modalName" class="modal-input" placeholder="e.g. Modern Sofa" />
    </div>
    <div class="modal-field">
      <label class="modal-label">Room type <span class="tag-type-badge room">green</span></label>
      <select id="modalRoomSelect" class="modal-select">
        <option value="">— choose or type custom —</option>
      </select>
      <input type="text" id="modalRoomCustom" class="modal-input" placeholder="Custom room tag..." style="margin-top:6px" />
    </div>
    <div class="modal-field">
      <label class="modal-label">Object type <span class="tag-type-badge obj">blue</span></label>
      <select id="modalObjSelect" class="modal-select">
        <option value="">— choose or type custom —</option>
      </select>
      <input type="text" id="modalObjCustom" class="modal-input" placeholder="Custom object tag..." style="margin-top:6px" />
    </div>
    <div class="modal-actions">
      <button class="modal-cancel" id="modalCancel">Cancel</button>
      <button class="modal-save" id="modalSave">Save to Library</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast hidden" id="toast"></div>

<script>
/* JS added in Tasks 4–9 */
</script>
</body>
</html>
```

- [ ] **Step 2: Open `http://localhost:8000/static/index.html` and confirm two screens exist structurally (Editor shows, Library hidden). Header has two pill buttons.**

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): two-screen HTML skeleton with editor, library, and tag modal"
```

---

## Task 3: CSS — full styles

**Files:**
- Modify: `static/index.html` — replace the `<style>` block

- [ ] **Step 1: Replace the entire `<style>` block (between `<style>` and `</style>`) with:**

```css
/* ── Tokens ─────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --canvas:        #f9f7f3;
  --bone:          #f3f0e8;
  --card:          #ffffff;
  --surface-dark:  #202020;
  --primary:       #ea2804;
  --primary-deep:  #c01f00;
  --ink:           #202020;
  --body:          #3a3a3a;
  --charcoal:      #575757;
  --mute:          #646464;
  --hairline:      rgba(32,32,32,0.12);
  --hairline-str:  #202020;
  --r-xs: 4px; --r-sm: 6px; --r-md: 10px; --r-lg: 16px; --r-full: 9999px;
  --sp-xs:4px; --sp-sm:8px; --sp-md:12px; --sp-lg:16px; --sp-xl:24px; --sp-xxl:32px;
}
html, body { height: 100%; background: var(--canvas); color: var(--ink); font-family: 'Inter', -apple-system, system-ui, sans-serif; font-size: 16px; line-height: 1.5; overflow: hidden; }
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--hairline); border-radius: var(--r-full); }

/* ── Utilities ────────────────────────────────────────────── */
.hidden { display: none !important; }

/* ── App shell ─────────────────────────────────────────────── */
#app { display: flex; flex-direction: column; height: 100vh; }

/* ── Header ─────────────────────────────────────────────────── */
#app-header {
  height: 58px; flex-shrink: 0;
  background: var(--canvas); border-bottom: 1px solid var(--hairline);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 var(--sp-xl); z-index: 50;
}
.header-logo {
  font-family: 'Bricolage Grotesque', sans-serif;
  font-weight: 700; font-size: 18px; letter-spacing: -0.5px; color: var(--ink);
}
.header-nav {
  display: flex; gap: 3px;
  background: var(--bone); padding: 3px; border-radius: var(--r-md);
  border: 1px solid var(--hairline);
}
.nav-pill {
  padding: 6px 22px; border-radius: var(--r-sm); border: none;
  font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s;
  background: transparent; color: var(--mute);
}
.nav-pill.active { background: var(--primary); color: #fff; }
.nav-pill:not(.active):hover { color: var(--ink); }
.header-meta { font-size: 11px; color: var(--mute); letter-spacing: 0.3px; }

/* ── Screens ─────────────────────────────────────────────────── */
.screen { flex: 1; overflow: hidden; }
.screen.active { display: flex; }

/* ── Editor screen layout ─────────────────────────────────── */
#screen-editor { display: grid; grid-template-columns: 400px 320px 1fr; overflow: hidden; }
#editor-inputs { overflow-y: auto; background: var(--bone); border-right: 1px solid var(--hairline); }
#prompt-panel { overflow-y: auto; background: var(--card); border-right: 1px solid var(--hairline); display: flex; flex-direction: column; gap: 0; }
#canvas-area { position: relative; display: flex; align-items: center; justify-content: center; padding: var(--sp-xl); overflow: hidden; background: var(--canvas); }

/* ── Input sections ──────────────────────────────────────── */
.input-section {
  padding: var(--sp-lg) var(--sp-xl);
  border-bottom: 1px solid var(--hairline);
  transition: opacity 0.25s;
}
.input-section.disabled { opacity: 0.4; pointer-events: none; }
.section-title {
  display: flex; align-items: baseline; gap: 7px;
  font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: var(--sp-md);
}
.section-num { color: var(--primary); font-size: 13px; font-weight: 600; }
.asset-count-badge {
  background: var(--primary); color: #fff;
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: var(--r-full);
  margin-left: auto;
}

/* Drop zones */
.drop-zone {
  border: 1px dashed var(--hairline-str); background: var(--card);
  padding: var(--sp-xxl) var(--sp-lg); text-align: center; cursor: pointer;
  position: relative; transition: all 0.2s; border-radius: var(--r-md);
}
.drop-zone:hover { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(234,40,4,0.06); }
.drop-zone input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
.drop-text { font-size: 13px; color: var(--body); }
.drop-text strong { display: block; color: var(--ink); margin-bottom: 3px; font-weight: 600; font-size: 12px; letter-spacing: 0.5px; }

/* Mini preview */
.mini-preview {
  display: flex; background: var(--card); padding: var(--sp-md);
  border: 1px solid var(--hairline); align-items: center; gap: 12px;
  border-radius: var(--r-md); margin-top: var(--sp-md);
}
.mini-preview.hidden { display: none; }
.mini-preview img { width: 44px; height: 44px; object-fit: cover; border-radius: var(--r-xs); }
.mini-preview-name { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 2px; }
.mini-preview-action { font-size: 12px; color: var(--mute); cursor: pointer; text-decoration: underline; }
.mini-preview-action:hover { color: var(--primary); }

/* Asset mode toggle */
.asset-toggle {
  display: flex; gap: 0; background: var(--bone); border-radius: var(--r-sm);
  padding: 3px; margin-bottom: var(--sp-md); border: 1px solid var(--hairline);
}
.asset-toggle-btn {
  flex: 1; padding: 6px; border: none; border-radius: 5px; font-size: 12px;
  font-weight: 600; cursor: pointer; background: transparent; color: var(--mute); transition: all 0.15s;
}
.asset-toggle-btn.active { background: var(--card); color: var(--ink); box-shadow: 0 1px 4px rgba(32,32,32,0.08); }

/* ── Editor inline library grid ──────────────────────────── */
#editor-lib-grid { padding: 4px 0; }
.elib-section { margin-bottom: var(--sp-lg); }
.elib-section-header {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11px; font-weight: 600; color: var(--mute); text-transform: uppercase;
  letter-spacing: 0.6px; margin-bottom: 8px;
}
.elib-expand { background: none; border: 1px solid var(--hairline); color: var(--mute); font-size: 10px; padding: 2px 8px; border-radius: var(--r-sm); cursor: pointer; }
.elib-thumbs { display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; }
.elib-thumb {
  aspect-ratio: 1; border-radius: var(--r-sm); overflow: hidden; cursor: pointer;
  border: 2px solid transparent; transition: border-color 0.12s, transform 0.1s;
  position: relative; background: var(--bone);
}
.elib-thumb:hover { border-color: var(--charcoal); transform: translateY(-1px); }
.elib-thumb.selected { border-color: var(--primary); }
.elib-thumb img { width: 100%; height: 100%; object-fit: cover; }
.elib-thumb .check { position: absolute; top: 2px; right: 2px; width: 14px; height: 14px; background: var(--primary); border-radius: 50%; display: none; align-items: center; justify-content: center; font-size: 8px; font-weight: bold; color: #fff; }
.elib-thumb.selected .check { display: flex; }
.elib-empty { font-size: 12px; color: var(--mute); text-align: center; padding: var(--sp-xl) 0; }

/* ── Prompt panel ────────────────────────────────────────── */
.panel-title {
  padding: var(--sp-lg) var(--sp-xl) var(--sp-md);
  font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 18px; font-weight: 700; letter-spacing: -0.3px; color: var(--ink);
  border-bottom: 1px solid var(--hairline); flex-shrink: 0;
}
.panel-block { padding: var(--sp-md) var(--sp-xl); border-bottom: 1px solid var(--hairline); }
.panel-label { font-size: 10px; font-weight: 600; color: var(--mute); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }

/* Chips */
.chips-row { display: flex; gap: 6px; flex-wrap: wrap; min-height: 28px; }
.chip {
  display: flex; align-items: center; gap: 5px;
  background: var(--bone); border: 1px solid var(--hairline-str); border-radius: var(--r-sm);
  padding: 4px 8px 4px 6px; font-size: 11px; font-weight: 600; color: var(--ink);
}
.chip-thumb { width: 20px; height: 20px; border-radius: 3px; object-fit: cover; }
.chip-remove { color: var(--mute); cursor: pointer; font-size: 10px; line-height: 1; }
.chip-remove:hover { color: var(--primary); }
.chips-empty { font-size: 12px; color: var(--mute); font-style: italic; }

/* Tag rows */
.tags-row { display: flex; gap: 5px; flex-wrap: wrap; }
.tag {
  background: var(--bone); border: 1px solid var(--hairline); color: var(--body);
  font-size: 11px; font-weight: 500; padding: 5px 11px; border-radius: var(--r-full);
  cursor: pointer; transition: all 0.15s; white-space: nowrap;
}
.tag:hover { border-color: var(--primary); color: var(--primary); background: rgba(234,40,4,0.04); }
.tag.active { background: var(--ink); border-color: var(--ink); color: #fff; }

/* Prompt textarea */
.prompt-textarea {
  width: 100%; height: 90px; background: var(--card); border: 1px solid var(--hairline);
  color: var(--ink); padding: 10px 14px; font-family: inherit; font-size: 13px;
  resize: none; outline: none; border-radius: var(--r-md); transition: border-color 0.2s, box-shadow 0.2s; line-height: 1.5;
}
.prompt-textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(234,40,4,0.1); }

/* Cost note */
.cost-note { padding: var(--sp-sm) var(--sp-xl); font-size: 11px; color: var(--mute); }

/* Generate button */
.btn-generate {
  margin: var(--sp-md) var(--sp-xl) var(--sp-xl);
  width: calc(100% - 2 * var(--sp-xl)); background: var(--primary); color: #fff;
  border: none; padding: 13px 24px; font-size: 14px; font-weight: 700;
  border-radius: var(--r-full); cursor: pointer; transition: background 0.2s; letter-spacing: 0.2px;
}
.btn-generate:hover { background: var(--primary-deep); }
.btn-generate:disabled { opacity: 0.45; cursor: not-allowed; }

/* ── Canvas area ──────────────────────────────────────────── */
.empty-state { text-align: center; color: var(--mute); display: flex; flex-direction: column; align-items: center; font-size: 15px; }
.viewport { display: flex; flex-direction: column; align-items: center; width: 100%; height: 100%; justify-content: center; }
.viewport.hidden { display: none; }
.main-img { max-width: 100%; max-height: calc(100vh - 130px); object-fit: contain; box-shadow: 0 8px 24px rgba(32,32,32,0.08); border-radius: var(--r-md); }
.view-toggles { display: inline-flex; background: var(--card); padding: 4px; border-radius: var(--r-md); border: 1px solid var(--hairline); margin-bottom: var(--sp-xl); gap: 3px; }
.view-toggles.hidden { display: none; }
.btn-toggle { padding: 6px 18px; border-radius: var(--r-xs); border: none; background: transparent; cursor: pointer; font-size: 13px; font-weight: 600; color: var(--mute); transition: all 0.15s; }
.btn-toggle.active { background: var(--canvas); color: var(--ink); box-shadow: 0 1px 4px rgba(32,32,32,0.06); }

/* Result view */
.result-view { display: flex; flex-direction: column; width: 100%; height: 100%; }
.result-view.hidden { display: none; }
.result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-xxl); flex-shrink: 0; }
.result-title { font-family: 'Bricolage Grotesque', sans-serif; font-size: 54px; font-weight: 700; line-height: 1; letter-spacing: -1.2px; color: var(--ink); }
.result-actions { display: flex; gap: 12px; align-items: center; }
.btn-ghost { background: var(--card); border: 1px solid var(--hairline-str); color: var(--ink); padding: 8px 18px; height: 42px; display: inline-flex; align-items: center; font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none; border-radius: var(--r-full); transition: all 0.2s; }
.btn-ghost:hover { background: var(--bone); }
.btn-export { border-color: var(--primary); color: var(--primary); }
.btn-export:hover { background: rgba(234,40,4,0.05); }
.comparison-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-xxl); flex: 1; min-height: 0; }
.image-pane { display: flex; flex-direction: column; min-height: 0; }
.pane-label { font-size: 13px; font-weight: 600; color: var(--charcoal); margin-bottom: 10px; flex-shrink: 0; }
.pane-label-result { color: var(--primary); }
.pane-img-wrap { flex: 1; background: var(--bone); display: flex; align-items: center; justify-content: center; overflow: hidden; border: 1px solid var(--hairline); border-radius: var(--r-md); min-height: 0; }
.pane-img-wrap img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: var(--r-md); }

/* Loader */
.overlay-loader { position: absolute; inset: 0; background: rgba(249,247,243,0.88); backdrop-filter: blur(4px); display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 20; }
.overlay-loader.hidden { display: none; }
.spinner { border: 3px solid var(--hairline); border-top-color: var(--primary); border-radius: 50%; width: 38px; height: 38px; animation: spin 0.9s linear infinite; margin-bottom: var(--sp-xl); }
@keyframes spin { to { transform: rotate(360deg); } }
.loader-title { font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700; font-size: 22px; letter-spacing: -0.3px; color: var(--ink); margin-bottom: 6px; }
.loader-desc { color: var(--body); font-size: 12px; }

/* ── Library screen ──────────────────────────────────────── */
#screen-library { flex-direction: column; overflow: hidden; }
#library-body { flex: 1; overflow-y: auto; padding: var(--sp-xl) var(--sp-xxl) 100px; max-width: 1200px; margin: 0 auto; width: 100%; }

.library-topbar { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: var(--sp-xl); gap: 16px; flex-wrap: wrap; }
.library-title { font-family: 'Bricolage Grotesque', sans-serif; font-size: 26px; font-weight: 700; letter-spacing: -0.5px; color: var(--ink); }
.library-subtitle { font-size: 12px; color: var(--mute); margin-top: 3px; }
.library-topbar-right { display: flex; gap: 10px; align-items: center; }
.btn-secondary { background: var(--card); border: 1px solid var(--hairline-str); color: var(--ink); font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: var(--r-full); cursor: pointer; transition: all 0.15s; }
.btn-secondary:hover { background: var(--bone); }
.btn-upload-trigger { background: var(--primary); color: #fff; font-size: 13px; font-weight: 700; padding: 9px 20px; border-radius: var(--r-full); border: none; cursor: pointer; transition: background 0.15s; position: relative; }
.btn-upload-trigger:hover { background: var(--primary-deep); }

.tag-info-strip {
  background: var(--card); border: 1px solid var(--hairline); border-radius: var(--r-md);
  padding: var(--sp-md) var(--sp-lg); display: flex; align-items: flex-start; gap: 12px;
  font-size: 12px; color: var(--body); line-height: 1.5; margin-bottom: var(--sp-xl);
}
.tag-info-icon { font-size: 18px; flex-shrink: 0; }
.room-tag-example { background: #e8f4e8; color: #2d6e2d; border-radius: 3px; padding: 0 5px; font-weight: 600; font-size: 11px; }
.obj-tag-example  { background: #e8e8f4; color: #2d2d6e; border-radius: 3px; padding: 0 5px; font-weight: 600; font-size: 11px; }

/* Library sections */
.lib-section { margin-bottom: var(--sp-xxl); }
.lib-section-header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 10px; border-bottom: 1px solid var(--hairline); margin-bottom: 14px; }
.lib-section-title { font-size: 13px; font-weight: 600; color: var(--charcoal); text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 8px; }
.lib-count { background: var(--bone); color: var(--mute); font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 10px; }
.lib-expand-btn { background: none; border: 1px solid var(--hairline); color: var(--mute); font-size: 11px; padding: 4px 12px; border-radius: var(--r-full); cursor: pointer; transition: all 0.15s; }
.lib-expand-btn:hover { border-color: var(--ink); color: var(--ink); }

.lib-thumb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }
.lib-thumb-card {
  border-radius: var(--r-md); overflow: hidden; cursor: pointer;
  border: 2px solid transparent; background: var(--card);
  transition: border-color 0.15s, transform 0.1s, box-shadow 0.15s;
  position: relative; box-shadow: 0 1px 4px rgba(32,32,32,0.06);
}
.lib-thumb-card:hover { border-color: var(--hairline-str); transform: translateY(-2px); box-shadow: 0 4px 12px rgba(32,32,32,0.1); }
.lib-thumb-card.selected { border-color: var(--primary); }
.lib-thumb-card .check-mark { position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; background: var(--primary); border-radius: 50%; display: none; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color: #fff; }
.lib-thumb-card.selected .check-mark { display: flex; }
.lib-thumb-img { width: 100%; aspect-ratio: 1; object-fit: cover; }
.lib-thumb-info { padding: 8px 10px 10px; }
.lib-thumb-name { font-size: 12px; font-weight: 600; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 5px; }
.lib-thumb-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.lib-tag { font-size: 9px; font-weight: 600; padding: 2px 6px; border-radius: 3px; }
.lib-tag.room { background: #e8f4e8; color: #2d6e2d; }
.lib-tag.obj  { background: #e8e8f4; color: #2d2d6e; }
.lib-thumb-card .delete-btn { position: absolute; top: 6px; left: 6px; width: 18px; height: 18px; background: rgba(32,32,32,0.7); border-radius: 50%; display: none; align-items: center; justify-content: center; font-size: 9px; color: #fff; cursor: pointer; border: none; }
.lib-thumb-card:hover .delete-btn { display: flex; }

/* Empty library */
.library-empty-state { text-align: center; padding: 80px 0; }
.library-empty-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.4; }
.library-empty-title { font-family: 'Bricolage Grotesque', sans-serif; font-size: 22px; font-weight: 700; color: var(--ink); margin-bottom: 6px; }
.library-empty-sub { font-size: 14px; color: var(--mute); }

/* Floating bar */
.float-bar {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: var(--surface-dark); color: #fff; border-radius: var(--r-lg);
  padding: 12px 16px; display: flex; align-items: center; gap: 12px;
  box-shadow: 0 8px 32px rgba(32,32,32,0.25); z-index: 100; min-width: 460px;
  max-width: calc(100vw - 48px);
}
.float-bar.hidden { display: none; }
.float-chips { display: flex; gap: 6px; flex: 1; overflow: hidden; }
.float-chip { background: rgba(255,255,255,0.1); border-radius: var(--r-sm); padding: 4px 9px; font-size: 11px; font-weight: 600; display: flex; align-items: center; gap: 5px; white-space: nowrap; }
.float-chip img { width: 18px; height: 18px; border-radius: 3px; object-fit: cover; }
.float-count { font-size: 12px; color: rgba(255,255,255,0.55); white-space: nowrap; }
.float-clear { background: none; border: none; color: rgba(255,255,255,0.45); font-size: 11px; cursor: pointer; white-space: nowrap; }
.float-clear:hover { color: #fff; }
.btn-generate-lib { background: var(--primary); color: #fff; border: none; padding: 9px 18px; border-radius: var(--r-full); font-size: 13px; font-weight: 700; cursor: pointer; white-space: nowrap; transition: background 0.15s; display: flex; align-items: center; gap: 7px; }
.btn-generate-lib:hover { background: var(--primary-deep); }
.float-badge { background: rgba(255,255,255,0.2); border-radius: var(--r-full); padding: 1px 7px; font-size: 11px; font-weight: 800; }

/* ── Tag modal ───────────────────────────────────────────── */
.modal-overlay { position: fixed; inset: 0; background: rgba(32,32,32,0.5); backdrop-filter: blur(3px); display: flex; align-items: center; justify-content: center; z-index: 200; }
.modal-overlay.hidden { display: none; }
.modal { background: var(--card); border-radius: var(--r-lg); padding: var(--sp-xl); width: 420px; max-width: calc(100vw - 32px); box-shadow: 0 16px 48px rgba(32,32,32,0.2); }
.modal-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--sp-lg); }
.modal-title { font-family: 'Bricolage Grotesque', sans-serif; font-size: 18px; font-weight: 700; color: var(--ink); }
.modal-close { background: none; border: none; font-size: 16px; color: var(--mute); cursor: pointer; line-height: 1; }
.modal-preview-wrap { width: 100%; height: 160px; background: var(--bone); border-radius: var(--r-md); overflow: hidden; margin-bottom: var(--sp-lg); display: flex; align-items: center; justify-content: center; }
.modal-preview-wrap img { max-width: 100%; max-height: 100%; object-fit: contain; }
.modal-field { margin-bottom: var(--sp-md); }
.modal-label { display: block; font-size: 11px; font-weight: 600; color: var(--mute); text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; }
.modal-input, .modal-select { width: 100%; background: var(--bone); border: 1px solid var(--hairline); color: var(--ink); padding: 9px 12px; font-family: inherit; font-size: 13px; border-radius: var(--r-sm); outline: none; transition: border-color 0.2s; }
.modal-input:focus, .modal-select:focus { border-color: var(--primary); }
.tag-type-badge { font-size: 9px; font-weight: 600; padding: 1px 6px; border-radius: 3px; margin-left: 4px; }
.tag-type-badge.room { background: #e8f4e8; color: #2d6e2d; }
.tag-type-badge.obj  { background: #e8e8f4; color: #2d2d6e; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: var(--sp-lg); }
.modal-cancel { background: var(--bone); border: 1px solid var(--hairline); color: var(--ink); padding: 9px 18px; border-radius: var(--r-full); cursor: pointer; font-size: 13px; font-weight: 600; }
.modal-save { background: var(--primary); color: #fff; border: none; padding: 9px 20px; border-radius: var(--r-full); cursor: pointer; font-size: 13px; font-weight: 700; }
.modal-save:hover { background: var(--primary-deep); }

/* ── Toast ───────────────────────────────────────────────── */
.toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface-dark); color: #fff; padding: 11px 20px; font-size: 13px; border-radius: var(--r-full); font-weight: 500; z-index: 300; transform: translateY(120px); opacity: 0; transition: all 0.28s; box-shadow: 0 6px 20px rgba(32,32,32,0.18); pointer-events: none; }
.toast.visible { transform: translateY(0); opacity: 1; }

/* ── Responsive ─────────────────────────────────────────── */
@media (max-width: 1100px) {
  #screen-editor { grid-template-columns: 360px 300px 1fr; }
}
@media (max-width: 900px) {
  #screen-editor { grid-template-columns: 1fr; grid-template-rows: auto auto 50vh; overflow-y: auto; }
  #prompt-panel { border-right: none; border-top: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); }
  #canvas-area { min-height: 50vh; }
  .comparison-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 2: Reload `http://localhost:8000/static/index.html` — confirm it looks clean, header pill visible, editor layout 3-col, no visual errors.**

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): full CSS — tokens, header, editor layout, library, modal, floating bar"
```

---

## Task 4: JS — LibraryStore (localStorage layer)

**Files:**
- Modify: `static/index.html` — replace `/* JS added in Tasks 4–9 */` inside `<script>`

- [ ] **Step 1: Replace the `/* JS added in Tasks 4–9 */` comment with the LibraryStore and AppState:**

```js
// ── LibraryStore ─────────────────────────────────────────
const LibraryStore = {
  ASSETS_KEY:    'roomai_assets',
  ROOM_TAGS_KEY: 'roomai_custom_room_tags',
  OBJ_TAGS_KEY:  'roomai_custom_object_tags',

  PRESET_ROOM_TAGS: ['Living Room','Bedroom','Kitchen','Bathroom','Dining Room','Office','Kids Room'],
  PRESET_OBJ_TAGS:  ['Sofa','Chair','Bed frame','Table','Lamp','Curtain','Rug','Mirror','Plant','Decor','Storage'],

  getAssets() {
    try { return JSON.parse(localStorage.getItem(this.ASSETS_KEY) || '[]'); }
    catch { return []; }
  },
  saveAsset(asset) {
    const arr = this.getAssets();
    arr.push(asset);
    localStorage.setItem(this.ASSETS_KEY, JSON.stringify(arr));
  },
  deleteAsset(id) {
    const arr = this.getAssets().filter(a => a.id !== id);
    localStorage.setItem(this.ASSETS_KEY, JSON.stringify(arr));
  },
  getCustomRoomTags() {
    try { return JSON.parse(localStorage.getItem(this.ROOM_TAGS_KEY) || '[]'); }
    catch { return []; }
  },
  getCustomObjTags() {
    try { return JSON.parse(localStorage.getItem(this.OBJ_TAGS_KEY) || '[]'); }
    catch { return []; }
  },
  addCustomRoomTag(tag) {
    const tags = this.getCustomRoomTags();
    if (tag && !tags.includes(tag)) { tags.push(tag); localStorage.setItem(this.ROOM_TAGS_KEY, JSON.stringify(tags)); }
  },
  addCustomObjTag(tag) {
    const tags = this.getCustomObjTags();
    if (tag && !tags.includes(tag)) { tags.push(tag); localStorage.setItem(this.OBJ_TAGS_KEY, JSON.stringify(tags)); }
  },
  allRoomTags() { return [...this.PRESET_ROOM_TAGS, ...this.getCustomRoomTags()]; },
  allObjTags()  { return [...this.PRESET_OBJ_TAGS,  ...this.getCustomObjTags()]; },
  groupByRoom() {
    const assets = this.getAssets();
    const map = new Map();
    for (const a of assets) {
      const key = a.roomTag || 'Untagged';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(a);
    }
    return map;
  },
};

// ── AppState ──────────────────────────────────────────────
const AppState = {
  currentScreen:    'editor',  // 'editor' | 'library'
  libSelectedIds:   new Set(), // selected asset IDs on library screen
  editorSelectedIds: new Set(),// selected asset IDs in editor library mode
  assetMode:        'upload',  // 'upload' | 'library'
  baseBlob:         null,
  baseURL:          null,
  uploadedObjBlob:  null,
  uploadedObjURL:   null,
  userEditedPrompt: false,
  selectedAction:   'Add to room',
  selectedStyles:   ['Minimalist'],
  selectedPlacement:'Center wall',

  getLibSelected() {
    return LibraryStore.getAssets().filter(a => this.libSelectedIds.has(a.id));
  },
  getEditorSelected() {
    return LibraryStore.getAssets().filter(a => this.editorSelectedIds.has(a.id));
  },
};
```

- [ ] **Step 2: Open browser console at `http://localhost:8000/static/index.html`, run:**

```js
LibraryStore.saveAsset({id:'test1',name:'Test',dataUrl:'data:image/png;base64,iVBORw0KGgo=',roomTag:'Bedroom',objectTag:'Lamp',createdAt:Date.now()});
console.log(LibraryStore.getAssets()); // should log array with 1 item
LibraryStore.deleteAsset('test1');
console.log(LibraryStore.getAssets()); // should log []
```

Expected: array with 1 item, then empty array.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): LibraryStore localStorage layer and AppState singleton"
```

---

## Task 5: JS — tag modal (upload + tagging flow)

**Files:**
- Modify: `static/index.html` — append to `<script>` block

- [ ] **Step 1: Append the following after the AppState block inside `<script>`:**

```js
// ── Utilities ─────────────────────────────────────────────
const $ = id => document.getElementById(id);

function showToast(msg, duration = 3500) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('visible');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('visible'), duration);
}

function generateId() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function dataUrlToBlob(url) {
  const parts = url.split(',');
  const mime = parts[0].match(/:(.*?);/)[1];
  const bin = atob(parts[1]);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

// ── Tag Modal ─────────────────────────────────────────────
let _modalPendingDataUrl = null;
let _modalPendingFile = null;

function populateModalSelects() {
  const roomSel = $('modalRoomSelect');
  const objSel  = $('modalObjSelect');
  roomSel.innerHTML = '<option value="">— choose or type custom —</option>';
  objSel.innerHTML  = '<option value="">— choose or type custom —</option>';
  for (const t of LibraryStore.allRoomTags()) {
    roomSel.insertAdjacentHTML('beforeend', `<option value="${t}">${t}</option>`);
  }
  for (const t of LibraryStore.allObjTags()) {
    objSel.insertAdjacentHTML('beforeend', `<option value="${t}">${t}</option>`);
  }
}

function openTagModal(dataUrl) {
  _modalPendingDataUrl = dataUrl;
  $('modalPreviewImg').src = dataUrl;
  $('modalName').value = '';
  $('modalRoomCustom').value = '';
  $('modalObjCustom').value = '';
  populateModalSelects();
  $('tagModal').classList.remove('hidden');
}

function closeTagModal() {
  $('tagModal').classList.add('hidden');
  _modalPendingDataUrl = null;
}

function saveFromModal() {
  const name      = $('modalName').value.trim();
  const roomTag   = $('modalRoomCustom').value.trim() || $('modalRoomSelect').value || 'Untagged';
  const objectTag = $('modalObjCustom').value.trim()  || $('modalObjSelect').value  || 'Item';
  if (!name) return showToast('Please enter an asset name.');
  if (!_modalPendingDataUrl) return;

  if ($('modalRoomCustom').value.trim()) LibraryStore.addCustomRoomTag(roomTag);
  if ($('modalObjCustom').value.trim())  LibraryStore.addCustomObjTag(objectTag);

  LibraryStore.saveAsset({
    id: generateId(),
    name,
    dataUrl: _modalPendingDataUrl,
    roomTag,
    objectTag,
    createdAt: Date.now(),
  });

  closeTagModal();
  renderLibrary();
  showToast(`"${name}" saved to Library.`);
}

$('modalClose').addEventListener('click', closeTagModal);
$('modalCancel').addEventListener('click', closeTagModal);
$('modalSave').addEventListener('click', saveFromModal);
$('tagModal').addEventListener('click', e => { if (e.target === $('tagModal')) closeTagModal(); });

// Library file upload trigger
$('libUploadTrigger').addEventListener('click', () => $('libFileInput').click());
$('libFileInput').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file || !file.type.startsWith('image/')) return;
  const reader = new FileReader();
  reader.onload = ev => openTagModal(ev.target.result);
  reader.readAsDataURL(file);
  e.target.value = '';
});
```

- [ ] **Step 2: Open `http://localhost:8000/static/index.html`, click "Add Asset", pick any image — modal should open showing the image preview and tag dropdowns. Click Cancel — modal closes.**

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): tag modal — upload, preview, room/object tagging, save to localStorage"
```

---

## Task 6: JS — Library screen rendering + selection + floating bar

**Files:**
- Modify: `static/index.html` — append to `<script>` block

- [ ] **Step 1: Append the following after the tag modal block inside `<script>`:**

```js
// ── Library Rendering ─────────────────────────────────────
function renderLibrary() {
  const container = $('library-grid-container');
  const emptyEl   = $('library-empty');
  const grouped   = LibraryStore.groupByRoom();

  if (grouped.size === 0) {
    container.innerHTML = '';
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');

  container.innerHTML = '';
  for (const [roomTag, assets] of grouped) {
    const COLLAPSED_COUNT = 6;
    const isExpanded = container.dataset['expanded_' + roomTag] === '1';
    const visible    = isExpanded ? assets : assets.slice(0, COLLAPSED_COUNT);
    const hasMore    = !isExpanded && assets.length > COLLAPSED_COUNT;

    const section = document.createElement('div');
    section.className = 'lib-section';
    section.innerHTML = `
      <div class="lib-section-header">
        <div class="lib-section-title">${roomTag} <span class="lib-count">${assets.length}</span></div>
        ${assets.length > COLLAPSED_COUNT
          ? `<button class="lib-expand-btn" data-room="${roomTag}" data-expanded="${isExpanded ? '1' : '0'}">
               ${isExpanded ? '⌃ Collapse' : '⤢ Expand all'}
             </button>`
          : ''}
      </div>
      <div class="lib-thumb-grid">
        ${visible.map(a => renderThumbCard(a, AppState.libSelectedIds.has(a.id))).join('')}
        ${hasMore ? `<div class="lib-more-row" style="grid-column:1/-1;text-align:center;padding:6px 0;">
          <button class="lib-expand-btn" data-room="${roomTag}" data-expanded="0" style="width:auto">
            + ${assets.length - COLLAPSED_COUNT} more
          </button></div>` : ''}
      </div>`;

    section.querySelectorAll('.lib-expand-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const expanded = btn.dataset.expanded === '1' ? '0' : '1';
        container.dataset['expanded_' + btn.dataset.room] = expanded;
        renderLibrary();
      });
    });

    section.querySelectorAll('.lib-thumb-card').forEach(card => {
      card.addEventListener('click', e => {
        if (e.target.classList.contains('delete-btn')) {
          LibraryStore.deleteAsset(card.dataset.id);
          AppState.libSelectedIds.delete(card.dataset.id);
          renderLibrary();
          updateFloatBar();
          renderEditorLibraryGrid();
          return;
        }
        toggleLibSelection(card.dataset.id);
      });
    });

    container.appendChild(section);
  }
}

function renderThumbCard(asset, selected) {
  return `
    <div class="lib-thumb-card${selected ? ' selected' : ''}" data-id="${asset.id}">
      <button class="delete-btn" title="Delete">✕</button>
      <div class="check-mark">✓</div>
      <img class="lib-thumb-img" src="${asset.dataUrl}" alt="${asset.name}" loading="lazy" />
      <div class="lib-thumb-info">
        <div class="lib-thumb-name">${asset.name}</div>
        <div class="lib-thumb-tags">
          <span class="lib-tag room">${asset.roomTag}</span>
          <span class="lib-tag obj">${asset.objectTag}</span>
        </div>
      </div>
    </div>`;
}

function toggleLibSelection(id) {
  if (AppState.libSelectedIds.has(id)) {
    AppState.libSelectedIds.delete(id);
  } else {
    if (AppState.libSelectedIds.size >= 5) { showToast('Max 5 objects selected.'); return; }
    AppState.libSelectedIds.add(id);
  }
  renderLibrary();
  updateFloatBar();
}

// ── Floating bar ──────────────────────────────────────────
function updateFloatBar() {
  const selected = AppState.getLibSelected();
  const bar      = $('floatBar');
  if (selected.length === 0) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');

  $('floatCount').textContent = `${selected.length} / 5`;
  $('floatBadge').textContent = selected.length;
  $('floatChips').innerHTML = selected.slice(0, 4).map(a =>
    `<div class="float-chip"><img src="${a.dataUrl}" alt="" />${a.name}</div>`
  ).join('') + (selected.length > 4 ? `<div class="float-chip">+${selected.length - 4}</div>` : '');
}

$('floatClear').addEventListener('click', () => {
  AppState.libSelectedIds.clear();
  renderLibrary();
  updateFloatBar();
});

$('libGenerateBtn').addEventListener('click', () => {
  if (AppState.libSelectedIds.size === 0) return;
  // Carry selections to editor
  AppState.editorSelectedIds = new Set(AppState.libSelectedIds);
  switchScreen('editor');
  // Switch editor to library mode so selections are visible
  setEditorAssetMode('library');
  renderEditorLibraryGrid();
  updateEditorChips();
  updatePromptFromTags();
});
```

- [ ] **Step 2: Append the screen switcher to `<script>` (after the library block):**

```js
// ── Screen switching ─────────────────────────────────────
function switchScreen(name) {
  AppState.currentScreen = name;
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $(`screen-${name}`).classList.add('active');
  document.querySelectorAll('.nav-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.screen === name);
  });
  if (name === 'library') renderLibrary();
}

document.querySelectorAll('.nav-pill').forEach(btn => {
  btn.addEventListener('click', () => switchScreen(btn.dataset.screen));
});
```

- [ ] **Step 3: Open browser, switch to Library tab — if localStorage is empty the empty state shows. Add one asset via tag modal. Confirm it appears in a section. Select it — orange border + floating bar appears. Click clear — bar hides.**

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): library screen rendering, selection, floating bar, screen switcher"
```

---

## Task 7: JS — Editor screen wiring (room upload + asset toggle + inline library)

**Files:**
- Modify: `static/index.html` — append to `<script>` block

- [ ] **Step 1: Append the following inside `<script>`:**

```js
// ── Editor: room image ────────────────────────────────────
function setBaseImage(blob, url) {
  AppState.baseBlob = blob;
  AppState.baseURL  = url;
  if (blob) {
    $('roomZone').style.display = 'none';
    $('roomThumb').src = url;
    $('roomMini').classList.remove('hidden');
    $('section-assets').classList.remove('disabled');
  } else {
    $('roomZone').style.display = '';
    $('roomMini').classList.add('hidden');
    $('section-assets').classList.add('disabled');
    setUploadedObj(null, null);
  }
  updateCanvasState();
}

function setUploadedObj(blob, url) {
  AppState.uploadedObjBlob = blob;
  AppState.uploadedObjURL  = url;
  if (blob) {
    $('objZone').style.display = 'none';
    $('objThumb').src = url;
    $('objMini').classList.remove('hidden');
  } else {
    $('objZone').style.display = '';
    $('objMini').classList.add('hidden');
  }
  updateCanvasState();
}

function updateCanvasState() {
  if ($('resultView') && !$('resultView').classList.contains('hidden')) return;
  if (AppState.baseURL) {
    $('emptyState').style.display = 'none';
    $('canvasMainImg').src = AppState.baseURL;
    $('viewport').classList.remove('hidden');
    const hasObj = AppState.assetMode === 'upload'
      ? !!AppState.uploadedObjURL
      : AppState.editorSelectedIds.size > 0;
    if (hasObj) {
      const src = AppState.assetMode === 'upload'
        ? AppState.uploadedObjURL
        : AppState.getEditorSelected()[0]?.dataUrl || '';
      $('canvasAssetImg').src = src;
      $('viewToggles').classList.remove('hidden');
    } else {
      $('viewToggles').classList.add('hidden');
      showSceneView();
    }
  } else {
    $('emptyState').style.display = '';
    $('viewport').classList.add('hidden');
  }
}

function showSceneView() {
  $('btnViewScene').classList.add('active');
  $('btnViewAsset').classList.remove('active');
  $('canvasMainImg').style.display = '';
  $('canvasAssetImg').style.display = 'none';
}

function handleRead(file, cb) {
  if (!file || !file.type.startsWith('image/')) return;
  const r = new FileReader();
  r.onload = e => cb(file, e.target.result);
  r.readAsDataURL(file);
}

$('roomInput').addEventListener('change', e => handleRead(e.target.files[0], setBaseImage));
$('roomRemove').addEventListener('click', () => { $('roomInput').value = ''; setBaseImage(null, null); $('resultView').classList.add('hidden'); });
$('objInput').addEventListener('change', e => handleRead(e.target.files[0], setUploadedObj));
$('objRemove').addEventListener('click', () => { $('objInput').value = ''; setUploadedObj(null, null); });

$('btnViewScene').addEventListener('click', showSceneView);
$('btnViewAsset').addEventListener('click', () => {
  $('btnViewAsset').classList.add('active');
  $('btnViewScene').classList.remove('active');
  $('canvasMainImg').style.display = 'none';
  $('canvasAssetImg').style.display = '';
});

// ── Asset mode toggle ─────────────────────────────────────
function setEditorAssetMode(mode) {
  AppState.assetMode = mode;
  document.querySelectorAll('.asset-toggle-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  $('asset-upload-mode').style.display  = mode === 'upload'  ? '' : 'none';
  $('asset-library-mode').style.display = mode === 'library' ? '' : 'none';
  updateCanvasState();
}

document.querySelectorAll('.asset-toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    setEditorAssetMode(btn.dataset.mode);
    if (btn.dataset.mode === 'library') renderEditorLibraryGrid();
  });
});

// ── Editor inline library grid ───────────────────────────
function renderEditorLibraryGrid() {
  const container = $('editor-lib-grid');
  const grouped   = LibraryStore.groupByRoom();

  if (grouped.size === 0) {
    container.innerHTML = '<div class="elib-empty">No assets in library yet.<br>Go to <strong>Library</strong> tab to add some.</div>';
    return;
  }

  container.innerHTML = '';
  for (const [roomTag, assets] of grouped) {
    const COLLAPSED = 5;
    const expandKey = 'elib_expanded_' + roomTag;
    const isExpanded = container.dataset[expandKey] === '1';
    const visible    = isExpanded ? assets : assets.slice(0, COLLAPSED);
    const hasMore    = !isExpanded && assets.length > COLLAPSED;

    const sec = document.createElement('div');
    sec.className = 'elib-section';
    sec.innerHTML = `
      <div class="elib-section-header">
        <span>${roomTag}</span>
        ${assets.length > COLLAPSED
          ? `<button class="elib-expand" data-room="${roomTag}" data-expanded="${isExpanded ? '1' : '0'}">${isExpanded ? '▲' : '▼ ' + (assets.length - COLLAPSED) + ' more'}</button>`
          : ''}
      </div>
      <div class="elib-thumbs">
        ${visible.map(a => `
          <div class="elib-thumb${AppState.editorSelectedIds.has(a.id) ? ' selected' : ''}" data-id="${a.id}" title="${a.name}">
            <img src="${a.dataUrl}" alt="${a.name}" loading="lazy" />
            <div class="check">✓</div>
          </div>`).join('')}
      </div>`;

    if (hasMore) {
      sec.querySelector('.elib-expand')?.addEventListener('click', () => {
        container.dataset[expandKey] = '1';
        renderEditorLibraryGrid();
      });
    }

    sec.querySelectorAll('.elib-thumb').forEach(thumb => {
      thumb.addEventListener('click', () => {
        const id = thumb.dataset.id;
        if (AppState.editorSelectedIds.has(id)) {
          AppState.editorSelectedIds.delete(id);
        } else {
          if (AppState.editorSelectedIds.size >= 5) { showToast('Max 5 objects selected.'); return; }
          AppState.editorSelectedIds.add(id);
        }
        updateEditorAssetCountBadge();
        updateEditorChips();
        updatePromptFromTags();
        updateCanvasState();
        renderEditorLibraryGrid();
      });
    });

    container.appendChild(sec);
  }
}

function updateEditorAssetCountBadge() {
  const badge = $('assetCountBadge');
  const count = AppState.assetMode === 'library' ? AppState.editorSelectedIds.size : (AppState.uploadedObjBlob ? 1 : 0);
  if (count > 0) { badge.textContent = `${count} selected`; badge.style.display = ''; }
  else badge.style.display = 'none';
}

// Iterate layer (reuse result as base)
$('refineBtn').addEventListener('click', () => {
  const url = $('afterImg').src;
  setBaseImage(dataUrlToBlob(url), url);
  setUploadedObj(null, null);
  AppState.editorSelectedIds.clear();
  $('promptInput').value = '';
  $('resultView').classList.add('hidden');
  updateCanvasState();
});
```

- [ ] **Step 2: Refresh browser. Upload a room image — it should show in canvas and enable the Subject Assets step. Toggle to "From Library" mode — the inline grid renders (or shows the empty hint). Toggle back to upload.**

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): editor room upload, asset toggle, inline library grid"
```

---

## Task 8: JS — Prompt panel (chips, tags, auto-builder)

**Files:**
- Modify: `static/index.html` — append to `<script>` block

- [ ] **Step 1: Append the following inside `<script>`:**

```js
// ── Prompt panel ─────────────────────────────────────────

function updateEditorChips() {
  const row = $('selected-chips');
  const assets = AppState.assetMode === 'library'
    ? AppState.getEditorSelected()
    : (AppState.uploadedObjBlob ? [{ id: '_upload', name: 'Uploaded asset', dataUrl: AppState.uploadedObjURL, objectTag: 'Object' }] : []);

  if (assets.length === 0) {
    row.innerHTML = '<span class="chips-empty">No objects selected</span>';
    return;
  }

  row.innerHTML = assets.map(a => `
    <div class="chip" data-id="${a.id}">
      <img class="chip-thumb" src="${a.dataUrl}" alt="" />
      ${a.objectTag || a.name}
      <span class="chip-remove" data-id="${a.id}">✕</span>
    </div>`).join('');

  row.querySelectorAll('.chip-remove').forEach(x => {
    x.addEventListener('click', e => {
      e.stopPropagation();
      const id = x.dataset.id;
      if (id === '_upload') { setUploadedObj(null, null); }
      else { AppState.editorSelectedIds.delete(id); renderEditorLibraryGrid(); }
      updateEditorAssetCountBadge();
      updateEditorChips();
      updatePromptFromTags();
      updateCanvasState();
    });
  });

  if (!AppState.userEditedPrompt) updatePromptFromTags();
}

function buildAutoPrompt() {
  const assets = AppState.assetMode === 'library'
    ? AppState.getEditorSelected()
    : (AppState.uploadedObjBlob ? [{ objectTag: 'the object' }] : []);

  const objList = assets.map(a => a.objectTag || a.name).join(', ') || 'the selected objects';
  const action  = AppState.selectedAction;
  const styles  = AppState.selectedStyles.join(', ') || 'natural';
  const place   = AppState.selectedPlacement || 'naturally';

  return `${action} ${objList} into this room in a ${styles} style. ` +
         `Place ${assets.length === 1 ? 'it' : 'them'} ${place.toLowerCase()}. ` +
         `Maintain photorealism and match existing lighting and perspective.`;
}

function updatePromptFromTags() {
  if (AppState.userEditedPrompt) return;
  $('promptInput').value = buildAutoPrompt();
}

// Tag click handlers
document.querySelectorAll('#action-tags .tag').forEach(tag => {
  tag.addEventListener('click', () => {
    document.querySelectorAll('#action-tags .tag').forEach(t => t.classList.remove('active'));
    tag.classList.add('active');
    AppState.selectedAction = tag.textContent;
    updatePromptFromTags();
  });
});

document.querySelectorAll('#style-tags .tag').forEach(tag => {
  tag.addEventListener('click', () => {
    tag.classList.toggle('active');
    AppState.selectedStyles = [...document.querySelectorAll('#style-tags .tag.active')].map(t => t.textContent);
    updatePromptFromTags();
  });
});

document.querySelectorAll('#placement-tags .tag').forEach(tag => {
  tag.addEventListener('click', () => {
    document.querySelectorAll('#placement-tags .tag').forEach(t => t.classList.remove('active'));
    tag.classList.add('active');
    AppState.selectedPlacement = tag.textContent;
    updatePromptFromTags();
  });
});

// Manual edit lock
$('promptInput').addEventListener('input', () => {
  AppState.userEditedPrompt = true;
});
```

- [ ] **Step 2: Refresh browser. Set editor to Library mode (with an asset in library). Select an asset — chips row should update to show the asset. Click Action tag "Replace existing" — prompt textarea should auto-update. Type in textarea — auto-update should stop. Select a different asset — textarea updates again (reset lock).**

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): prompt panel — chips, action/style/placement tags, auto-prompt builder"
```

---

## Task 9: JS — Generate submission (multi-object formData + result display)

**Files:**
- Modify: `static/index.html` — append to `<script>` block

- [ ] **Step 1: Append the following inside `<script>`:**

```js
// ── Generate ─────────────────────────────────────────────
$('generateBtn').addEventListener('click', async () => {
  if (!AppState.baseBlob) return showToast('Upload a room scene first.');
  const prompt = $('promptInput').value.trim();
  if (!prompt) return showToast('Add a directive or select tags above.');

  // Gather object assets
  let objectAssets = [];
  let objectTags   = [];
  if (AppState.assetMode === 'library') {
    objectAssets = AppState.getEditorSelected();
    objectTags   = objectAssets.map(a => a.objectTag || a.name);
  } else if (AppState.uploadedObjBlob) {
    objectAssets = [{ dataUrl: AppState.uploadedObjURL }];
    objectTags   = ['object'];
  }

  $('loader').classList.remove('hidden');
  $('resultView').classList.add('hidden');
  $('viewport').classList.add('hidden');
  $('emptyState').style.display = 'none';

  try {
    const formData = new FormData();
    const ext = AppState.baseBlob.type === 'image/jpeg' ? 'jpg' : 'png';
    formData.append('room_image', new File([AppState.baseBlob], `room.${ext}`, { type: AppState.baseBlob.type }));
    formData.append('prompt', prompt);
    formData.append('object_tags', JSON.stringify(objectTags));

    for (let i = 0; i < Math.min(objectAssets.length, 5); i++) {
      const asset = objectAssets[i];
      let blob;
      if (asset.dataUrl) {
        blob = dataUrlToBlob(asset.dataUrl);
      } else {
        continue;
      }
      formData.append(`object_image_${i + 1}`, new File([blob], `obj${i + 1}.png`, { type: 'image/png' }));
    }

    const res  = await fetch('/edit', { method: 'POST', body: formData });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || `Error ${res.status}`);

    const b64      = (json.images_b64?.length) ? json.images_b64[0] : json.image_b64;
    const finalUrl = `data:image/${json.format};base64,${b64}`;

    $('beforeImg').src   = AppState.baseURL;
    $('afterImg').src    = finalUrl;
    $('downloadBtn').href = finalUrl;
    $('resultView').classList.remove('hidden');
  } catch (err) {
    showToast(err.message);
    updateCanvasState();
  } finally {
    $('loader').classList.add('hidden');
  }
});
```

- [ ] **Step 2: Append the initialisation call at the very end of `<script>`:**

```js
// ── Init ─────────────────────────────────────────────────
updateEditorChips();
updatePromptFromTags();
```

- [ ] **Step 3: Start the server and run an end-to-end test:**

```bash
uvicorn main:app --reload
```

Open `http://localhost:8000/static/index.html`:
1. Go to Library — add 1 asset, tag it (e.g. "Bedroom" / "Lamp")
2. Select it → float bar appears
3. Click "Generate →" — switches to Editor, Library mode active, asset chip shown
4. Upload a room photo
5. Check prompt textarea has auto-built prompt
6. Click "Process Render" → loader shows → result appears

Expected: before/after comparison renders.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): multi-object generate submission, result display, full end-to-end flow"
```

---

## Task 10: Polish — gitignore, CLAUDE.md update

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `.superpowers/` to `.gitignore`**

If `.gitignore` exists, append; otherwise create it:

```
.superpowers/
uploads/
outputs/
__pycache__/
*.pyc
.env
```

- [ ] **Step 2: Update `CLAUDE.md` — add Library section:**

In `CLAUDE.md`, add after the existing Architecture section:

```markdown
## Feature: Library + multi-object

- `static/index.html` contains all frontend — two screens (Editor / Library), toggled via JS `switchScreen()`
- `LibraryStore` — localStorage CRUD at keys `roomai_assets`, `roomai_custom_room_tags`, `roomai_custom_object_tags`
- `AppState` — in-memory singleton for current screen, selection sets, asset mode, prompt lock flag
- Multi-object: up to 5 assets selected → sent as `object_image_1..5` → backend stitches into composite grid before OpenAI call
- Auto-prompt builder locks (`userEditedPrompt=true`) on manual textarea edit; resets when object selection changes
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore CLAUDE.md
git commit -m "chore: update gitignore and CLAUDE.md with library feature docs"
```

---

## Self-review against spec

| Spec section | Covered by |
|---|---|
| Header pill nav | Task 2 HTML + Task 3 CSS + Task 6 switchScreen |
| Library upload + tag modal | Task 5 |
| Room tag + object tag + custom tags | Task 5 LibraryStore + modal |
| Asset grid grouped by room, expand button | Task 6 renderLibrary |
| Selection up to 5, toast on 6th | Task 6 toggleLibSelection |
| Floating bar with chips + Generate → | Task 6 updateFloatBar |
| Library → Editor carries selections | Task 6 libGenerateBtn handler |
| Editor room image upload + replace | Task 7 |
| Asset toggle Upload / Library | Task 7 setEditorAssetMode |
| Editor inline library grid | Task 7 renderEditorLibraryGrid |
| Selected object chips with remove | Task 8 updateEditorChips |
| Action / Style / Placement tags | Task 8 tag click handlers |
| Auto-prompt builder + lock | Task 8 buildAutoPrompt + input listener |
| Cost note | Task 2 HTML |
| Generate locked without room image | Task 9 guard check |
| Multi-object formData | Task 9 generate handler |
| Backend composite builder | Task 1 build_object_composite |
| Backend new endpoint 5 inputs | Task 1 /edit replacement |
| Before/after result display | Task 9 (unchanged structure from original) |
| Iterate layer button | Task 7 refineBtn |
| localStorage schema | Task 4 LibraryStore |
