# CodexMind Backend

代码库智能检索与分析系统 — 后端服务

## 环境要求

- Ubuntu 24.04 LTS
- Python 3.11+
- CUDA 13.2 + NVIDIA RTX 5070 Ti 16GB
- Docker（用于运行 Qdrant）
- Ollama（已安装并拉取模型）

---

## 快速启动

### 1. 启动 Qdrant

```bash
docker compose up -d
```

验证：http://localhost:6333/dashboard

---

### 2. 安装 Ollama 模型

```bash
# 推荐：Qwen2.5-Coder 32B Q4（约 20GB，16GB 显存 + 内存 swap）
ollama pull qwen2.5-coder:32b

# 如果显存不够，备选 14B
ollama pull qwen2.5-coder:14b
```

---

### 3. 安装 Python 依赖

```bash
# 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装 PyTorch（CUDA 13.2 对应 torch 2.3+）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 安装项目依赖
pip install -e .
```

---

### 4. 配置环境变量

```bash
cp .env.example .env
# 按需修改 .env：
#   OLLAMA_MODEL=qwen2.5-coder:32b   # 或 14b
#   ALLOWED_REPO_ROOTS=/home,/workspace   # 安全白名单（可选）
```

---

### 5. 启动后端

```bash
python -m app.main
# 或
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API 文档：http://localhost:8000/docs

---

## 使用流程

### Step 1：注册仓库

```bash
curl -X POST http://localhost:8000/api/repo \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "root_path": "/home/user/projects/my-project"}'
```

返回 `repo_id`（12位 hash）。

### Step 2：触发索引

```bash
curl -X POST http://localhost:8000/api/repo/{repo_id}/index
```

查看进度：
```bash
curl http://localhost:8000/api/repo/{repo_id}/index/status
```

### Step 3：语义检索

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "用户登录在哪实现", "repo_id": "{repo_id}"}'
```

### Step 4：LLM 分析（SSE 流）

```bash
curl -N -X POST http://localhost:8000/api/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "{repo_id}",
    "file_path": "src/auth/AuthController.java",
    "line_start": 20,
    "line_end": 45,
    "code": "...",
    "mode": "summary"
  }'
```

---

## API 一览

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/repo` | 注册仓库 |
| GET | `/api/repo` | 仓库列表 |
| GET | `/api/repo/{id}` | 仓库详情 |
| DELETE | `/api/repo/{id}` | 删除仓库 |
| POST | `/api/repo/{id}/index` | 触发索引 |
| GET | `/api/repo/{id}/index/status` | 索引进度 |
| GET | `/api/repo/{id}/index/logs` | 索引日志 |
| GET | `/api/repo/{id}/tree` | 文件树 |
| GET | `/api/repo/{id}/file?path=xxx` | 文件内容 |
| POST | `/api/search` | 语义检索 |
| GET | `/api/search/history?repo_id=xxx` | 查询历史 |
| DELETE | `/api/search/history/{id}` | 删除历史记录 |
| POST | `/api/analyze/stream` | LLM 分析（SSE） |
| GET | `/api/status` | 系统状态 |

---

## 目录结构

```
app/
├── main.py              # FastAPI 入口
├── api/
│   ├── repo.py          # 仓库管理
│   ├── search.py        # 语义检索
│   └── analysis.py      # LLM 分析 SSE
├── services/
│   ├── repo_service.py  # 文件系统操作
│   ├── indexer_service.py  # 向量索引构建
│   ├── parser_service.py   # tree-sitter 解析
│   ├── search_service.py   # Qdrant 检索
│   └── llm_service.py      # Ollama 调用
├── models/
│   ├── repo.py
│   ├── search.py
│   └── analysis.py
├── core/
│   ├── config.py        # 全局配置
│   ├── qdrant_client.py # Qdrant 单例
│   └── embedder.py      # bge-m3 单例
└── db/
    └── database.py      # SQLite 初始化
```
