import base64
import time
import json as _json
from io import BytesIO
from PIL import Image
from app.config import (
    OPENAI_API_KEY, MODEL_LABS_KEY, USE_MODEL_LABS,
    MAX_DIMENSION, MAX_SIZE_BYTES,
)


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
        new_w = max(8, int(img.width * scale))
        new_h = max(8, int(img.height * scale))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="PNG")
        data = buf.getvalue()
    return data


def clamp_dims(width: int, height: int) -> tuple[int, int]:
    w = max(512, min(2048, width - (width % 8)))
    h = max(512, min(2048, height - (height % 8)))
    return w, h


def _to_b64_image(content: bytes) -> str:
    """Normalize ModelsLab response bytes into a base64 image string."""
    if not content or len(content) < 100:
        raise RuntimeError("ModelsLab: empty image response")

    # HTML 404 page
    if content[:15].lstrip().lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        raise RuntimeError("ModelsLab: image not ready yet")

    # Already a base64 text payload (JPEG starts with /9j/, PNG with iVBOR)
    try:
        text = content.decode("utf-8").strip()
        if text.startswith("data:image"):
            text = text.split(",", 1)[1]
        raw = base64.b64decode(text, validate=False)
        Image.open(BytesIO(raw)).verify()
        return text
    except Exception:
        pass

    # Raw binary image
    try:
        Image.open(BytesIO(content)).verify()
        return base64.b64encode(content).decode("ascii")
    except Exception as e:
        raise RuntimeError(f"ModelsLab: invalid image data ({e})")


def generate_openai(room_png: bytes, reference_png: bytes | None, prompt: str, room_fname: str) -> str:
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-placeholder"):
        raise RuntimeError("OPENAI_API_KEY not configured")
    import openai
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    if reference_png:
        response = client.images.edit(
            model="gpt-image-1.5",
            image=[(room_fname, room_png, "image/png"), ("reference.png", reference_png, "image/png")],
            prompt=prompt, n=1, size="1024x1024",
        )
    else:
        response = client.images.edit(
            model="gpt-image-1.5",
            image=(room_fname, room_png, "image/png"),
            prompt=prompt, n=1, size="1024x1024",
        )
    for img_data in response.data:
        if hasattr(img_data, "b64_json") and img_data.b64_json:
            return img_data.b64_json
        if hasattr(img_data, "url") and img_data.url:
            import requests as req
            r = req.get(img_data.url, timeout=30)
            return base64.b64encode(r.content).decode()
    raise RuntimeError("No image data returned from OpenAI")


def generate_modelslab(room_png: bytes, object_png: bytes | None, prompt: str, width: int = 0, height: int = 0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")
    if not object_png:
        raise RuntimeError("ModelsLab Interior-Mixer requires an object image. Upload at least one furniture/object reference.")

    room_b64 = base64.b64encode(room_png).decode("ascii")
    obj_b64 = base64.b64encode(object_png).decode("ascii")

    if width < 512 or height < 512:
        img = Image.open(BytesIO(room_png))
        width, height = img.width, img.height
    width, height = clamp_dims(width, height)

    import requests as req
    payload = {
        "key": MODEL_LABS_KEY,
        "init_image": room_b64,
        "object_image": obj_b64,
        "prompt": prompt or "Place the object naturally into the room with realistic lighting and shadows",
        "width": width,
        "height": height,
        "base64": True,
        "num_inference_steps": 51,
        "guidance_scale": 8,
    }

    resp = req.post(
        "https://modelslab.com/api/v6/interior/interior_mixer",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ModelsLab HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    status = (data.get("status") or "").lower()
    if status == "error":
        raise RuntimeError(f"ModelsLab: {data.get('message', 'unknown error')}")

    # Immediate success with output
    outputs = data.get("output") or []
    if status == "success" and outputs:
        return _extract_output(outputs[0], req)

    future_links = data.get("future_links") or []
    fetch_url = data.get("fetch_result")
    job_id = data.get("id")

    # Poll for up to 3 minutes
    total_start = time.time()
    for attempt in range(30):
        if time.time() - total_start > 180:
            break
        time.sleep(8)

        # 1) Prefer future_links when ready
        if future_links:
            try:
                ir = req.get(future_links[0], timeout=30)
                if ir.status_code == 200 and len(ir.content) > 500:
                    return _to_b64_image(ir.content)
            except Exception:
                pass

        # 2) Poll fetch_result endpoint
        if fetch_url:
            try:
                fr = req.post(fetch_url, json={"key": MODEL_LABS_KEY}, timeout=30)
                if fr.status_code == 200:
                    fd = fr.json()
                    fstatus = (fd.get("status") or "").lower()
                    if fstatus == "success":
                        outs = fd.get("output") or fd.get("future_links") or []
                        if outs:
                            return _extract_output(outs[0], req)
                    if fstatus == "error":
                        raise RuntimeError(f"ModelsLab: {fd.get('message', 'generation failed')}")
            except RuntimeError:
                raise
            except Exception:
                pass

        # 3) Fallback fetch by id
        if job_id and not fetch_url:
            try:
                fr = req.post(
                    f"https://modelslab.com/api/v6/interior/fetch/{job_id}",
                    json={"key": MODEL_LABS_KEY},
                    timeout=30,
                )
                if fr.status_code == 200:
                    fd = fr.json()
                    if (fd.get("status") or "").lower() == "success":
                        outs = fd.get("output") or []
                        if outs:
                            return _extract_output(outs[0], req)
            except Exception:
                pass

    raise RuntimeError("ModelsLab: timed out waiting for image (try again)")


def _extract_output(item, req) -> str:
    """item may be a URL or a base64 string."""
    if not isinstance(item, str):
        raise RuntimeError("ModelsLab: unexpected output format")
    if item.startswith("http"):
        r = req.get(item, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ModelsLab: failed to download result ({r.status_code})")
        return _to_b64_image(r.content)
    return _to_b64_image(item.encode("utf-8"))
