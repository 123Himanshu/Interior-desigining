from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.routes.auth import get_current_user
from app.services.generations import list_generations, get_generation, delete_generation

router = APIRouter()


@router.get("/generations")
async def generations(
    user: dict = Depends(get_current_user),
    limit: int = Query(24, ge=1, le=50),
    mode: str | None = Query(None, max_length=40),
    cursor: str | None = Query(None, max_length=80),
):
    items, next_cursor = list_generations(user["id"], limit, mode, cursor)
    return JSONResponse({"items": items, "next_cursor": next_cursor})


@router.get("/generations/{generation_id}")
async def generation(generation_id: str, user: dict = Depends(get_current_user)):
    item = get_generation(user["id"], generation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Generation not found")
    return JSONResponse(item)


@router.delete("/generations/{generation_id}")
async def remove_generation(generation_id: str, user: dict = Depends(get_current_user)):
    if not delete_generation(user["id"], generation_id):
        raise HTTPException(status_code=404, detail="Generation not found")
    return JSONResponse({"deleted": True})
