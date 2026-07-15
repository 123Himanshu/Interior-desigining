import uuid
import json as _json
import traceback
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.routes.auth import get_current_user
from app.services.users import charge_credit, refund_credit
from app.services.generation import prepare_image, generate_flux_klein
from app.services.backup import schedule_backup
from app.services.generations import save_generation

router = APIRouter()

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

MAX_PROMPT_LENGTH = 4000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _generation_response(user_id: int, mode: str, prompt: str, tags: list, image_b64: str, credits: int) -> JSONResponse:
    try:
        saved = save_generation(user_id, mode, prompt, tags, image_b64)
    except Exception:
        try:
            refund_credit(user_id)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Could not save generated image. Credit refunded. Try again.")
    return JSONResponse({
        "generation_id": saved["id"],
        "output_url": saved["output_url"],
        "format": saved["format"],
        "credits": credits,
        "history_saved": True,
    })


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

    if room_image.content_type not in ALLOWED_IMAGE_TYPES:
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
        object_pngs = []
        for i, obj_upload in enumerate(raw_objects):
            if obj_upload and obj_upload.filename:
                if obj_upload.content_type not in ALLOWED_IMAGE_TYPES:
                    raise HTTPException(status_code=400, detail=f"Object image {i + 1} must be JPEG, PNG, WebP, or GIF")
                obj_bytes = await obj_upload.read()
                obj_png = prepare_image(obj_bytes)
                (UPLOADS_DIR / f"{room_id}_obj{i + 1}.png").write_bytes(obj_png)
                object_pngs.append(obj_png)

        if not object_pngs:
            raise HTTPException(
                status_code=400,
                detail="Upload at least one furniture image to replace or add to the room.",
            )

        room_dims = Image.open(BytesIO(room_png))
        room_w, room_h = room_dims.width, room_dims.height

        result_b64 = generate_flux_klein(room_png, object_pngs, prompt, room_w, room_h)

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

    return _generation_response(user["id"], "edit", prompt, tags[:len(object_pngs)], result_b64, credits)
