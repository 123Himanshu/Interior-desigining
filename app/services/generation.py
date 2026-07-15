import base64
import time
from io import BytesIO
from PIL import Image
from app.config import MODEL_LABS_KEY, MAX_DIMENSION, MAX_SIZE_BYTES

ML_API_BASE = "https://modelslab.com/api/v6"
ML_IMG2IMG = f"{ML_API_BASE}/images/img2img"
ML_FETCH = f"{ML_API_BASE}/images/fetch"


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
        new_w = max(16, int(img.width * scale))
        new_h = max(16, int(img.height * scale))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="PNG")
        data = buf.getvalue()
    return data


def clamp_dims(width: int, height: int) -> tuple[int, int]:
    w = max(512, min(1024, width - (width % 16)))
    h = max(512, min(1024, height - (height % 16)))
    return w, h


def _to_b64_image(content: bytes) -> str:
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


def _upload_to_modelslab(data: bytes) -> str:
    import requests as req
    resp = req.post(
        f"{ML_API_BASE}/base64_to_url",
        json={
            "key": MODEL_LABS_KEY,
            "base64_string": f"data:image/png;base64,{_b64(data)}",
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ModelsLab image upload HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    if (payload.get("status") or "").lower() != "success":
        raise RuntimeError(f"ModelsLab image upload failed: {payload.get('message', payload)}")
    urls = payload.get("output") or []
    if not urls or not isinstance(urls[0], str):
        raise RuntimeError("ModelsLab image upload returned no URL")
    return urls[0]


def _poll_result(job_id: str) -> str:
    import requests as req
    total_start = time.time()
    for attempt in range(30):
        if time.time() - total_start > 180:
            break
        time.sleep(6)
        try:
            fr = req.post(
                ML_FETCH,
                json={"key": MODEL_LABS_KEY, "request_id": job_id},
                timeout=30,
            )
            if fr.status_code != 200:
                continue
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
    raise RuntimeError("ModelsLab: timed out waiting for image (try again)")


def generate_flux_klein(
    room_png: bytes,
    object_pngs: list[bytes],
    prompt: str,
    width: int = 0,
    height: int = 0,
    strength: float = 0.7,
) -> str:
    if not MODEL_LABS_KEY:
        raise RuntimeError("MODEL_LABS_KEY not configured")

    room_url = _upload_to_modelslab(room_png)
    init_images = [room_url]
    for obj in object_pngs:
        init_images.append(_upload_to_modelslab(obj))

    if width < 512 or height < 512:
        img = Image.open(BytesIO(room_png))
        width, height = img.width, img.height
    width, height = clamp_dims(width, height)

    import requests as req
    payload = {
        "key": MODEL_LABS_KEY,
        "model_id": "flux-klein",
        "init_image": init_images,
        "prompt": prompt or "Place the furniture naturally into the room with realistic lighting and shadows",
        "samples": 1,
        "strength": max(0.7, min(1.0, strength)),
        "enhance_prompt": False,
        "width": width,
        "height": height,
    }

    resp = req.post(
        ML_IMG2IMG,
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

    if future_links:
        try:
            ir = req.get(future_links[0], timeout=30)
            if ir.status_code == 200 and len(ir.content) > 500:
                return _to_b64_image(ir.content)
        except Exception:
            pass

    if fetch_url:
        fetch_deadline = time.time() + 180
        for attempt in range(30):
            if time.time() > fetch_deadline:
                break
            time.sleep(6)
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

    if job_id:
        return _poll_result(str(job_id))

    raise RuntimeError("ModelsLab: timed out waiting for image (try again)")
