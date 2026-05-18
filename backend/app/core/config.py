from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ──────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = True

    # ── Qdrant ──────────────────────────────────────
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333

    # ── Embedding ───────────────────────────────────
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cuda"
    EMBEDDING_BATCH_SIZE: int = 8
    EMBEDDING_MAX_LENGTH: int = 512

    # ── Ollama ──────────────────────────────────────
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:14b"
    OLLAMA_TIMEOUT: int = 120

    # ── Indexer ─────────────────────────────────────
    CHUNK_MAX_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64
    SUPPORTED_EXTENSIONS: str = ".java,.ts,.js"

    # ── SQLite ──────────────────────────────────────
    SQLITE_PATH: Path = Path("./data/history.db")

    # ── Security ────────────────────────────────────
    ALLOWED_REPO_ROOTS: str = ""  # 空 = 不限制

    # ── Computed ─────────────────────────────────────
    @property
    def supported_ext_set(self) -> set[str]:
        return {e.strip() for e in self.SUPPORTED_EXTENSIONS.split(",") if e.strip()}

    @property
    def allowed_roots(self) -> list[Path]:
        raw = self.ALLOWED_REPO_ROOTS
        if not raw:
            return []
        return [Path(p.strip()) for p in raw.split(",") if p.strip()]

    def is_path_allowed(self, path: Path) -> bool:
        """验证路径是否在白名单内（白名单为空时放行）"""
        if not self.allowed_roots:
            return True
        resolved = path.resolve()
        return any(
            str(resolved).startswith(str(root.resolve()))
            for root in self.allowed_roots
        )

    @property
    def vector_size(self) -> int:
        """bge-m3 dense vector 维度"""
        return 1024


settings = Settings()

# 确保 SQLite 目录存在
settings.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
