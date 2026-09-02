"""应用入口：组装依赖、生命周期管理、路由挂载、静态前端。

运行：.venv/bin/python -m app.main   （读取 .env 配置）
"""
from __future__ import annotations

import concurrent.futures
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import config_routes, fs as fs_routes, health, kb, pair, qa, quiz as quiz_routes, qrlogin, threads as threads_routes
from .config import Settings, get_settings
from .core.asr import AsrClient
from .core.db import Database
from .core.embeddings import EmbeddingClient
from .core.llm import LLMClient
from .core.ocr import OcrEngine
from .core.qdrant_store import VectorStore
from .core.rerank import RerankClient
from .core.runtime_config import RuntimeConfig
from .core.vlm import VisionClient
from .ingest.pipeline import IngestPipeline
from .ingest.retriever import Retriever
from .ingest.watcher import WatchManager
from .security import require_api_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        cfg = RuntimeConfig(settings)
        store = VectorStore(settings, cfg)
        embedder = EmbeddingClient(cfg)
        llm = LLMClient(cfg)
        asr = AsrClient(cfg)
        reranker = RerankClient(cfg)
        ocr = OcrEngine(settings.ocr_enabled)
        vlm = VisionClient(cfg)
        pipeline = IngestPipeline(db, store, embedder, ocr, settings, vlm, cfg)
        retriever = Retriever(db, store, embedder, reranker, settings)
        watcher = WatchManager(settings, pipeline, cfg)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="task"
        )

        # 入库即消化：索引完成后后台生成「摘要 + 关键问题」，失败只记日志
        def _digest_job(doc_id: str) -> None:
            from .ingest.digest import digest_document

            try:
                digest_document(llm, db, doc_id)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("摘要任务失败 doc=%s", doc_id)

        pipeline.on_indexed = lambda doc_id: executor.submit(_digest_job, doc_id)

        try:
            store.ensure_collection()  # 含维度不一致自动重建
        except Exception:
            logging.getLogger(__name__).exception(
                "Qdrant 初始化失败（向量检索降级，关键词检索不受影响）"
            )
        if not embedder.available:
            logging.getLogger(__name__).warning(
                "未配置嵌入 key：可在网页端 http://127.0.0.1:%d/ 配置，立即生效",
                settings.app_port,
            )
        watcher.start()
        stuck = db.fail_stuck_tasks()
        if stuck:
            logging.getLogger(__name__).warning("服务重启，已将 %d 个中断任务标记为失败", stuck)

        app.state.settings = settings
        app.state.cfg = cfg
        app.state.db = db
        app.state.store = store
        app.state.embedder = embedder
        app.state.llm = llm
        app.state.asr = asr
        app.state.reranker = reranker
        app.state.ocr = ocr
        app.state.pipeline = pipeline
        app.state.retriever = retriever
        app.state.watcher = watcher
        app.state.executor = executor
        yield
        watcher.stop()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="personal-library", version="0.2.0", lifespan=lifespan)

    def _bound_settings() -> Settings:
        return settings

    # 让 Depends(get_settings) 的消费方（如认证）拿到本实例的配置
    app.dependency_overrides[get_settings] = _bound_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 局域网个人服务；配合 API Key 使用
        allow_methods=["*"],
        allow_headers=["*"],
    )
    protected = [Depends(require_api_key)]
    # health 也保护（回环免认证不受影响）：绑 0.0.0.0 后不向局域网裸露文档数/监听目录等元信息
    app.include_router(health.router, prefix="/api/v1", dependencies=protected)
    app.include_router(config_routes.router, prefix="/api/v1", dependencies=protected)
    app.include_router(kb.router, prefix="/api/v1", dependencies=protected)
    app.include_router(threads_routes.router, prefix="/api/v1", dependencies=protected)
    app.include_router(quiz_routes.router, prefix="/api/v1", dependencies=protected)
    app.include_router(qa.router, prefix="/api/v1", dependencies=protected)
    app.include_router(qrlogin.router, prefix="/api/v1", dependencies=protected)
    app.include_router(pair.router, prefix="/api/v1", dependencies=protected)
    app.include_router(fs_routes.router, prefix="/api/v1", dependencies=protected)

    # PWA 关键文件：显式路由保证 MIME 正确且不缓存（sw 更新即时生效）
    if STATIC_DIR.exists():

        def _sw():
            return FileResponse(
                STATIC_DIR / "sw.js",
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        def _manifest():
            return FileResponse(
                STATIC_DIR / "manifest.webmanifest",
                media_type="application/manifest+json",
                headers={"Cache-Control": "no-cache"},
            )

        app.get("/sw.js", include_in_schema=False)(_sw)
        app.get("/manifest.webmanifest", include_in_schema=False)(_manifest)
        # 壳文件禁止启发式缓存：SW 的 addAll 会尊重 HTTP 缓存，
        # 若 StaticFiles 不发 no-cache，新版本 SW 安装时会把旧 JS/PNG 一并装进新缓存
        @app.middleware("http")
        async def no_cache_shell(request: Request, call_next):
            response = await call_next(request)
            p = request.url.path
            if p == "/" or p.startswith(("/js/", "/css/")) or p in ("/sw.js", "/manifest.webmanifest"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def main() -> None:
    import faulthandler
    import signal
    import uvicorn

    # 排障：kill -USR1 <pid> 把全部线程栈转储到 data/logs/stack_dump.log（卡死类问题一击定位）
    settings_early = get_settings()
    try:
        _dump = open(settings_early.data_dir / "stack_dump.log", "a", encoding="utf-8")
        faulthandler.register(signal.SIGUSR1, file=_dump, all_threads=True)
    except Exception:  # noqa: BLE001
        faulthandler.register(signal.SIGUSR1, all_threads=True)

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.app_host,
        port=settings.app_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
