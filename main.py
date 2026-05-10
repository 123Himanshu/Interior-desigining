import os
import base64
import uuid
from pathlib import Path
from io import BytesIO

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from PIL import Image
import openai

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in environment. Please add it to your .env file.")

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

MAX_SIZE_BYTES = 4 * 1024 * 1024   # 4 MB
MAX_DIMENSION  = 1024               # px


def prepare_image(file_bytes: bytes) -> bytes:
    """Convert image to PNG, resize if needed, keep under 4 MB."""
    img = Image.open(BytesIO(file_bytes)).convert("RGBA")

    # Resize if too large
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    # If still too large, reduce quality iteratively
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


def build_edit_prompt(user_prompt: str, has_object_reference: bool) -> str:
    """Build a structured prompt for reliable interior edits using industry-grade constraints."""
    reference_instruction = (
        "CRITICAL: Use the second uploaded image as the specific visual reference for the new furniture/object. "
        "Match its shape perfectly. Extract and apply its exact materiality and textures. Ground it with realistic contact shadows that follow the room's primary light direction."
        if has_object_reference
        else "Do not introduce unrelated new furniture, decor, or humans unless explicitly requested by the user."
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
    return {"status": "ok"}


@app.post("/edit")
async def edit_room(
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    object_image: UploadFile = File(None),
):
    # ── Validate inputs ──────────────────────────────────────────────────────
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if room_image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Room image must be JPEG, PNG, WebP, or GIF.")

    # ── Read & prepare room image ─────────────────────────────────────────────
    room_bytes = await room_image.read()
    room_png   = prepare_image(room_bytes)

    # Save to disk (optional, useful for debugging)
    room_id   = uuid.uuid4().hex
    room_path = UPLOADS_DIR / f"{room_id}_room.png"
    room_path.write_bytes(room_png)

    # ── Build prompt ──────────────────────────────────────────────────────────
    final_prompt = build_edit_prompt(prompt, bool(object_image and object_image.filename))

    # ── Optional object image ─────────────────────────────────────────────────
    object_png_bytes = None
    if object_image and object_image.filename:
        obj_bytes        = await object_image.read()
        object_png_bytes = prepare_image(obj_bytes)
        obj_path         = UPLOADS_DIR / f"{room_id}_object.png"
        obj_path.write_bytes(object_png_bytes)
    # ── Call OpenAI images.edit ───────────────────────────────────────────────
    try:
        room_file_tuple = (room_path.name, room_png, "image/png")

        if object_png_bytes:
            # Pass both images as a list
            obj_path_name = UPLOADS_DIR / f"{room_id}_object.png"
            response = client.images.edit(
                model="gpt-image-1.5",
                image=[
                    (room_path.name,         room_png,         "image/png"),
                    (obj_path_name.name,     object_png_bytes, "image/png"),
                ],
                prompt=final_prompt,
                n=1,
                size="1024x1024",
            )
        else:
            response = client.images.edit(
                model="gpt-image-1.5",
                image=room_file_tuple,
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

    # ── Decode result ─────────────────────────────────────────────────────────
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

        out_path = OUTPUTS_DIR / f"{room_id}_result_{idx+1}.png"
        out_path.write_bytes(result_bytes)
        results_b64.append(result_b64)

    if not results_b64:
        raise HTTPException(status_code=500, detail="No image data returned from OpenAI.")

    return JSONResponse({
        "image_b64": results_b64[0],
        "images_b64": results_b64,
        "format": "png"
    })


# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
