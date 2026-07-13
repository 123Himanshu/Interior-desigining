from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from app.services.auth import get_user_by_token, login_user

router = APIRouter()


async def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


@router.post("/login")
async def login(body: dict):
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    token, uname, credits = login_user(username, password)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return JSONResponse({"token": token, "username": uname, "credits": credits})


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return JSONResponse({"username": user["username"], "credits": user["credits"]})
