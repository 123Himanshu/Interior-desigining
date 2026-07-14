import base64
import time
import json as _json
from io import BytesIO
from PIL import Image
from app.config import (
    OPENAI_API_KEY, MODEL_LABS_KEY, USE_MODEL_LABS,
    MAX_DIMENSION, MAX_SIZE_BYTES,
)

ML_BASE = "https://modelslab.com/api/v6/interior"


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

    if content[:15].lstrip().lower().startswith(b"<!doctype") or content[:6].lower().startswith(b"<html"):
        raise RuntimeError("ModelsLab: image not ready yet")

    try:
        text = content.decode("utf-8").strip()
        if text.startswith("data:image"):
            text = text.split(",", 1)[1]
        raw = base64.b64decode(text, validate=False)
        Image.open(BytesIO(raw)).verify()
        return text
    except Exception:
        pass

    try:
        Image.open(BytesIO(content)).verify()
        return base64.b64encode(content).decode("ascii")
    except Exception as e:
        raise RuntimeError(f"ModelsLab: invalid image data ({e})")


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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _post_and_poll(endpoint: str, payload: dict) -> str:
    """Shared: POST to ModelsLab endpoint, poll until done, return base64 image."""
    import requests as req

    resp = req.post(
        f"{ML_BASE}/{endpoint}",
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

    outputs = data.get("output") or []
    if status == "success" and outputs:
        return _extract_output(outputs[0], req)

    future_links = data.get("future_links") or []
    fetch_url = data.get("fetch_result")
    job_id = data.get("id")

    total_start = time.time()
    for attempt in range(30):
        if time.time() - total_start > 180:
            break
        time.sleep(8)

        if future_links:
            try:
                ir = req.get(future_links[0], timeout=30)
                if ir.status_code == 200 and len(ir.content) > 500:
                    return _to_b64_image(ir.content)
            except Exception:
                pass

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

        if job_id and not fetch_url:
            try:
                fr = req.post(f"{ML_BASE}/fetch/{job_id}", json={"key": MODEL_LABS_KEY}, timeout=30)
                if fr.status_code == 200:
                    fd = fr.json()
                    if (fd.get("status") or "").lower() == "success":
                        outs = fd.get("output") or []
                        if outs:
                            return _extract_output(outs[0], req)
            except Exception:
                pass

    raise RuntimeError("ModelsLab: timed out waiting for image (try again)")


# ── Endpoint 1: Interior Mixer ──────────────────────────────────────────────
def generate_modelslab(room_png: bytes, object_png: bytes | None, prompt: str, width: int = 0, height: int = 0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")
    if not object_png:
        raise RuntimeError("Interior-Mixer requires an object image.")

    room_b64 = _b64(room_png)
    obj_b64 = _b64(object_png)

    if width < 512 or height < 512:
        img = Image.open(BytesIO(room_png))
        width, height = img.width, img.height
    width, height = clamp_dims(width, height)

    return _post_and_poll("interior_mixer", {
        "key": MODEL_LABS_KEY,
        "init_image": room_b64,
        "object_image": obj_b64,
        "prompt": prompt or "Place the object naturally into the room with realistic lighting and shadows",
        "width": width,
        "height": height,
        "base64": True,
        "num_inference_steps": 51,
        "guidance_scale": 8,
    })


# ── Endpoint 2: Interior Make (Room Redesign) ──────────────────────────────
def generate_interior_make(room_png: bytes, prompt: str, negative_prompt: str = "",
                           strength: float = 5.0, specific_object: str = "") -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    img = Image.open(BytesIO(room_png))
    w, h = clamp_dims(img.width, img.height)

    payload = {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(room_png),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    }
    if specific_object:
        payload["specific_object"] = specific_object

    return _post_and_poll("make", payload)


# ── Endpoint 3: Room Decorator ─────────────────────────────────────────────
def generate_room_decorator(room_png: bytes, prompt: str, negative_prompt: str = "",
                            strength: float = 5.0, specific_object: str = "") -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    img = Image.open(BytesIO(room_png))
    w, h = clamp_dims(img.width, img.height)

    payload = {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(room_png),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    }
    if specific_object:
        payload["specific_object"] = specific_object

    return _post_and_poll("room_decorator", payload)


# ── Endpoint 4: Floor Planning ─────────────────────────────────────────────
def generate_floor_plan(room_png: bytes, prompt: str, negative_prompt: str = "",
                        strength: float = 5.0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    img = Image.open(BytesIO(room_png))
    w, h = clamp_dims(img.width, img.height)

    return _post_and_poll("floor_planning", {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(room_png),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    })


# ── Endpoint 5: Object Removal ─────────────────────────────────────────────
def generate_object_removal(room_png: bytes, object_name: str) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    return _post_and_poll("object_removal", {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(room_png),
        "object_name": object_name,
        "base64": True,
    })


# ── Endpoint 6: Scenario Changer ──────────────────────────────────────────
SCENARIOS = ["beach", "desert", "plain", "taiga", "mountain", "snow", "jungle", "city", "underwater", "urban", "forest"]

def generate_scenario_change(room_png: bytes, prompt: str, scenario: str, negative_prompt: str = "",
                             strength: float = 5.0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")
    if scenario not in SCENARIOS:
        raise RuntimeError(f"Invalid scenario '{scenario}'. Must be one of: {', '.join(SCENARIOS)}")

    img = Image.open(BytesIO(room_png))
    w, h = clamp_dims(img.width, img.height)

    return _post_and_poll("scenario_changer", {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(room_png),
        "prompt": prompt,
        "scenario": scenario,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    })


# ── Endpoint 7: Sketch Rendering ──────────────────────────────────────────
def generate_sketch_render(sketch_png: bytes, prompt: str, negative_prompt: str = "",
                           strength: float = 5.0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    img = Image.open(BytesIO(sketch_png))
    w, h = clamp_dims(img.width, img.height)

    return _post_and_poll("sketch_rendering", {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(sketch_png),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    })


# ── Endpoint 8: Exterior Restorer ─────────────────────────────────────────
def generate_exterior_restore(exterior_png: bytes, prompt: str, negative_prompt: str = "",
                              strength: float = 5.0) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    img = Image.open(BytesIO(exterior_png))
    w, h = clamp_dims(img.width, img.height)

    return _post_and_poll("exterior_restorer", {
        "key": MODEL_LABS_KEY,
        "init_image": _b64(exterior_png),
        "prompt": prompt,
        "negative_prompt": negative_prompt or "bad quality, blurry, distorted",
        "strength": strength,
        "guidance_scale": 8,
        "num_inference_steps": 51,
        "base64": True,
        "width": w,
        "height": h,
    })


# ── OpenAI fallback ────────────────────────────────────────────────────────
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
