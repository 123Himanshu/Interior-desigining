from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.database import init_db
from app.config import DATABASE_URL
from app.services.backup import restore_from_hf
from app.routes.auth import router as auth_router
from app.routes.admin import router as admin_router
from app.routes.library import router as library_router
from app.routes.edit import router as edit_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not DATABASE_URL:
        restore_from_hf()
    yield


app = FastAPI(title="Room AI - Interior Visualizer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(library_router)
app.include_router(edit_router)


@app.get("/health")
async def health():
    from app.config import HF_TOKEN, HF_DATASET_REPO, DATABASE_URL
    from app.database import _USE_PG
    result = {
        "status": "ok",
        "db": "postgresql" if _USE_PG else "sqlite",
        "hf_sync": bool(HF_TOKEN and HF_DATASET_REPO),
    }
    try:
        from app.database import get_db
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        result["db_ok"] = True
    except Exception:
        result["db_ok"] = False
    return result


app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
