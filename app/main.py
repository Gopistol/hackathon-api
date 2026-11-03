from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.routes import chat, files, health, items
from app.core.config import get_settings
from app.db.session import init_db

settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, root_path=settings.root_path, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(items.router)
app.include_router(files.router)
app.include_router(chat.router)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """프론트 준비 전 API 확인·백업 시연용 페이지."""
    return FileResponse(STATIC_DIR / "index.html")
