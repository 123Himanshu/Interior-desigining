import base64
import time
import json as _json
from io import BytesIO
from PIL import Image
from app.config import (
    OPENAI_API_KEY, MODEL_LABS_KEY, USE_MODEL_LABS,
    MAX_DIMENSION, MAX_SIZE_BYTES,
)


_client = None
if not USE_MODEL_LABS and OPENAI_API_KEY:
    import openai
    _client = openai.OpenAI(api_key=OPENAI_API_KEY)


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
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="PNG")
        data = buf.getvalue()
    return data


def clamp_dims(width: int, height: int) -> tuple[int, int]:
    return max(512, min(2048, width - (width % 8))), max(512, min(2048, height - (height % 8)))


def generate_openai(room_png: bytes, reference_png: bytes | None, prompt: str, room_fname: str) -> str:
    if not OPENAI_API_KEY:
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
    room_b64 = base64.b64encode(room_png).decode()
    if width < 512:
        width = Image.open(BytesIO(room_png)).width
    if height < 512:
        height = Image.open(BytesIO(room_png)).height
    width, height = clamp_dims(width, height)

    import requests as req
    payload = {
        "key": MODEL_LABS_KEY,
        "model_id": "Interior-Mixer",
        "init_image": room_b64,
        "prompt": prompt,
        "width": str(width),
        "height": str(height),
        "base64": True,
    }
    if object_png:
        payload["object_image"] = base64.b64encode(object_png).decode()

    resp = req.post(
        "https://modelslab.com/api/v6/interior/interior_mixer",
        headers={"Content-Type": "application/json"},
        json=payload, timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ModelsLab {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    if data.get("status") == "error":
        raise RuntimeError(f"ModelsLab: {data.get('message', 'unknown')}")

    future_links = data.get("future_links", [])
    if not future_links:
        raise RuntimeError(f"ModelsLab: no future_links. Response: {_json.dumps(data)[:200]}")

    image_url = future_links[0]
    for _ in range(30):
        time.sleep(8)
        ir = req.get(image_url, timeout=30)
        if ir.status_code == 200 and len(ir.content) > 100:
            return ir.content.decode("utf-8")
    raise RuntimeError("ModelsLab: timed out waiting for image")
