from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import aiofiles

from app.core.config import settings
from app.models.repo import FileNode, FileContentResponse

logger = logging.getLogger(__name__)

# ── 语言映射 ──────────────────────────────────────────────────────────────────

EXT_TO_LANG: dict[str, str] = {
    ".java":  "java",
    ".py":    "python",
    ".ts":    "typescript",
    ".tsx":   "typescript",
    ".js":    "javascript",
    ".jsx":   "javascript",
    ".go":    "go",
    ".kt":    "kotlin",
    ".scala": "scala",
    ".rs":    "rust",
    ".cpp":   "cpp",
    ".cc":    "cpp",
    ".c":     "c",
    ".cs":    "csharp",
    ".rb":    "ruby",
    ".php":   "php",
    ".swift": "swift",
    ".vue":   "vue",
    ".md":    "markdown",
    ".yaml":  "yaml",
    ".yml":   "yaml",
    ".json":  "json",
    ".toml":  "toml",
    ".xml":   "xml",
    ".sql":   "sql",
    ".sh":    "bash",
}

# 永远跳过的目录名
SKIP_DIRS: set[str] = {
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    "target", "build", "dist", ".gradle", ".idea", ".vscode",
    "out", "bin", ".cache", ".mypy_cache", ".pytest_cache",
}

# 永远跳过的文件名
SKIP_FILES: set[str] = {
    ".DS_Store", "Thumbs.db", ".gitignore", ".gitattributes",
}


def get_language(path: Path) -> Optional[str]:
    return EXT_TO_LANG.get(path.suffix.lower())


def make_repo_id(root_path: str) -> str:
    """根据路径生成稳定的 repo_id（sha1 前 12 位）"""
    return hashlib.sha1(root_path.encode()).hexdigest()[:12]


# ── 文件树扫描 ───────────────────────────────────────────────────────────────

def scan_file_tree(
    root: Path,
    rel_base: Optional[Path] = None,
    max_depth: int = 8,
    _depth: int = 0,
) -> list[FileNode]:
    """递归扫描目录，返回 FileNode 树"""
    if rel_base is None:
        rel_base = root

    if _depth > max_depth:
        return []

    nodes: list[FileNode] = []

    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return []

    for entry in entries:
        if entry.name in SKIP_FILES:
            continue
        if entry.is_dir() and entry.name in SKIP_DIRS:
            continue

        rel_path = str(entry.relative_to(rel_base))

        if entry.is_dir():
            children = scan_file_tree(entry, rel_base, max_depth, _depth + 1)
            nodes.append(FileNode(
                name=entry.name,
                path=rel_path,
                type="dir",
                children=children,
            ))
        elif entry.is_file():
            lang = get_language(entry)
            # 只展示支持或常见的文件，跳过二进制
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            nodes.append(FileNode(
                name=entry.name,
                path=rel_path,
                type="file",
                language=lang,
                size=size,
                children=None,
            ))

    return nodes


def count_code_files(root: Path) -> int:
    """统计 root 下支持的代码文件数量"""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext in settings.supported_ext_set:
                count += 1
    return count


def detect_primary_language(root: Path) -> Optional[str]:
    """统计各语言文件数，返回占比最高的语言"""
    counter: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            lang = get_language(Path(f))
            if lang:
                counter[lang] = counter.get(lang, 0) + 1
    if not counter:
        return None
    return max(counter, key=lambda k: counter[k])


# ── 文件内容读取 ──────────────────────────────────────────────────────────────

async def read_file_content(root: Path, rel_path: str) -> FileContentResponse:
    """读取单个文件内容，验证路径安全性"""
    full_path = (root / rel_path).resolve()

    # 安全检查：不允许路径逃逸（用 is_relative_to 而非 startswith，避免前缀误判）
    if not full_path.is_relative_to(root.resolve()):
        raise PermissionError(f"路径逃逸: {rel_path}")

    if not full_path.exists():
        raise FileNotFoundError(f"文件不存在: {rel_path}")

    if not full_path.is_file():
        raise IsADirectoryError(f"不是文件: {rel_path}")

    size = full_path.stat().st_size
    if size > 2 * 1024 * 1024:  # 2MB 上限
        raise ValueError(f"文件过大 ({size} bytes)，拒绝读取: {rel_path}")

    async with aiofiles.open(full_path, encoding="utf-8", errors="replace") as f:
        content = await f.read()

    lang = get_language(full_path) or "plaintext"
    line_count = content.count("\n") + 1

    return FileContentResponse(
        path=rel_path,
        language=lang,
        content=content,
        line_count=line_count,
        size=size,
    )
