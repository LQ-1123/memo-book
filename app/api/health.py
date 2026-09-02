"""健康检查（免认证）：存活、依赖状态、索引统计。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.watchdirs import effective_watch_dirs

router = APIRouter()


@router.get("/health")
def health(request: Request):
    state = request.app.state
    qdrant_ok = state.store.healthy() if hasattr(state, "store") else False
    doc_count = 0
    chunk_count = 0
    if hasattr(state, "db"):
        doc_count = state.db.list_documents(limit=1)[1]
        chunk_count = state.db.count_chunks()
    return {
        "status": "ok",
        "qdrant": qdrant_ok,
        "embed_configured": state.embedder.available if hasattr(state, "embedder") else False,
        "llm_configured": state.llm.available if hasattr(state, "llm") else False,
        "rerank_configured": state.reranker.available if hasattr(state, "reranker") else False,
        "ocr_available": state.ocr.available if hasattr(state, "ocr") else False,
        "watching": state.watcher.running if hasattr(state, "watcher") else False,
        "watch_dirs": [str(d) for d in effective_watch_dirs(getattr(state, "cfg", None), state.settings)],
        "documents": doc_count,
        "chunks": chunk_count,
    }
