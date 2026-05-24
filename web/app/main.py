import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import settings

app = FastAPI(title="OCR Web App", version="0.1.0")
app.include_router(api_router)

# Ensure data directories exist
os.makedirs(os.path.join(settings.data_dir, "uploads"), exist_ok=True)
os.makedirs(os.path.join(settings.data_dir, "results"), exist_ok=True)

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    # no-cache so Cloudflare/browsers always revalidate the HTML (chart + model list
    # change on deploy). Static assets keep their ?v= long-cache for performance.
    return FileResponse(
        os.path.join(static_dir, "index.html"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
