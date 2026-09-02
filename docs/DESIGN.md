# 架构设计 — 个人 RAG 知识库

> 依据 [REQUIREMENTS.md](REQUIREMENTS.md)。本文记录需求文档第 7 节全部开放问题的最终决策及理由。

## 1. 总体架构

```
                 ┌────────────────────── 本机 (macOS, 仅局域网) ─────────────────────┐
                 │                                                                    │
 监听目录 ──fs事件──▶ ┌─────────┐   解析/分块   ┌──────────────┐  向量  ┌──────────┐ │
 (WATCH_DIRS)    │ │ 入库管线 │ ───────────▶ │  SQLite 注册表 │        │  Qdrant  │ │
                 │ │ (worker) │              │  docs/chunks/ │  稠密  │ (Docker) │ │
 URL ──抓取正文──▶ │          │ ──嵌入调用──▶ │  chunks_fts   │ ─────▶ │          │ │
                 │ └─────────┘              └──────────────┘        └──────────┘ │
                 │      ▲                                                          │
                 │      │ 转存为 md 剪藏文件                                        │
                 │  FastAPI (检索/问答/文档管理)  ◀── API Key ── 局域网设备/Agent    │
                 └────────────────────────────────────────────────────────────────┘
                          │ embedding / LLM / rerank (可选)
                          ▼
                    智谱开放平台 (OpenAI 兼容) ；rerank 走 SiliconFlow(可选)
```

**统一入库原则**：文件只有一条入库路径——监听目录。URL 抓取的正文也落成 `<DATA_DIR>/clips/` 下的 md 文件再走同一管线，天然获得去重、状态跟踪与失败重试。

**SQLite 是事实源（source of truth）**：文档注册表、chunk 原文、FTS 索引、任务状态都在 SQLite；Qdrant 只存稠密向量 + 最小 payload（doc_id/chunk_id），可随时从 SQLite 全量重建。

## 2. 五个开放问题的决策

### 2.1 关键词索引：SQLite FTS5 + jieba（而非 Qdrant sparse）

- `chunks_fts`（FTS5, unicode61）存 jieba 分词后的文本，查询侧同样分词，得到真正的中文 BM25。
- 理由：不用维护 Qdrant sparse 向量与 IDF 状态；SQLite 本地零运维、可备份、易调试；十万级 chunk 毫无压力。
- 混合融合：FTS top50 + 向量 top50 → **RRF（k=60）** 在应用层融合。
- 中文查询 <2 字时 FTS5 匹配弱，此时以向量检索为主，FTS 结果仅作补充。

### 2.2 Rerank：SiliconFlow 托管 bge-reranker-v2-m3（可选，优雅降级）

- 配置 `RERANK_API_KEY` 即启用（SiliconFlow `/v1/rerank`，国内可直连、便宜）；不配置则自动跳过 rerank，仅 RRF 排序，服务不受影响。
- RerankProvider 做成接口，未来可换本地 bge-reranker 或其他厂商。

### 2.3 OCR：RapidOCR（本地 ONNX，可选加载）

- 选 `rapidocr-onnxruntime`：中文效果好、模型随包内置（离线）、CPU 即可、macOS arm64 无 paddle 依赖痛点。
- 触发条件：PDF 某页提取文本 < 32 字符（疑似扫描页）→ PyMuPDF 渲染 200 DPI 位图 → OCR。
- 未安装该包或 `OCR_ENABLED=false` 时优雅跳过（记录 warning），架构上 `OcrEngine` 接口可换 PaddleOCR / GLM-4V。

### 2.4 分块策略（按类型差异化，字符预算制）

统一预算：目标 ~1100 字符（≈ 中文 700 字 / 英文 ~180 tokens），重叠 ~150 字符；上下文前缀（标题路径/文件路径）计入预算。

| 类型 | 切分方式 |
|---|---|
| Markdown | 按 ATX 标题切 section（携带标题层级路径前缀），超长 section 再按段落二次切分 |
| PDF | 按页提取文本 → 段落聚合到预算，chunk 带页码；扫描页走 OCR 后同样处理 |
| HTML / URL | trafilatura 提取正文（含标题结构）→ 同 Markdown 策略 |
| 代码 | 优先按 def/class/函数边界（正则启发式）切，超长块按行窗口切；前缀带相对路径 |

### 2.5 文件监听：watchdog 事件 + 双保险对账

- watchdog `Observer`（macOS 走 FSEvents）+ 1.5s 防抖批量处理。
- **启动全量对账** + 每 5 分钟周期对账：比较 (path, size, mtime, sha256) 与注册表，兜底捕获漏掉的事件、进程离线期间的变更；删除文件 → 级联删除 chunk/向量。
- hash 相同的"移动"只更新 path 不重新嵌入。

## 3. 数据模型

```sql
documents(id, source[watch|url], path, url, title, doc_type[md|pdf|html|code|text],
          hash, size, mtime, status[pending|indexed|failed], error,
          chunk_count, created_at, updated_at, indexed_at)
chunks(chunk_id PK, doc_id FK, seq, heading, page, text, nchars)
chunks_fts(body, chunk_id UNINDEXED)          -- FTS5, jieba 预分词
tasks(id, kind[url_ingest|file|reindex], status[queued|running|done|failed],
      error, doc_id, created_at, updated_at)
```

- chunk_id = `{doc_id}:{seq}`；Qdrant point id = chunk_id 的 UUID5。
- 文件事件处理与注册表更新在同一事务，保证索引与元数据一致。

## 4. 检索与问答流程

**检索 `/api/v1/search`**：
1. 查询预处理（jieba 分词）
2. 并行：FTS5 BM25 top50（按元数据过滤 doc_id 集）+ Qdrant 稠密 top50（payload filter）
3. RRF 融合去重 → （可选 rerank top30 → topN）
4. 返回 chunk + 文档来源（title/path/page/heading）+ 分数

**问答 `/api/v1/ask`**：
1. 上述检索取 top6 作为上下文（编号 [1..6]）
2. 提示词要求：仅依据上下文回答、标注引用编号、上下文不足时明确说明
3. 流式：SSE 事件 `sources → delta* → done`；非流式：JSON `{answer, sources}`
4. LLM 走智谱 OpenAI 兼容端点（`openai` SDK，`base_url` 可换任意厂商）

## 5. API 一览（前缀 /api/v1，除 health 外均需 API Key）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/v1/health | 存活 + 依赖状态（Qdrant/模型配置） |
| POST | /api/v1/ingest/url | `{url}` → 抓取入库（异步任务） |
| GET | /api/v1/documents | 列表（分页、type/source/status 过滤） |
| GET | /api/v1/documents/{id} | 详情含 chunk 预览 |
| DELETE | /api/v1/documents/{id} | 删除文档及索引 |
| POST | /api/v1/documents/{id}/reindex | 重新解析入索引 |
| GET | /api/v1/tasks/{id} · /api/v1/tasks | 任务状态 |
| GET | /api/v1/search | `q` + 过滤参数 → 检索结果 |
| POST | /api/v1/ask | `{question, stream?}` → 问答（JSON 或 SSE） |

## 6. 工程结构

```
app/            config / security / main
 ├─ api/        路由层（health, documents, search, ask, ingest, tasks）
 ├─ core/       db(SQLite+FTS5) embeddings llm rerank ocr qdrant_store retriever
 └─ ingest/     watcher pipeline chunking url_fetcher parsers/
tests/          离线单元测试（不依赖网络与 Qdrant）
scripts/        smoke_test.py（填入 key 后一条命令端到端验证）
docker-compose.yml   仅 Qdrant；应用跑宿主机（文件监听需要真实文件系统）
```

**应用跑宿主机而非容器**：监听目录必须是宿主机真实文件系统（macOS Docker 挂载 FSEvents 不可靠），故 compose 只含 Qdrant。
