from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import repo, search, analysis
from app.api.graph import router as graph_router
from app.core.config import settings
from app.db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== CodexMind Backend v2 starting ===")

    # 确保数据目录存在（移出 config 模块，避免 import 时副作用）
    settings.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.BM25_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    await init_db()

    # Qdrant
    try:
        from app.core.qdrant_client import get_qdrant
        info = await get_qdrant().get_collections()
        logger.info("Qdrant connected: %d collections", len(info.collections))
    except Exception as e:
        logger.warning("Qdrant not available at startup: %s", e)

    # Neo4j
    try:
        from app.core.neo4j_client import init_neo4j
        await init_neo4j()
        logger.info("Neo4j schema initialized")
    except Exception as e:
        logger.warning("Neo4j not available at startup: %s", e)

    logger.info("Backend ready on %s:%d", settings.HOST, settings.PORT)
    yield

    # Shutdown
    try:
        from app.core.neo4j_client import close_neo4j
        await close_neo4j()
    except Exception:
        pass
    logger.info("=== CodexMind Backend shutting down ===")


app = FastAPI(
    title       = "CodexMind API",
    description = "代码库智能检索与分析系统 v2",
    version     = "2.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

app.include_router(repo.router)
app.include_router(search.router)
app.include_router(analysis.router)
app.include_router(graph_router)


@app.get("/api/status", tags=["system"])
async def system_status():
    qdrant_ok, qdrant_cols = False, 0
    try:
        from app.core.qdrant_client import get_qdrant
        info = await get_qdrant().get_collections()
        qdrant_ok  = True
        qdrant_cols = len(info.collections)
    except Exception:
        pass

    neo4j_ok = False
    try:
        from app.core.neo4j_client import run_query
        await run_query("RETURN 1")
        neo4j_ok = True
    except Exception:
        pass

    return {
        "status":           "ok",
        "version":          "2.0.0",
        "qdrant":           {"online": qdrant_ok, "collections": qdrant_cols},
        "neo4j":            {"online": neo4j_ok},
        "embedding_model":  settings.EMBEDDING_MODEL,
        "embedding_device": settings.EMBEDDING_DEVICE,
        "llm_model":        settings.OLLAMA_MODEL,
        "vector_dim":       settings.vector_size,
        "timestamp":        int(time.time()),
    }


@app.get("/", tags=["system"])
async def root():
    return {"message": "CodexMind API v2", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host   = settings.HOST,
        port   = settings.PORT,
        reload = settings.RELOAD,
    )
