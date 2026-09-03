# Memo Book

<p align="center">
  <img src="docs/screenshots/hero.png" alt="personal-library 首页">
</p>

[中文](README.md) | [English](README.en.md)

一个跑在本机的 RAG 知识库。把文档、代码、网页、B 站视频丢进监听目录，它自动解析入库；之后用自然语言提问，回答附带原文引用，点引用能跳回原句。

所有资料、索引、对话记录都存在本机。出网的只有模型调用：embedding、LLM，可选的 rerank 和图像理解。

## 解决什么问题

资料散落各处：PDF 书、项目源码、剪藏的网页、B 站收藏。找东西靠翻文件夹和文件名搜索，想"对着这些资料提问"更是没有现成工具。

云知识库方案要求把数据传到别人的服务器，模型也不能换。personal-library 反过来：**数据全部留在本机**，模型服务商随便换（任何 OpenAI 兼容接口都行），回答里的每句话都能点回你自己的原文。

## 入库：把整个文件夹扔进去就行

监听目录递归扫描子目录，`node_modules`、`dist`、`__pycache__`、锁文件这些构建产物自动跳过，项目里的 `.gitignore` 规则也生效——所以整个项目文件夹直接扔进去，进来的都是干净的源码和文档。

![文档树](docs/screenshots/docs.png)

支持的类型：md、PDF（扫描页自动 OCR）、docx / pptx / xlsx / xls、html、23 种代码扩展名、txt 等纯文本、图片（VLM 描述）。

入库时文档树顶部有实时的「解析中」队列，每篇文件带线圈球动画和状态，解析完成自动归位到目录树：

![解析中](docs/screenshots/parsing.png)

还有两种入库方式：网页里粘贴链接（普通网页 / CSDN / 公众号正文提取；B 站视频拉字幕生成带时间戳的笔记，无字幕自动语音转写），或直接拖文件上传。

## 提问：回答里的每句话都能点回原文

关键词（BM25）和向量两路召回，RRF 融合，可选 SiliconFlow 重排。回答流式输出，结论标注 `[n]` 引用角标，右栏按文档聚合展示来源，点击定位到原文片段。

![问答](docs/screenshots/ask.png)

支持多轮追问（理解指代）；问「知识库里有哪些文档」「最近入库了什么」这类问题走元问题路由，直接查库秒答，不浪费一次模型调用。

## 文档管理

侧栏按监听目录的真实层级展示多级可折叠目录树；每篇文档入库后自动生成 AI 摘要和 3 个关键问题；PDF 按页预览，Office / 代码 / 网页显示转换后的 Markdown。

![预览](docs/screenshots/preview.png)

## 测验

指定主题从知识库选材出题：单选 / 判断 / 简答，即时判分，简答题由 LLM 评分给评语，每题带原文出处，支持错题回顾和最佳成绩记录。

![测验](docs/screenshots/quiz.png)

## 手机访问：扫一个二维码

电脑端「设置 → 手机访问」出二维码，手机相机扫码即打开同一个知识库——口令藏在二维码链接里自动写入手机，之后永久免输入。

![手机访问](docs/screenshots/settings.png)

## 桌面版：双击就用

macOS 和 Windows 安装包内嵌向量库和静态 ffmpeg，目标机器不需要 Python、Docker、ffmpeg。首次启动弹出配置向导：选模型服务商（智谱 / 硅基流动 / 自定义 OpenAI 兼容），粘贴 API Key 即可。

- macOS：`bash scripts/build_app.sh` 本地构建 .app 和 DMG
- Windows：推送 tag 后 GitHub Actions 自动构建，到 Release 下载 `personal-library-win64.zip`

详见 [docs/DESKTOP.md](docs/DESKTOP.md)。

## 快速开始（服务器形态）

需要 Python 3.11+ 和 Docker。

```bash
git clone https://github.com/LQ-1123/memo-book.git
cd personal-library

python3 -m venv .venv
.venv/bin/pip install -e .

# 启动 Qdrant（向量库）
docker run -d --name library-qdrant -p 6333:6333 \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant:latest

# 配置
cp .env.example .env
# 编辑 .env：填 WATCH_DIRS（要监听的目录）、EMBED_API_KEY、LLM_API_KEY（默认智谱）

# 启动
.venv/bin/python -m app.main
```

打开 `http://127.0.0.1:8790/`。macOS 上用 `scripts/maintain.py install` 可以装成 launchd 常驻服务（登录自启、崩溃自动拉起、每日自动备份）。

## 配置

`.env` 或数据目录 `.env`（网页「设置」里也能改，保存即热生效）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `WATCH_DIRS` | 监听目录，逗号分隔，递归扫描 | — |
| `EMBED_API_KEY` / `LLM_API_KEY` | 嵌入 / 问答模型 key（智谱） | — |
| `EMBED_MODEL` / `LLM_MODEL` | 模型名 | embedding-3 / glm-4.6 |
| `EMBED_DIM` | 向量维度 | 2048 |
| `API_KEYS` | 访问口令（本机免填；桌面版自动生成） | — |
| `APP_HOST` / `APP_PORT` | 监听地址 / 端口 | 0.0.0.0 / 8790 |
| `QDRANT_EMBEDDED` | 桌面版内嵌向量库 | 桌面 true / 服务器 false |
| `INGEST_WORKERS` | 并发入库线程数 | 4 |

完整字段见 `.env.example` 与 `app/config.py`。

## API

前缀 `/api/v1`，除 `GET /health` 外需要 `X-API-Key` 头。常用接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 依赖状态与索引统计 |
| GET | `/search?q=关键词&topk=10` | 混合检索，返回片段与出处 |
| POST | `/ask` | `{"question":"...","stream":true,"history":[...]}`，stream 为 SSE |
| GET / POST / DELETE | `/threads` | 对话持久化 |
| GET / DELETE | `/documents`、`/documents/{id}` | 文档列表 / 详情 / 删除 |
| POST | `/documents/{id}/reindex` | 强制重建索引 |
| POST | `/ingest/url`、`/ingest/video`、`/ingest/upload` | 链接 / 视频 / 文件入库 |
| POST | `/ingest/reconcile` | 手动全量对账 |
| GET / PUT | `/config` | 运行时配置热更新 |
| GET | `/pair/url` | 局域网扫码配对链接 |

SSE 事件序列：`sources` → `delta`* → `done`。完整列表见 `app/api/`。

## 已知限制

- 旧版 `.doc`（Word 97-2003）不支持，转存 .docx 即可
- 快手视频不支持（yt-dlp 已移除 extractor），会给出明确提示
- B 站无字幕视频的语音转写按量计费（智谱 GLM-ASR），单条上限 15 分钟
- iOS Safari 不支持 PWA 分享入库（安卓 Chrome 可用）
- 未签名的桌面安装包：macOS 首次打开需右键 → 打开，Windows SmartScreen 会提示
- 大库（数十万片段）查询速度无压力，但相似内容多了建议配 rerank 提升排序

## 故障排查

- **索引失败 / Connection refused**：Qdrant 没在跑。检查 Docker Desktop 是否启动、`docker ps` 里有没有 qdrant 容器
- **问答 503 / 提示未配置 key**：在「设置 → 模型服务」里填，保存即生效
- **入库的文件没出现**：看「文档」页顶部的「解析中」区；不支持的类型会被静默跳过
- **页面样式旧**：Service Worker 缓存，刷新一次即新版
- **服务卡死**：`kill -USR1 <服务pid>`，线程栈转储在 `data/logs/stack_dump.log`

## 测试

```bash
.venv/bin/python -m pytest tests/ -q     # 200+ 离线单测（全 mock，不触网）
```

## 技术栈

FastAPI、SQLite（FTS5）、Qdrant、jieba、PyMuPDF / markitdown（解析）、RapidOCR、智谱 GLM（OpenAI 兼容）、pywebview（桌面壳）、原生 JS 前端（零框架）。

## License

MIT
