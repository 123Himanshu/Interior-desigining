"""Private Supabase Storage adapter used by Library and generation History."""
import hashlib
import time
from pathlib import PurePosixPath

import requests

from app.config import (
    MAX_SIZE_BYTES,
    STORAGE_SIGNED_URL_TTL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STORAGE_BUCKET,
    SUPABASE_URL,
)


ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class StorageError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_STORAGE_BUCKET)


def _headers(content_type: str | None = None) -> dict:
    if not is_configured():
        raise StorageError("Supabase Storage is not configured")
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _url(path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/{path.lstrip('/')}"


def ensure_bucket() -> None:
    """Check the private bucket and create it once when missing."""
    response = requests.get(_url("bucket"), headers=_headers(), timeout=20)
    if response.status_code != 200:
        raise StorageError(f"Storage bucket check failed ({response.status_code})")
    buckets = response.json()
    bucket = next((b for b in buckets if b.get("id") == SUPABASE_STORAGE_BUCKET), None)
    if bucket is None:
        response = requests.post(
            _url("bucket"),
            headers={**_headers("application/json"), "Prefer": "return=representation"},
            json={
                "id": SUPABASE_STORAGE_BUCKET,
                "name": SUPABASE_STORAGE_BUCKET,
                "public": False,
                "file_size_limit": MAX_SIZE_BYTES,
                "allowed_mime_types": list(ALLOWED_IMAGE_TYPES),
            },
            timeout=20,
        )
        if response.status_code not in (200, 201):
            raise StorageError(f"Storage bucket creation failed ({response.status_code})")
        return
    if bucket.get("public") is True:
        raise StorageError("Configured Storage bucket must be private")


def health_check() -> dict:
    if not is_configured():
        return {
            "storage_configured": False,
            "storage_ok": False,
            "storage_bucket": SUPABASE_STORAGE_BUCKET,
            "storage_error": "Supabase Storage credentials are not configured",
        }
    try:
        ensure_bucket()
        return {
            "storage_configured": True,
            "storage_ok": True,
            "storage_bucket": SUPABASE_STORAGE_BUCKET,
        }
    except Exception as exc:
        return {
            "storage_configured": True,
            "storage_ok": False,
            "storage_bucket": SUPABASE_STORAGE_BUCKET,
            "storage_error": str(exc)[:200],
        }


def validate_image_bytes(data: bytes, content_type: str | None = None) -> tuple[str, str, int, str]:
    if not data or len(data) > MAX_SIZE_BYTES:
        raise StorageError("Image is empty or exceeds the maximum size")
    from PIL import Image
    from io import BytesIO

    try:
        image = Image.open(BytesIO(data))
        image.verify()
        detected = Image.open(BytesIO(data)).get_format_mimetype()
    except Exception as exc:
        raise StorageError(f"Invalid image data: {exc}") from exc
    mime = detected if detected in ALLOWED_IMAGE_TYPES else content_type
    if mime not in ALLOWED_IMAGE_TYPES:
        raise StorageError("Unsupported image type")
    extension = ALLOWED_IMAGE_TYPES[mime]
    return mime, extension, len(data), hashlib.sha256(data).hexdigest()


def upload_image(path: str, data: bytes, content_type: str | None = None) -> dict:
    mime, extension, size, sha256 = validate_image_bytes(data, content_type)
    safe_path = str(PurePosixPath(path))
    for attempt in range(3):
        try:
            response = requests.post(
                _url(f"object/{SUPABASE_STORAGE_BUCKET}/{safe_path}"),
                headers={**_headers(mime), "x-upsert": "false"},
                data=data,
                timeout=60,
            )
            if response.status_code in (200, 201):
                return {
                    "path": safe_path,
                    "mime": mime,
                    "extension": extension,
                    "size": size,
                    "sha256": sha256,
                }
            if response.status_code == 409:
                raise StorageError("Storage path already exists")
            raise StorageError(f"Storage upload failed ({response.status_code})")
        except (requests.RequestException, StorageError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise StorageError("Storage upload failed")


def signed_url(path: str, expires: int = STORAGE_SIGNED_URL_TTL) -> str:
    response = requests.post(
        _url(f"object/sign/{SUPABASE_STORAGE_BUCKET}/{str(PurePosixPath(path))}"),
        headers={**_headers("application/json")},
        json={"expiresIn": max(60, expires)},
        timeout=20,
    )
    if response.status_code not in (200, 201):
        raise StorageError(f"Signed URL creation failed ({response.status_code})")
    signed = response.json().get("signedURL") or response.json().get("signedUrl")
    if not signed:
        raise StorageError("Storage returned no signed URL")
    if signed.startswith("http"):
        return signed
    if signed.startswith("/storage/v1/"):
        return f"{SUPABASE_URL}{signed}"
    return f"{SUPABASE_URL}/storage/v1{signed if signed.startswith('/') else '/' + signed}"


def delete_image(path: str) -> None:
    response = requests.delete(
        _url(f"object/{SUPABASE_STORAGE_BUCKET}/{str(PurePosixPath(path))}"),
        headers=_headers(),
        timeout=30,
    )
    if response.status_code not in (200, 204):
        raise StorageError(f"Storage delete failed ({response.status_code})")
