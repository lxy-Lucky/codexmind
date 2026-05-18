from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import repo, search, analysis
from app.core.config import settings
from app.db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== CodexMind Backend starting ===")

    # 初始化 SQLite 表结构
    await init_db()

    # 预热 Qdrant 连接（不强依赖，启动失败不阻断）
    try:
        from app.core.qdrant_client import get_qdrant
        client = get_qdrant()
        info = await client.get_collections()
        logger.info("Qdrant connected: %d collections", len(info.collections))
    except Exception as e:
        logger.warning("Qdrant not available at startup: %s", e)

    # 注意：bge-m3 不在启动时加载，首次请求时懒加载（避免启动慢）
    logger.info("Backend ready on %s:%d", settings.HOST, settings.PORT)

    yield

    logger.info("=== CodexMind Backend shutting down ===")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "CodexMind API",
    description = "代码库智能检索与分析系统",
    version     = "0.1.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS（开发环境放开，生产按需收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(repo.router)
app.include_router(search.router)
app.include_router(analysis.router)


# ── 健康检查 / 状态接口 ───────────────────────────────────────────────────────

@app.get("/api/status", tags=["system"])
async def system_status():
    """前端 StatusBar 实时拉取的状态接口"""
    qdrant_ok = False
    qdrant_collections = 0
    try:
        from app.core.qdrant_client import get_qdrant
        info = await get_qdrant().get_collections()
        qdrant_ok = True
        qdrant_collections = len(info.collections)
    except Exception:
        pass

    return {
        "status":             "ok",
        "qdrant":             {"online": qdrant_ok, "collections": qdrant_collections},
        "embedding_model":    settings.EMBEDDING_MODEL,
        "embedding_device":   settings.EMBEDDING_DEVICE,
        "llm_model":          settings.OLLAMA_MODEL,
        "vector_dim":         settings.vector_size,
        "context_window":     "128K tokens",
        "timestamp":          int(time.time()),
    }


@app.get("/", tags=["system"])
async def root():
    return {"message": "CodexMind API", "docs": "/docs"}


# ── 启动入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host   = settings.HOST,
        port   = settings.PORT,
        reload = settings.RELOAD,
    )
