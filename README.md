# Memo Book

<div align="center">
  <img src="docs/screenshots/hero.png" alt="Memo Book 主界面预览" width="800" />
</div>

<p align="center">
  <strong>本地优先的个人 RAG 知识库</strong><br />
  自动解析文档 / 代码 / 网页 / B 站视频，基于混合检索提供带引用来源的智能问答。
</p>

<p align="center">
  <a href="README.en.md">English</a> · <a href="docs/DESIGN.md">设计文档</a> · <a href="docs/DESKTOP.md">桌面版说明</a> · <a href="docs/REQUIREMENTS.md">需求文档</a>
</p>

---

## 项目简介

Memo Book 是一个自托管（self-hosted）的个人 RAG 知识库：把资料放进指定目录，系统会自动完成解析、分块、建索引；之后你可以用自然语言向自己的资料提问，模型给出的回答会标注引用编号，并支持一键跳回原文片段。

它主要面向以下使用场景：

- 本地有大量 PDF、Markdown、网页剪藏、项目源码或 B 站收藏，希望统一检索和问答；
- 对数据隐私敏感，不希望把私人文档上传到云端知识库；
- 希望自由切换模型服务商，而不是被某一厂商锁定。

### 与云端知识库的区别

| | 云端知识库 | Memo Book |
|---|---|---|
| 数据存储 | 上传到第三方服务器 | 全部保存在本机 |
| 模型选择 | 通常由平台限定 | 任意 OpenAI 兼容接口，默认智谱 GLM |
| 引用溯源 | 不一定可到原文 | 回答带 `[n]` 引用，点击定位原文 |
| 使用方式 | Web 订阅服务 | 本地 Web / PWA / 桌面应用 / HTTP API |

---

## 典型应用场景

| 场景 | 典型输入 | 主要能力 |
|---|---|---|
| 个人笔记与资料库 | Obsidian Vault、Markdown、txt、org 笔记 | 文件夹自动监听入库，跨笔记语义检索与引用问答 |
| 论文与书籍阅读 | PDF（含扫描版） | 自动 OCR / 图像理解，按页检索并回到原文页码 |
| 技术文档与源码沉淀 | 项目源码、README、代码片段 | 递归扫描并遵循 `.gitignore`，代码按函数 / 类边界分块 |
| 网页与公众号剪藏 | HTML、普通网页、CSDN / 公众号链接 | 提取正文转 Markdown 后统一入库 |
| B 站课程与视频笔记 | B 站视频链接 | 拉取字幕生成带时间戳笔记；无字幕时语音转写 |
| Office 资料管理 | Word / PPT / Excel 文档 | 自动转 Markdown，按标题分块检索 |
| 隐私敏感资料 | 本地不便于上传云端的文档 | 数据全部留在本机，仅模型调用出网 |

---

## 核心特性

### 自动化入库

- **文件夹监听**：对 `WATCH_DIRS` 指定目录递归扫描，通过文件系统事件增量感知新增、修改、删除；
- **干净入库**：自动跳过 `node_modules`、`dist`、`__pycache__`、锁文件等构建产物，并遵循逐层 `.gitignore` 规则；
- **多格式解析**：Markdown、PDF（扫描页自动 OCR）、Word / PPT / Excel、HTML、txt、23 种常见代码扩展名、图片（VLM 生成描述）；
- **网页与视频**：粘贴普通网页 / CSDN / 公众号链接可提取正文入库；B 站视频自动拉取字幕并生成带时间戳的笔记，无字幕时走语音转写；
- **解析状态可见**：文档树顶部提供实时「解析中」队列，每篇文件显示解析进度与状态，失败可追踪。

### 混合检索与引用问答

- **双路召回**：jieba 分词后的 SQLite FTS5 BM25 关键词检索 + Qdrant 稠密向量检索，应用层以 RRF 融合排序；
- **可选 Rerank**：配置 SiliconFlow 等 rerank 服务后可进一步提升排序效果，未配置时优雅降级；
- **流式问答**：回答以 SSE 流式输出，并带 `[n]` 引用角标；来源在右侧按文档聚合，点击即可定位原文片段；
- **多轮对话**：支持追问与指代理解；知识库统计类问题走元问题路由，直接查库秒答，不浪费模型调用。

### 文档管理

- 按监听目录的真实层级展示多级可折叠目录树；
- 入库后自动生成 AI 摘要与 3 个关键问题；
- PDF 按页预览；Office / 代码 / 网页显示转换后的 Markdown；
- 支持文档删除、强制重建索引、任务状态查询。

### 测验

- 指定主题后从知识库自动选材出题：单选 / 判断 / 简答；
- 即时判分；简答题由 LLM 评分并给出评语；
- 每道题附带原文出处，支持错题回顾与最佳成绩记录。

### 多端与桌面

- 内置 **PWA**，浏览器打开即可安装使用；
- 电脑端生成二维码，手机扫码后自动完成局域网配对，之后免输入口令；
- **桌面版**：macOS / Windows 安装包内嵌向量库与静态 ffmpeg，目标机器无需 Python、Docker、ffmpeg，双击即可运行；
- 同一套 HTTP API 可被脚本或 Agent 调用。

---

## 支持的文件与输入类型

### 本地文件

| 类别 | 扩展名 / 格式 |
|---|---|
| Markdown | `.md`、`.markdown` |
| PDF | `.pdf`（扫描页自动 OCR / 视觉转写；内嵌图片可追加图像理解描述） |
| HTML | `.html`、`.htm`、`.xhtml` |
| 纯文本 | `.txt`、`.text`、`.log`、`.rst`、`.org` |
| Office | `.docx`（Word）、`.pptx`（PowerPoint）、`.xlsx` / `.xls`（Excel） |
| 图片 | `.png`、`.jpg`、`.jpeg`（需配置图像理解模型） |
| 代码 | `.py` `.js` `.ts` `.tsx` `.jsx` `.go` `.java` `.c` `.h` `.cpp` `.hpp` `.rs` `.rb` `.sh` `.sql` `.json` `.yaml` `.yml` `.toml` `.swift` `.kt` `.php` `.lua`，共 23 种 |

### 代码与配置类文件如何处理

`py`、`rs`、`c`、`cpp`、`go`、`java`、`json`、`toml`、`yaml`、`sh`、`sql` 等文件会按**纯文本读取**进入系统：不会做语法树 / AST 解析，也不会理解注释、字符串或 JSON / TOML 的结构语义。它们能入库是因为扩展名在支持列表中，而不是靠“txt 兜底”。

但代码类文件在入库时仍有两个针对性的处理：

- **按代码结构分块**：优先识别 `def`、`class`、`func`、`function`、`fn`、`struct`、`impl`、`export`、`async def` 等边界，按函数 / 类 / 结构体切块；
- **无结构边界时按行窗口切块**：如普通脚本、`.json`、`.toml` 等没有明显代码边界的文件，会退化为按行 + 重叠窗口分块，效果上接近文本分块；
- **携带路径上下文**：每个 chunk 会带上文件路径前缀，检索结果可以定位回具体文件。

因此当前实现适合“搜索/引用某段代码或配置出现在哪个文件”，但不适合做跨文件的语法级分析（如调用链、类型推导）。

### 链接与网络输入

- 网页：支持普通 `http(s)` HTML 页面，常见 CSDN、公众号等正文可提取；PDF 链接请下载后放入监听目录；
- 视频：支持 B 站链接（`bilibili.com`、`b23.tv` 等），有字幕时生成带时间戳的笔记，无字幕且时长 ≤ 15 分钟时走语音转写。

### 当前不支持的常见格式 / 输入

| 类型 | 原因 / 建议 |
|---|---|
| 旧版 Word `.doc` | 不支持；请另存为 `.docx` |
| 图片 `.webp`、`.gif`、`.bmp` 等 | 目前只支持 `.png` / `.jpg` / `.jpeg` |
| 压缩包 `.zip`、`.rar`、`.7z` | 请解压后放入监听目录 |
| 电子书 `.epub`、`.mobi`、`.azw3` | 暂不支持，可先转为 Markdown / PDF |
| 本地音频 / 视频文件 | 暂不直接解析，仅支持 B 站链接的视频笔记 |
| 抖音 / 快手视频链接 | 已识别但明确不支持 |
| 无扩展名或未知二进制文件 | 无法判断类型，会被跳过 |

### 自动忽略的内容

监听目录递归扫描时，以下内容会被跳过，避免把依赖和临时文件索引进来：

- 隐藏文件 / 目录（以 `.` 开头）；
- 常见构建产物与依赖目录：`node_modules`、`dist`、`build`、`out`、`__pycache__`、`.venv`、`venv`、`target`、`vendor` 等；
- 锁文件与临时文件：`package-lock.json`、`yarn.lock`、`Cargo.lock`、`.DS_Store`、`*.tmp`、`*.swp` 等；
- 各层 `.gitignore` 中声明忽略的内容。

---

## 界面预览

| 文档库 | 问答 | 文档预览 |
|---|---|---|
| ![文档库](docs/screenshots/docs.png) | ![问答](docs/screenshots/ask.png) | ![预览](docs/screenshots/preview.png) |

| 解析队列 | 测验 | 手机配对 |
|---|---|---|
| ![解析中](docs/screenshots/parsing.png) | ![测验](docs/screenshots/quiz.png) | ![设置](docs/screenshots/settings.png) |

---

## 快速开始

### 方式一：桌面版（推荐给非开发用户）

桌面版无需安装 Python 或 Docker，具体支持平台、构建方法与数据目录见 [docs/DESKTOP.md](docs/DESKTOP.md)。

- macOS：本地执行 `bash scripts/build_app.sh` 构建 `.app` 与 DMG；
- Windows：推送 `v*` 标签后由 GitHub Actions 自动构建，从 Release 下载 `personal-library-win64.zip`。

首次启动会进入配置向导：选择模型服务商（智谱 / SiliconFlow / 自定义 OpenAI 兼容接口），粘贴 API Key 即可使用。

### 方式二：服务器模式（源码运行）

#### 环境要求

- Python 3.11+
- Docker（用于启动 Qdrant 向量库；也可以设置 `QDRANT_EMBEDDED=true` 使用内嵌模式，免 Docker）

#### 安装与启动

```bash
git clone https://github.com/LQ-1123/memo-book.git
cd memo-book

python3 -m venv .venv
.venv/bin/pip install -e .

# 启动 Qdrant（应用本身跑在宿主机，因为需要监听真实文件系统）
docker compose up -d

# 创建并编辑配置
cp .env.example .env
# 必填：WATCH_DIRS、EMBED_API_KEY、LLM_API_KEY；API_KEYS 用于局域网访问认证
# 默认地址与端口见 .env.example

# 启动服务
.venv/bin/python -m app.main
```

启动后打开 <http://127.0.0.1:8787/> 即可访问 Web 界面。

macOS 如需常驻后台，可执行 `scripts/maintain.py install` 将其注册为 launchd 服务（登录自启、崩溃自动拉起、每日自动备份）。

#### 首次使用

1. 在「设置 → 模型服务」中填入 embedding / LLM 的 API Key（保存即热生效）；
2. 将本地文档放入 `WATCH_DIRS` 指定的目录，或在页面中粘贴链接 / 上传文件；
3. 等待解析完成后，在问答页用自然语言向自己的知识库提问。

---

## 配置

配置可通过仓库根目录 `.env`、数据目录 `.env` 或网页「设置」页修改；网页保存后立即热生效。常用配置项：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `API_KEYS` | 访问口令，逗号分隔，支持多设备/调用方 | 空（服务器形态建议必填） |
| `WATCH_DIRS` | 监听目录，逗号分隔，递归扫描 | 空 |
| `EMBED_API_KEY` | Embedding 服务 API Key | 空 |
| `EMBED_MODEL` / `EMBED_DIM` | Embedding 模型 / 向量维度 | `embedding-3` / `2048` |
| `LLM_API_KEY` | 问答模型 API Key | 空 |
| `LLM_MODEL` | 问答模型名称 | `glm-4.6` |
| `RERANK_API_KEY` / `RERANK_MODEL` | 可选 Rerank 服务 | 空 / `BAAI/bge-reranker-v2-m3` |
| `QDRANT_URL` | Qdrant 服务地址 | `http://127.0.0.1:6333` |
| `QDRANT_EMBEDDED` | 是否使用内嵌向量库（桌面版默认开启） | `false` |
| `APP_HOST` / `APP_PORT` | 服务监听地址 / 端口 | `0.0.0.0` / `8787` |
| `OCR_ENABLED` | 是否启用扫描件 OCR | `true` |
| `INGEST_WORKERS` | 并发入库线程数 | `4` |
| `INGEST_ALLOW_PRIVATE_URLS` | 是否允许抓取内网/回环 URL（SSRF 防护） | `false` |
| `BILIBILI_SESSDATA` | B 站登录态 Cookie（可选，用于获取 AI 字幕） | 空 |

模型服务商默认使用智谱开放平台，但配置层兼容 OpenAI 接口格式；如需接入其他厂商，可在 `.env` 中覆盖 `EMBED_BASE_URL`、`LLM_BASE_URL` 等字段。完整配置项见 `.env.example` 与 `app/config.py`。

---

## HTTP API

API 统一前缀为 `/api/v1`，除本机回环访问外，接口需要通过 `X-API-Key` 头携带 `API_KEYS` 中的任意一个口令。常用接口如下：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查、依赖状态与索引统计 |
| GET | `/search?q=关键词&topk=10` | 混合检索，返回片段与出处 |
| POST | `/ask` | 问答；body 支持 `{"question":"...","stream":true,"history":[...]}`，`stream` 为 SSE |
| GET / POST / DELETE | `/threads` | 多轮会话持久化 |
| GET / DELETE | `/documents`、`/documents/{id}` | 文档列表 / 详情 / 删除 |
| GET | `/documents/{id}/file`、`/documents/{id}/pages` | 获取原始文件、PDF 页面预览 |
| POST | `/documents/{id}/reindex` | 重建某篇文档的索引 |
| POST | `/ingest/url`、`/ingest/video`、`/ingest/upload` | 链接 / 视频 / 文件入库 |
| POST | `/ingest/reconcile` | 手动全量对账 |
| GET | `/tasks`、`/tasks/{id}` | 入库任务状态 |
| GET / PUT | `/config` | 运行时配置读取与热更新 |
| GET | `/pair/url` | 局域网扫码配对地址 |
| POST / GET | `/quiz` | 生成测验 / 获取测验题目 |

SSE 事件序列为：`sources` → `delta`* → `done`。完整接口列表见 `app/api/` 目录。

---

## 工作原理

```
监听目录 / URL / 视频 / 上传
        │
        ▼
   解析与分块 ──► SQLite（文档注册表 + 原文 + FTS5 索引）
        │
        ▼
   Embedding ──► Qdrant（稠密向量）
        │
        ▼
  BM25 + 向量双路召回 ──► RRF 融合 ──►（可选）Rerank
        │
        ▼
   LLM 生成带引用编号的回答（SSE / JSON）
```

架构要点：

- **SQLite 是事实源**：文档注册表、分块原文、FTS 索引、任务状态都保存在 SQLite；Qdrant 只保存稠密向量及最小 payload，可随时从 SQLite 全量重建；
- **统一入库通道**：本地文件、网页正文（剪藏为 Markdown）、视频笔记、上传文件最终都进入同一条解析入库管线，便于去重、失败重试与状态追踪；
- **应用与向量库分离部署**：监听目录必须位于宿主机真实文件系统，因此 `docker-compose.yml` 仅包含 Qdrant，应用直接运行在宿主机；
- **桌面版与服务器版同构**：桌面版通过内嵌 Qdrant 与静态 ffmpeg 实现免环境运行，功能与 API 保持一致。

---

## 项目结构

```text
app/
  api/          # HTTP 路由：documents / search / ask / ingest / quiz / threads ...
  core/         # 基础设施：SQLite、Embedding、LLM、OCR、Rerank、Qdrant、VLM
  ingest/       # 入库链路：watcher、pipeline、chunking、parsers、URL/video 抓取
  static/       # 前端（原生 JS PWA）
docs/           # 设计、需求、桌面版说明、截图
scripts/        # 构建脚本、维护脚本、冒烟测试
tests/          # 离线单元测试（全 mock，不依赖网络 / Qdrant）
```

---

## 开发与测试

```bash
# 安装开发依赖
.venv/bin/pip install -e ".[dev]"

# 运行测试（200+ 离线单测，全 mock）
.venv/bin/python -m pytest tests/ -q

# 桌面壳开发模式
.venv/bin/python run_desktop.py
```

---

## 容量与性能边界

Memo Book 的定位是 **单机 / 局域网个人知识库**，不是多租户或企业级在线服务。代码中没有写入“最多多少篇文档”的硬性数量上限，但可以根据设计目标与工程实现给出参考边界：

| 维度 | 参考边界 | 说明 |
|---|---|---|
| 文档规模 | 上万篇文档、多 GB 原始数据 | 需求文档中的设计目标 |
| 索引片段 | 数十万 chunk 可流畅检索 | SQLite FTS5 + Qdrant 单机部署；十万级 chunk 在设计中视为无压力 |
| Web / API 单文件上传 | ≤ 50 MB | 超过 50 MB 的文件请直接放入监听目录，不经过上传接口 |
| 监听目录单文件 | 无内置大小上限 | 但解析时会整文件读取 / 转换，超大文件会占用内存并增加耗时 |
| B 站无字幕视频转写 | ≤ 15 分钟 / 条 | 超过上限且无字幕时无法语音转写入库 |
| 并发入库 | 默认 4 线程 | 瓶颈通常是 embedding API 的网络等待；PDF 解析与本地向量写入内部会串行化 |
| 检索元数据过滤 | 超过 1000 个文档 id 时自动降级为结果后过滤 | 对应检索器内部 `_FILTER_CAP` 边界 |
| 文件事件突发 | 单批事件队列约 10000 条 | 队列溢出时会丢弃事件，但周期性全量对账会兜底补扫 |

实际容量与速度还取决于：

- 磁盘剩余空间、Qdrant 存储位置和宿主机内存；
- embedding / LLM 服务商的并发与限流策略；
- 单篇文件的页数或大小（扫描 PDF 的 OCR / 视觉转写明显更慢）；
- 是否开启 rerank（每次检索会额外调用一次外部服务，延迟会上升）。

> 建议：首批导入时先放少量目录验证格式与耗时，再逐步扩大到完整资料集；知识库达到数十万 chunk 级别后，建议开启 rerank 并定期备份 SQLite 与向量数据。

---

## 已知限制

- 旧版 `.doc`（Word 97-2003）不支持，请转存为 `.docx`；
- 快手视频不支持（yt-dlp 已移除对应 extractor），会给出明确错误提示；
- 无字幕 B 站视频使用付费语音转写（默认智谱 GLM-ASR），单条上限约 15 分钟；
- iOS Safari 不支持 PWA 分享入库（Android Chrome 可用）；
- 未签名桌面安装包在 macOS / Windows 首次打开时会出现系统安全提示；
- 对非常大的知识库，建议配置 Rerank 以提升相似内容较多时的排序效果。

---

## 故障排查

- **入库失败 / Connection refused**：检查 Qdrant 是否在运行，例如 `docker compose ps`；
- **问答 503 / 提示未配置 Key**：到「设置 → 模型服务」填写 API Key，保存后立即生效；
- **文件始终未出现在文档库**：查看「文档」页顶部的「解析中」区域，不支持的类型会被静默跳过；
- **页面样式停留在旧版**：Service Worker 缓存导致，强制刷新一次即可；
- **服务疑似卡死**：执行 `kill -USR1 <pid>`，线程栈会转储到 `data/logs/stack_dump.log`。

---

## 许可证

MIT

---

*Memo Book 当前仍处于个人项目阶段，欢迎提交 Issue 与 PR。*
