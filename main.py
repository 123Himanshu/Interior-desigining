import os
import base64
import uuid
import math
import json as _json
from pathlib import Path
from io import BytesIO

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from PIL import Image, ImageDraw
import openai

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in environment. Please add it to your .env file.")

# ── HuggingFace dataset sync (optional) ───────────────────────
HF_TOKEN       = os.getenv("HF_TOKEN", "")
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "")  # e.g. "username/roomai-library"
LIBRARY_FILE   = "library.json"
_hf_enabled    = bool(HF_TOKEN and HF_DATASET_REPO)

if _hf_enabled:
    from huggingface_hub import HfApi
    _hf_api = HfApi(token=HF_TOKEN)


def _hf_load() -> list:
    """Download library.json from HF dataset and return parsed list."""
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


def _hf_save(assets: list) -> None:
    """Upload library.json to HF dataset."""
    try:
        content = _json.dumps(assets, ensure_ascii=False).encode("utf-8")
        _hf_api.upload_file(
            path_or_fileobj=content,
            path_in_repo=LIBRARY_FILE,
            repo_id=HF_DATASET_REPO,
            repo_type="dataset",
            commit_message="Update library",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HF save failed: {e}")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

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
    """Convert image to PNG, resize if needed, keep under 4 MB."""
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
    """Stitch up to 5 object images into a labelled 2-column grid PNG."""
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
    """Build a structured prompt. object_tags is an ordered list of tag names."""
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


@app.get("/health")
async def health():
    return {"status": "ok", "hf_sync": _hf_enabled}


# ── Library endpoints ──────────────────────────────────────────

@app.get("/library")
async def get_library():
    """Return the asset library. Loads from HF if configured, else returns empty."""
    if not _hf_enabled:
        return JSONResponse({"enabled": False, "assets": []})
    assets = _hf_load()
    return JSONResponse({"enabled": True, "assets": assets})


@app.post("/library")
async def save_library(request: Request):
    """Replace the entire library with the posted asset list."""
    if not _hf_enabled:
        return JSONResponse({"enabled": False, "saved": False})
    body = await request.json()
    assets = body.get("assets", [])
    if not isinstance(assets, list):
        raise HTTPException(status_code=400, detail="assets must be an array")
    _hf_save(assets)
    return JSONResponse({"enabled": True, "saved": True, "count": len(assets)})


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

    try:
        tags: list = _json.loads(object_tags)
    except Exception:
        tags = []

    # Prepare room image
    room_bytes = await room_image.read()
    room_png   = prepare_image(room_bytes)
    room_id    = uuid.uuid4().hex
    room_path  = UPLOADS_DIR / f"{room_id}_room.png"
    room_path.write_bytes(room_png)

    # Collect object images
    raw_objects = [object_image_1, object_image_2, object_image_3, object_image_4, object_image_5]
    pil_objects = []
    for i, obj_upload in enumerate(raw_objects):
        if obj_upload and obj_upload.filename:
            obj_bytes = await obj_upload.read()
            obj_png   = prepare_image(obj_bytes)
            (UPLOADS_DIR / f"{room_id}_obj{i + 1}.png").write_bytes(obj_png)
            pil_objects.append(Image.open(BytesIO(obj_png)).convert("RGBA"))

    # Build prompt
    final_prompt = build_edit_prompt(prompt, tags[:len(pil_objects)])

    # Build reference: composite if >1 object, single PNG if exactly 1
    reference_png: bytes | None = None
    if len(pil_objects) > 1:
        reference_png = build_object_composite(pil_objects, tags)
        (UPLOADS_DIR / f"{room_id}_composite.png").write_bytes(reference_png)
    elif len(pil_objects) == 1:
        buf = BytesIO()
        pil_objects[0].save(buf, format="PNG")
        reference_png = buf.getvalue()

    # Call OpenAI
    try:
        if reference_png:
            response = client.images.edit(
                model="gpt-image-1.5",
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
                model="gpt-image-1.5",
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
            result_b64   = image_data.b64_json
            result_bytes = base64.b64decode(result_b64)
        elif hasattr(image_data, "url") and image_data.url:
            import requests as req
            result_bytes = req.get(image_data.url, timeout=30).content
            result_b64   = base64.b64encode(result_bytes).decode()
        else:
            continue
        (OUTPUTS_DIR / f"{room_id}_result_{idx + 1}.png").write_bytes(result_bytes)
        results_b64.append(result_b64)

    if not results_b64:
        raise HTTPException(status_code=500, detail="No image data returned from OpenAI.")

    return JSONResponse({"image_b64": results_b64[0], "images_b64": results_b64, "format": "png"})


# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
