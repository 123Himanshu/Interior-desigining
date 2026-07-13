from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.services.backup import restore_from_hf
from app.routes.auth import router as auth_router
from app.routes.admin import router as admin_router
from app.routes.library import router as library_router
from app.routes.edit import router as edit_router

init_db()
restore_from_hf()

app = FastAPI(title="Room AI - Interior Visualizer")

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
    from app.config import HF_TOKEN, HF_DATASET_REPO
    return {"status": "ok", "hf_sync": bool(HF_TOKEN and HF_DATASET_REPO)}


app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/", StaticFiles(directory="static", html=True), name="root")
