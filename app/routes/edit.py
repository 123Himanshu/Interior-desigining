import base64
import uuid
import math
import json as _json
import traceback
import sys
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.routes.auth import get_current_user
from app.services.users import charge_credit, refund_credit
from app.services.generation import (
    prepare_image, generate_modelslab, generate_openai,
    generate_interior_make, generate_room_decorator, generate_floor_plan,
    generate_object_removal, generate_scenario_change, generate_sketch_render,
    generate_exterior_restore, SCENARIOS,
)
from app.services.backup import schedule_backup
from app.config import USE_MODEL_LABS

router = APIRouter()

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

MAX_PROMPT_LENGTH = 4000


def build_composite(pil_images: list, tags: list) -> bytes:
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


def build_prompt(user_prompt: str, object_tags: list) -> str:
    n = len(object_tags)
    if n == 0:
        ref = "Do not introduce unrelated new furniture, decor, or humans unless explicitly requested."
    elif n == 1:
        ref = f"CRITICAL: Use the second uploaded image as the exact visual reference for the '{object_tags[0]}'. Match its shape, proportions, and silhouette precisely. Extract and faithfully reproduce its materiality, surface textures, color, and finish. Scale it correctly relative to the room and surrounding furniture. Ground it with realistic contact shadows that follow the room's primary light direction."
    else:
        guide = "; ".join(f"Cell {i + 1}: the reference '{tag}'" for i, tag in enumerate(object_tags))
        ref = f"OBJECT REFERENCE GUIDE: The second uploaded image is a 2-column labelled grid containing {n} separate reference objects. {guide}. Each cell is an independent visual reference for a different object. Extract each object's exact shape, proportions, materiality, and surface textures from its cell. Place each one into the scene as instructed, ensuring correct relative scale between them. Ground every object with physically accurate contact and ambient shadows that follow the room's primary light direction."
    return (
        "You are an expert architectural visualization AI and senior interior designer. "
        "Your task is to execute the user's request with strict adherence to photorealism.\n\n"
        "CORE CONSTRAINTS:\n"
        "- POSITION & PLACEMENT (Handling all scenarios):\n"
        "   * IF REPLACING of SIMILAR SIZE: The new item MUST strictly occupy the exact same spatial location and footprint.\n"
        "   * IF REPLACING with DIFFERENT SIZE: Anchor the new item to the original origin point, but scale it naturally.\n"
        "   * IF ADDING to OPEN SPACE: Place the item naturally as described, strictly adhering to the room's vanishing points.\n"
        "   * IF REMOVING: Flawlessly synthesize and inpaint the newly exposed background.\n"
        "- OCCLUSIONS & OVERLAPS: Carefully preserve any foreground objects that blocked the original object.\n"
        "- REFLECTIONS & SHADOWS: Ensure new objects cast physically accurate ground shadows.\n"
        "- ANCHOR & PRESERVE: Do NOT alter the room's overarching geometry or camera angle.\n"
        "- MATERIALITY: Render with tactile, high-fidelity materiality.\n"
        "- TEXTILES & SOFT FURNISHINGS: Ensure natural draping, folding, and realistic light transmission.\n"
        f"- {ref}\n"
        "- CLEANLINESS: Output a single realistic edited image with NO text, NO watermarks, and NO borders.\n\n"
        f"USER DIRECTIVE: {user_prompt.strip()}"
    )


@router.post("/edit")
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
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Prompt must be under {MAX_PROMPT_LENGTH} characters")

    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if room_image.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Room image must be JPEG, PNG, WebP, or GIF")

    try:
        tags = _json.loads(object_tags)
    except Exception:
        tags = []

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex

    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        room_path = UPLOADS_DIR / f"{room_id}_room.png"
        room_path.write_bytes(room_png)

        raw_objects = [object_image_1, object_image_2, object_image_3, object_image_4, object_image_5]
        pil_objects = []
        room_w, room_h = 0, 0
        for i, obj_upload in enumerate(raw_objects):
            if obj_upload and obj_upload.filename:
                obj_bytes = await obj_upload.read()
                obj_png = prepare_image(obj_bytes)
                (UPLOADS_DIR / f"{room_id}_obj{i + 1}.png").write_bytes(obj_png)
                pil_objects.append(Image.open(BytesIO(obj_png)).convert("RGBA"))

        room_dims = Image.open(BytesIO(room_png))
        room_w, room_h = room_dims.width, room_dims.height

        reference_png = None
        if len(pil_objects) == 1:
            buf = BytesIO()
            pil_objects[0].save(buf, format="PNG")
            reference_png = buf.getvalue()
        elif len(pil_objects) > 1:
            if USE_MODEL_LABS:
                buf = BytesIO()
                pil_objects[0].save(buf, format="PNG")
                reference_png = buf.getvalue()
            else:
                reference_png = build_composite(pil_objects, tags)
                (UPLOADS_DIR / f"{room_id}_composite.png").write_bytes(reference_png)

        if USE_MODEL_LABS:
            if not reference_png:
                raise HTTPException(
                    status_code=400,
                    detail="Upload at least one object/furniture image. ModelsLab Interior-Mixer requires it.",
                )
            result_b64 = generate_modelslab(room_png, reference_png, prompt, room_w, room_h)
        else:
            final_prompt = build_prompt(prompt, tags[:len(pil_objects)])
            result_b64 = generate_openai(room_png, reference_png, final_prompt, room_path.name)

    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        msg = str(e)[:200] if str(e) else "Generation failed. Please try again."
        raise HTTPException(status_code=500, detail=msg)
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()

    return JSONResponse({
        "image_b64": result_b64,
        "images_b64": [result_b64],
        "format": "png",
        "credits": credits,
    })


# ── Interior Make (Room Redesign) ──────────────────────────────────────────
@router.post("/make")
async def make_interior(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
    specific_object: str = Form(""),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_interior_make(room_png, prompt, negative_prompt, strength, specific_object)
        else:
            final_prompt = build_prompt(prompt, [])
            result_b64 = generate_openai(room_png, None, final_prompt, f"{room_id}_room.png")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Room Decorator ─────────────────────────────────────────────────────────
@router.post("/decorate")
async def decorate_room(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
    specific_object: str = Form(""),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_room_decorator(room_png, prompt, negative_prompt, strength, specific_object)
        else:
            final_prompt = build_prompt(prompt, [])
            result_b64 = generate_openai(room_png, None, final_prompt, f"{room_id}_room.png")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Floor Planning ─────────────────────────────────────────────────────────
@router.post("/floor-plan")
async def floor_plan(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_floor_plan(room_png, prompt, negative_prompt, strength)
        else:
            raise HTTPException(status_code=400, detail="Floor planning requires ModelsLab API")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Object Removal ─────────────────────────────────────────────────────────
@router.post("/remove-object")
async def remove_object(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    object_name: str = Form(...),
):
    object_name = object_name.strip()
    if not object_name:
        raise HTTPException(status_code=400, detail="Object name cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_object_removal(room_png, object_name)
        else:
            raise HTTPException(status_code=400, detail="Object removal requires ModelsLab API")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Scenario Changer ──────────────────────────────────────────────────────
@router.post("/scenario")
async def change_scenario(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    scenario: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Invalid scenario. Must be one of: {', '.join(SCENARIOS)}")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_scenario_change(room_png, prompt, scenario, negative_prompt, strength)
        else:
            raise HTTPException(status_code=400, detail="Scenario changer requires ModelsLab API")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Sketch Rendering ──────────────────────────────────────────────────────
@router.post("/sketch")
async def render_sketch(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_sketch_render(room_png, prompt, negative_prompt, strength)
        else:
            raise HTTPException(status_code=400, detail="Sketch rendering requires ModelsLab API")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})


# ── Exterior Restorer ─────────────────────────────────────────────────────
@router.post("/exterior")
async def restore_exterior(
    user: dict = Depends(get_current_user),
    room_image: UploadFile = File(...),
    prompt: str = Form(...),
    negative_prompt: str = Form("bad quality, blurry, distorted"),
    strength: float = Form(5.0),
):
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    credits = charge_credit(user["id"])
    if credits is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    room_id = uuid.uuid4().hex
    try:
        room_bytes = await room_image.read()
        room_png = prepare_image(room_bytes)
        (UPLOADS_DIR / f"{room_id}_room.png").write_bytes(room_png)

        if USE_MODEL_LABS:
            result_b64 = generate_exterior_restore(room_png, prompt, negative_prompt, strength)
        else:
            raise HTTPException(status_code=400, detail="Exterior restorer requires ModelsLab API")
    except HTTPException:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            refund_credit(user["id"])
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e)[:200] or "Generation failed")
    finally:
        for f in UPLOADS_DIR.glob(f"{room_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass

    schedule_backup()
    return JSONResponse({"image_b64": result_b64, "images_b64": [result_b64], "format": "png", "credits": credits})
