from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db, health_check
from app.routes.auth import router as auth_router
from app.routes.admin import router as admin_router
from app.routes.library import router as library_router
from app.routes.edit import router as edit_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception as e:
        print(f"[WARN] DB init failed, running degraded: {e}", flush=True)
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
    from app.config import HF_TOKEN, HF_DATASET_REPO, MODEL_LABS_KEY
    info = health_check()
    return {
        "status": "ok" if info.get("db_ok") else "degraded",
        "hf_sync": bool(HF_TOKEN and HF_DATASET_REPO),
        "modelslab": bool(MODEL_LABS_KEY),
        **info,
    }


app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
