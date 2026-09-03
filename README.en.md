# personal-library

<p align="center">
  <img src="docs/screenshots/hero.png" alt="personal-library home">
</p>

[中文](README.md) | [English](README.en.md)

A self-hosted RAG knowledge base. Drop documents, code, web pages, or Bilibili videos into a watched folder — they get parsed and indexed automatically. Then ask questions in plain language; every answer cites the source, and each citation links back to the exact text.

Everything lives on your machine: documents, SQLite index, vectors, chat history. The only network calls are to model providers — embeddings, LLM, and optionally rerank and vision.

## Why

Your material is scattered: PDFs, project source code, saved articles, Bilibili favorites. Finding things means digging through folders and guessing filenames. Cloud knowledge bases fix that by uploading your data to someone else's server and locking you into their model.

personal-library flips it: **all data stays local**, and the model provider is swappable — any OpenAI-compatible endpoint works. Every claim in an answer points back to your own text.

## Ingestion: drop the whole folder in

The watch folder is scanned recursively; `node_modules`, `dist`, `__pycache__` and lock files are skipped automatically, and layered `.gitignore` rules are honored — so you can drop an entire project folder in and only clean source and docs get indexed.

![Documents](docs/screenshots/docs.png)

Supported types: md, PDF (scanned pages via OCR), docx / pptx / xlsx / xls, html, 23 code extensions, plain text, images (VLM captions).

While parsing, a live queue at the top of the Documents view shows every file with its own coil-orb animation and status; finished files fold into the directory tree:

![Parsing queue](docs/screenshots/parsing.png)

Two more ways in: paste a URL (articles from CSDN / WeChat; Bilibili videos become timestamped notes, subtitle-less videos fall back to speech-to-text), or drag files onto the page.

## Q&A: every sentence links back to your text

Keyword (BM25) and vector recall merged with RRF, optional rerank. Answers stream with `[n]` citations; the right panel groups them per document and each one locates the source text.

![Ask](docs/screenshots/ask.png)

Multi-turn follow-ups understand pronouns. Meta questions like "what documents do I have" route to a direct database lookup instead of burning a model call.

## Document management

The sidebar mirrors the watch folder as a collapsible multi-level tree; every indexed document gets an AI summary and 3 key questions; PDFs preview page-by-page, Office/code/web pages show converted Markdown.

![Preview](docs/screenshots/preview.png)

## Quizzes

Generate quizzes from your library by topic: single choice, true/false, short answer. Instant grading; short answers scored by the LLM with feedback; every question cites its source. Wrong-answer review and best-score tracking included.

![Quiz](docs/screenshots/quiz.png)

## Phone: scan one QR code

Open "Settings → 手机访问" on the host and a QR code appears — scanning it on your phone opens the same library with the token embedded. No passwords to type.

![Pairing](docs/screenshots/settings.png)

## Desktop builds: unzip and run

macOS and Windows packages embed the vector store and static ffmpeg — no Python, Docker, or ffmpeg on the target machine. First launch opens a setup wizard: pick a provider (Zhipu / SiliconFlow / custom OpenAI-compatible), paste an API key, done.

- macOS: `bash scripts/build_app.sh` builds the .app and DMG locally
- Windows: GitHub Actions builds `personal-library-win64.zip` on every tag — grab it from Releases

Data directory: `~/Library/Application Support/personal-library/` on macOS, `%APPDATA%\personal-library` on Windows. See [docs/DESKTOP.md](docs/DESKTOP.md).

## Getting started (server mode)

Requires Python 3.11+ and Docker.

```bash
git clone https://github.com/LQ-1123/personal-library.git
cd personal-library

python3 -m venv .venv
.venv/bin/pip install -e .

# Start Qdrant
docker run -d --name library-qdrant -p 6333:6333 \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant:latest

# Configure
cp .env.example .env
# edit .env: WATCH_DIRS, EMBED_API_KEY, LLM_API_KEY (Zhipu by default)

# Run
.venv/bin/python -m app.main
```

Open `http://127.0.0.1:8790/`. On macOS, `scripts/maintain.py install` turns it into a launchd service (autostart, crash restart, daily backups).

## Configuration

Via `.env` or the data-directory `.env`; the web Settings page hot-reloads everything:

| Variable | Description | Default |
|---|---|---|
| `WATCH_DIRS` | Watched folders (comma-separated, recursive) | — |
| `EMBED_API_KEY` / `LLM_API_KEY` | Embedding / LLM keys (Zhipu) | — |
| `EMBED_MODEL` / `LLM_MODEL` | Model names | embedding-3 / glm-4.6 |
| `EMBED_DIM` | Vector dimension | 2048 |
| `API_KEYS` | Access tokens (localhost exempt; auto-generated for desktop) | — |
| `APP_HOST` / `APP_PORT` | Bind address / port | 0.0.0.0 / 8790 |
| `QDRANT_EMBEDDED` | Embedded vector store | desktop true / server false |
| `INGEST_WORKERS` | Ingestion threads | 4 |

See `.env.example` and `app/config.py` for the full list.

## API

Prefix `/api/v1`; everything except `GET /health` needs an `X-API-Key` header. Common ones:

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Dependency status and index stats |
| GET | `/search?q=...&topk=10` | Hybrid search with sources |
| POST | `/ask` | `{"question":"...","stream":true,"history":[...]}`, SSE streaming |
| GET / POST / DELETE | `/threads` | Chat persistence |
| GET / DELETE | `/documents`, `/documents/{id}` | List / detail / delete |
| POST | `/documents/{id}/reindex` | Force re-index |
| POST | `/ingest/url`, `/ingest/video`, `/ingest/upload` | Link / video / file ingestion |
| POST | `/ingest/reconcile` | Manual reconciliation |
| GET / PUT | `/config` | Runtime config, hot reload |
| GET | `/pair/url` | LAN QR pairing link |

SSE event sequence: `sources` → `delta`* → `done`. Full list under `app/api/`.

## Known limitations

- Legacy `.doc` (Word 97-2003) is not supported — save as .docx
- Kuaishou videos are not supported (yt-dlp dropped the extractor); you get a clear error
- Subtitle-less Bilibili videos use paid speech-to-text (Zhipu GLM-ASR), 15-minute cap per video
- iOS Safari does not support PWA share-target ingestion (Android Chrome works)
- Unsigned desktop builds: macOS needs right-click → Open on first launch; Windows SmartScreen will warn
- Very large libraries benefit from rerank; raw retrieval speed stays flat

## Troubleshooting

- **Indexing fails / Connection refused**: Qdrant is not running. Check Docker Desktop and the qdrant container
- **Q&A returns 503**: no LLM key configured — set it in Settings, takes effect immediately
- **A file never shows up**: check the "解析中" (parsing) section at the top of Documents; unsupported types are skipped silently
- **Stale UI**: Service Worker cache — one refresh picks up the new version
- **Hangs**: `kill -USR1 <pid>` dumps all thread stacks to `data/logs/stack_dump.log`

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 200+ offline unit tests, fully mocked
```

## Stack

FastAPI, SQLite (FTS5), Qdrant, jieba, PyMuPDF/markitdown, RapidOCR, Zhipu GLM (OpenAI-compatible), pywebview, vanilla JS frontend.

## License

MIT
