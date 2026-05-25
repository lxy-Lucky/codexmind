from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env —— 相对于本模块文件位置解析，不依赖 CWD
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
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

    # ── Neo4j ───────────────────────────────────────
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "codexmind"

    # ── Embedding ───────────────────────────────────
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cuda"
    EMBEDDING_BATCH_SIZE: int = 8
    # bge-m3 原生支持 8192；2048 在 chunk 表达力和显存间取平衡
    EMBEDDING_MAX_LENGTH: int = 2048

    # ── Ollama ──────────────────────────────────────
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:14b"
    OLLAMA_TIMEOUT: int = 120
    LLM_TRIGGER_THRESHOLD: float = 0.55

    # ── Indexer ─────────────────────────────────────
    # 配合 EMBEDDING_MAX_LENGTH=2048：大多数业务方法一窗装得下，减少切窗碎片
    CHUNK_MAX_TOKENS: int = 1500
    CHUNK_OVERLAP_TOKENS: int = 150
    SUPPORTED_EXTENSIONS: str = ".java,.js,.ts,.jsx,.tsx,.vue,.xml"

    # ── BM25 ────────────────────────────────────────
    BM25_INDEX_DIR: Path = Path("./data/bm25")

    # ── SQLite ──────────────────────────────────────
    SQLITE_PATH: Path = Path("./data/history.db")

    # ── Security ────────────────────────────────────
    ALLOWED_REPO_ROOTS: str = ""

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
        if not self.allowed_roots:
            return True
        resolved = path.resolve()
        # 用 is_relative_to 而非 str.startswith，避免 /home/user/repos 误匹配 /home/user/repos2
        return any(resolved.is_relative_to(root.resolve()) for root in self.allowed_roots)

    @property
    def vector_size(self) -> int:
        return 1024


settings = Settings()
