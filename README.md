# personal-library — 个人 RAG 知识库

监听本地目录自动入库，提供**混合检索**（BM25 + 向量 + 可选重排）与**带引用的多轮 RAG 问答**的本地服务。
数据（文档 / SQLite / 向量）全部在本机；仅 embedding、LLM、可选 rerank 与 VLM 调用出网。

- 需求与设计决策见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)、[docs/DESIGN.md](docs/DESIGN.md)
- 网页端：`http://127.0.0.1:8790/`（桌面软件式工作台：问答 / 文档 / 设置，PWA 可安装）

## 快速开始

```bash
# 0) 依赖（已装好可跳过）
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # 或按 pyproject.toml 手动安装

# 1) 启动 Qdrant（向量库；国内拉不动可换 docker.m.daocloud.io 镜像）
docker compose up -d

# 2) 配置
cp .env.example .env   # 填 API_KEYS（自定随机串）、WATCH_DIRS、智谱 EMBED_API_KEY/LLM_API_KEY

# 3) 启动服务（或用下方 launchd 常驻方式）
.venv/bin/python -m app.main
```

把文档放进 `WATCH_DIRS` 目录即自动入库（增量监听 + 启动/周期对账）；删除文件即同步删除索引。
也可以在网页端直接**上传文件**、**粘贴网页 / B站链接**入库。

## 桌面版（免环境客户端）

打包成双击即用的 .app / .exe：**目标机器无需 Python、Docker、ffmpeg**（向量库内嵌，静态 ffmpeg 随包）。

```bash
bash scripts/build_app.sh        # macOS → dist/personal-library.app
# Windows 在 Windows 机器上: .\scripts\build_app_win.ps1
```

数据目录、签名分发、局域网访问、迁移备份见 [docs/DESKTOP.md](docs/DESKTOP.md)。

## 功能一览

| 能力 | 说明 |
|---|---|
| 混合检索 | BM25（jieba FTS5）+ 向量（Qdrant）RRF 融合，可选 SiliconFlow 重排 |
| 多轮问答 | `/ask` 支持携带 `history`，追问可理解指代；SSE 流式；回答带 `[n]` 引用角标 |
| 引用面板 | 右栏**按文档聚合**展示引用来源，点击角标定位正文原句 |
| 元问题路由 | 问「知识库里有哪些文档 / 最近入库了什么」直接查文档表秒答，不走向量检索 |
| 对话持久化 | 对话存服务端 SQLite（`GET/POST/DELETE /api/v1/threads`），localStorage 仅作离线缓存 |
| 入库即消化 | 每篇文档索引完成后自动生成「摘要 + 3 个关键问题」，展示在文档列表与预览；也可手动/批量生成 |
| 小测验 | 从任意文档出题（单选 + 判断 + 简答 AI 判分），题量 10/30/50、可指定主题重点，每题解析带原文出处，结束有错题回顾与重做（`#/quiz` 第四视图；文档预览也有「出题测验」入口） |
| PDF | 虚拟化按页预览；扫描页自动 OCR；含图页可走 VLM 图像理解 |
| Office | docx / pptx / xlsx 转 Markdown 后按标题感知分块入库 |
| URL 剪藏 | 正文落盘 `data/clips/`，哈希去重、增量更新；CSDN/公众号文章开箱即用 |
| B站视频笔记 | 粘贴 bilibili / b23.tv 链接 → 拉字幕 → LLM 整理成带 `[mm:ss]` 时间戳的结构化笔记入库；支持扫码登录获取 AI 字幕（SESSDATA 仅存本机） |
| 无字幕视频兜底 | B站无字幕视频自动走 **ASR 语音转写**（智谱 GLM-ASR，≤28s/块分块转写，15 分钟/条上限）→ LLM 笔记入库（需 ffmpeg） |
| PWA | 可安装；安卓可把链接「分享」进应用直接入库（需 HTTPS，推荐 Tailscale） |

## API（前缀 `/api/v1`，除 health 外需 `X-API-Key` 头）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 依赖状态与索引统计（免认证） |
| GET | `/search?q=关键词&doc_type=md&source=watch&topk=10` | 混合检索，返回片段+出处 |
| POST | `/ask` | `{"question": "...", "stream": true, "history": [{"role":"user","content":"..."},...]}`；stream=true 为 SSE |
| GET / POST / DELETE | `/threads`（DELETE 用 `?id=`） | 对话列表 / upsert / 删除 |
| POST | `/quiz` | body `{"doc_id","count":10|30|50,"focus"?}`，异步出题（202 任务） |
| GET | `/quiz` · `/quiz?id=` | 测验历史 / 单套完整题目（简答参考答案判分后才下发） |
| POST | `/quiz/grade` | body `{"id","index","answer"}`，简答题 LLM 判分（2/1/0 档） |
| POST | `/quiz/result` · DELETE `/quiz?id=` | 提交得分（更新最好成绩）/ 删除测验 |
| GET | `/documents` · `/documents/{id}` | 列表 / 详情（含 chunk 预览与 `summary` 摘要字段） |
| DELETE | `/documents/{id}` | 删除文档及索引 |
| POST | `/documents/{id}/reindex` | 强制重新解析索引 |
| POST | `/documents/digest` | body `{"doc_id": "..."}`，生成/重生成该文档摘要（202 任务） |
| POST | `/documents/digest-missing` | 为所有缺摘要的已索引文档批量生成 |
| POST | `/ingest/url` · `/ingest/video` · `/ingest/upload` | 网页 / B站视频 / 文件上传入库（异步任务） |
| GET | `/tasks/{id}` · `/tasks` · POST `/ingest/reconcile` | 任务状态 / 手动对账 |
| GET / PUT | `/config` · `/fs/dirs` · `/bilibili/qr/*` | 运行时配置 / 服务端目录浏览 / B站扫码 |

SSE 事件序列：`{"type":"sources",...}` → `{"type":"delta","text":...}*` → `{"type":"done"}`。

## 行为说明

- **未配置智谱 key 时**：关键词检索照常可用，向量检索与问答不可用（/health 显示 embed/llm 为 false）
- **快手**：当前 yt-dlp 已移除快手 extractor，暂不支持（粘贴快手链接会得到明确提示）
- ASR 转写复用智谱 key、按量计费；抖音/快手视频链接会给出明确的「不支持」提示
- **排障**：`kill -USR1 <服务pid>` 把全部线程栈转储到 `data/logs/stack_dump.log`（卡死类问题定位）
- **同内容移动/重命名**：按内容哈希识别，只改路径不重新嵌入
- **大文件**：直接放监听目录，无 HTTP 上传限制；未变更文件（size+mtime 未变）不重复解析
- **摘要失败不阻塞入库**：LLM 不可用时摘要跳过，可在预览页手动补生成

## 常驻运行与备份

服务通过 macOS launchd（用户级 LaunchAgent）常驻：登录自启、崩溃约 10 秒内自动拉起、每日 03:03 自动维护。

```bash
.venv/bin/python scripts/maintain.py install    # 安装并加载（幂等）
.venv/bin/python scripts/maintain.py status     # 查看服务状态
.venv/bin/python scripts/maintain.py backup     # 手动备份（data/backups/，保留 7 份）
```

- 服务日志在 `data/logs/`；备份内容为 SQLite（唯一事实源）+ 运行时配置
- **Qdrant 向量不备份**——恢复后 `POST /api/v1/ingest/reconcile?force=true` 全量重嵌入重建
- 改代码后重载：`launchctl kickstart -k gui/$(id -u)/com.personal-library.server`

## 测试与检索质量评估

```bash
.venv/bin/python -m pytest tests/ -q                # 离线单元测试（全 mock，不触网）
.venv/bin/python scripts/eval_rag.py --token <key>  # 金标准问答集检索命中评估（需服务在线）
```

金标准题集在 `data/golden_questions.json`（question + expected 标题子串），随你的库内容自行维护。

## 故障排查

- **Qdrant 拉取失败**（网络）：`docker-compose.yml` 镜像改为 `docker.m.daocloud.io/qdrant/qdrant:latest`
- **检索无结果**：看 `/api/v1/health` 的 documents/chunks 是否增长
- **问答 503**：未配置 `LLM_API_KEY`（或在网页「设置」里填，热生效）
- **局域网访问不了**：确认 `APP_HOST=0.0.0.0` 且防火墙放行 8790 端口
