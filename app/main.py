import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import seed
from app.api import v1
from app.api.routes import health
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.services.video import fail_stuck_jobs

logging.basicConfig(level=logging.INFO)
settings = get_settings()
settings.media_path.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed.seed()  # 비어 있을 때만
    fail_stuck_jobs()
    yield


app = FastAPI(
    title=settings.app_name,
    description="전통시장 AI 숏폼 서비스 API. 계약: 노션 '04. 백엔드·API'. 에러 형식: {\"error\": {\"code\", \"message\"}}",
    root_path=settings.root_path,
    lifespan=lifespan,
)
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(v1.router)
app.mount("/media", StaticFiles(directory=settings.media_path), name="media")
