import secrets
from fastapi import APIRouter, HTTPException, Request, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from app.routes.auth import get_current_user
from app.services.library import (
    seed_library, get_user_library, save_user_library,
    add_library_item, delete_library_item,
    add_library_image,
)
from app.services.backup import schedule_backup
from app.services.storage import is_configured

router = APIRouter()


@router.get("/library")
async def get_library(user: dict = Depends(get_current_user)):
    seed_library(user["id"], user["username"])
    assets = get_user_library(user["id"])
    return JSONResponse({"enabled": True, "storage_enabled": is_configured(), "assets": assets})


@router.post("/library")
async def save_library(request: Request, user: dict = Depends(get_current_user)):
    if is_configured():
        raise HTTPException(status_code=410, detail="Use /library/upload for Storage-backed library images")
    body = await request.json()
    assets = body.get("assets")
    if not isinstance(assets, list):
        raise HTTPException(status_code=400, detail="assets must be an array")
    save_user_library(user["id"], assets)
    schedule_backup()
    return JSONResponse({"enabled": True, "saved": True, "count": len(assets)})


@router.post("/library/add")
async def add_item(request: Request, user: dict = Depends(get_current_user)):
    if is_configured():
        raise HTTPException(status_code=410, detail="Use /library/upload for Storage-backed library images")
    body = await request.json()
    name = (body.get("name") or "").strip()
    data_url = (body.get("dataUrl") or body.get("image_b64") or "").strip()
    if not name or not data_url:
        raise HTTPException(status_code=400, detail="name and image required")
    item_id = str(body.get("id") or secrets.token_hex(8))
    add_library_item(user["id"], item_id, name,
                     body.get("roomTag", body.get("room_tag", "")),
                     body.get("objectTag", body.get("obj_tag", "")),
                     data_url)
    schedule_backup()
    return JSONResponse({"id": item_id, "name": name})


@router.post("/library/upload")
async def upload_item(
    user: dict = Depends(get_current_user),
    image: UploadFile = File(...),
    name: str = Form(...),
    room_tag: str = Form("Untagged"),
    object_tag: str = Form("Item"),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        data = await image.read()
        item_id = secrets.token_hex(12)
        asset = add_library_image(user["id"], item_id, name, room_tag.strip(), object_tag.strip(), data, image.content_type)
        schedule_backup()
        return JSONResponse(asset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not save library image: {str(exc)[:160]}") from exc


@router.delete("/library/{item_id}")
async def delete_item(item_id: str, user: dict = Depends(get_current_user)):
    if not delete_library_item(user["id"], item_id):
        raise HTTPException(status_code=404, detail="Library item not found")
    schedule_backup()
    return JSONResponse({"deleted": True})
