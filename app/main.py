"""FastAPI entry point for 知源 (ZhiYuan) RAG knowledge base."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import chat, knowledge, settings, system
from .config import AppConfig, SettingsStore
from .db import Database
from .ingest import seed_demo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("zhiyuan")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AppConfig.from_env()
    config.ensure()
    db = Database(config.db_path)
    db.init()
    store = SettingsStore(config)
    app.state.config = config
    app.state.db = db
    app.state.store = store
    if bool(store.get().get("seed_demo", True)):
        try:
            created = seed_demo(db, config, store)
            if created:
                logger.info("Demo knowledge base is ready")
        except Exception:
            logger.exception("Failed to seed the demo knowledge base")
    logger.info("知源 running with data dir %s", config.data_dir)
    yield


app = FastAPI(
    title="知源 ZhiYuan · RAG 知识库问答",
    description="基于检索增强生成的企业知识库问答系统",
    version=system.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(knowledge.router)
app.include_router(chat.router)
app.include_router(settings.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    target = STATIC_DIR / "index.html"
    if not target.exists():
        return JSONResponse(
            status_code=500,
            content={"detail": "前端文件缺失，请确认 static/index.html 存在"},
        )
    return FileResponse(target, media_type="text/html; charset=utf-8")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon = STATIC_DIR / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"detail": "not found"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误：{exc}"},
    )
