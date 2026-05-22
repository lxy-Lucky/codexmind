from __future__ import annotations

import hashlib
import logging
import os
import re
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

# 永远跳过的目录名（所有语言通用）
SKIP_DIRS: set[str] = {
    # VCS
    ".git", ".svn", ".hg",
    "CVS", "cvs",                  # CVS 版本控制元数据
    # Node / 前端包管理
    "node_modules", "bower_components", "jspm_packages",
    # Python
    "__pycache__", ".venv", "venv", "env", "site-packages", ".tox", ".nox",
    # 构建 / 输出 / IDE
    "target", "build", "dist", ".gradle", ".idea", ".vscode",
    "out", "bin", ".cache", ".mypy_cache", ".pytest_cache",
    "cmake-build-debug", "cmake-build-release",
    # Java Web 编译产物 / 运行时临时目录
    "classes",                     # WEB-INF/classes — 编译后 .class 文件
    "work",                        # Tomcat work 目录（JSP 编译缓存）
    # 第三方库 / vendor（跨语言）
    "vendor", "vendors",
    "third_party", "thirdparty",
    "external", "externals",
    "deps",
    # iOS / macOS
    "Pods", "Carthage",
    # Ruby
    ".bundle",
    # Java web 内嵌资源（Bootstrap / jQuery 等打包进 jar 的静态文件）
    "webjars",
    # 静态资产目录（图片，扩展名不在支持列表，跳过可减少 IO）
    "img", "imgs", "images",
}

# 永远跳过的文件名（精确匹配，全部小写；检索时用 fname.lower() 比较）
SKIP_FILES: set[str] = {
    ".ds_store", "thumbs.db", ".gitignore", ".gitattributes",
    # ── XML：构建 / 日志 / Servlet 配置（MyBatis Mapper 由 indexer 白名单保留）──
    "pom.xml", "web.xml",
    "logback.xml", "logback-spring.xml",
    "log4j.xml", "log4j2.xml", "log4j2-spring.xml",
    # ── 已知第三方 JS 库（未压缩原版）──────────────────────────────────────────
    "jquery.js", "jquery.slim.js",
    "bootstrap.js", "bootstrap.bundle.js",
    "moment.js", "moment-with-locales.js",
    "lodash.js", "underscore.js",
    "axios.js",
    "angular.js",
    "react.js", "react-dom.js",
    "react.development.js", "react-dom.development.js",
    "react.production.min.js", "react-dom.production.min.js",
    "vue.js", "vue.global.js", "vue.esm-browser.js",
    "d3.js", "echarts.js", "highcharts.js",
    "popper.js", "tippy.js",
    "select2.js", "datatables.js",
    "swiper.js", "slick.js",
    "require.js", "requirejs.js",
}

# 版本号标记的第三方库文件名正则（如 jquery-3.6.0.js / bootstrap.5.3.2.js）
VENDOR_VERSION_RE = re.compile(
    r'^(?:jquery|bootstrap|angular|moment|lodash|underscore|axios|'
    r'react(?:-dom)?|vue|d3|echarts|highcharts|select2|datatables|'
    r'swiper|popper|tippy|require|knockout|backbone|font.?awesome)'
    r'[.\-]\d',
    re.IGNORECASE,
)

# 跳过的文件名后缀（含这些后缀的视为压缩/打包库文件，不参与索引）
SKIP_SUFFIXES: tuple[str, ...] = (
    ".min.js", ".min.css",
    ".bundle.js", ".chunk.js",
    "-min.js", "-min.css",
)


def get_language(path: Path) -> Optional[str]:
    return EXT_TO_LANG.get(path.suffix.lower())


def _should_skip_dir(
    name: str,
    rel_from_root: str,
    extra_skip_dirs: frozenset[str],
) -> bool:
    """
    判断目录是否命中用户自定义跳过规则。

    extra_skip_dirs 两种写法：
      - 纯目录名（不含 / 和 \）→ 匹配任意层级同名目录
        例：'Property'  →  跳过所有叫 Property 的目录
      - 相对路径（含 /）      → 只匹配从 repo root 起的特定路径
        例：'WEB-INF/src/Property'  →  只跳过那一个
    """
    rel_norm = rel_from_root.replace("\\", "/")
    for rule in extra_skip_dirs:
        rule_norm = rule.replace("\\", "/").strip("/")
        if "/" in rule_norm:
            # 路径规则：精确或前缀匹配
            if rel_norm == rule_norm or rel_norm.startswith(rule_norm + "/"):
                return True
        else:
            # 名称规则：任意层级
            if name == rule_norm:
                return True
    return False


def make_repo_id(root_path: str) -> str:
    """根据路径生成稳定的 repo_id（sha1 前 12 位）"""
    return hashlib.sha1(root_path.encode()).hexdigest()[:12]


# ── 文件树扫描 ───────────────────────────────────────────────────────────────

def scan_file_tree(
    root: Path,
    rel_base: Optional[Path] = None,
    max_depth: int = 8,
    _depth: int = 0,
    extra_skip_dirs: frozenset[str] = frozenset(),
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
        if entry.name.lower() in SKIP_FILES:
            continue

        rel_path = str(entry.relative_to(rel_base))

        if entry.is_dir() and (
            entry.name in SKIP_DIRS
            or _should_skip_dir(entry.name, rel_path, extra_skip_dirs)
        ):
            continue

        if entry.is_dir():
            children = scan_file_tree(entry, rel_base, max_depth, _depth + 1, extra_skip_dirs)
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

def detect_encoding(raw: bytes) -> str:
    """按优先级尝试常见编码，返回第一个能无损解码的编码名。"""
    for encoding in ("utf-8", "cp932", "gb2312", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


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

    async with aiofiles.open(full_path, "rb") as f:
        raw = await f.read()
    encoding = detect_encoding(raw)
    content = raw.decode(encoding, errors="replace")

    lang = get_language(full_path) or "plaintext"
    line_count = content.count("\n") + 1

    return FileContentResponse(
        path=rel_path,
        language=lang,
        content=content,
        line_count=line_count,
        size=size,
    )
