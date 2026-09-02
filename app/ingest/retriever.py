"""混合检索：FTS5 BM25 + Qdrant 稠密 → RRF 融合 →（可选）rerank 重排。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings
from ..core.db import Database
from ..core.embeddings import EmbeddingClient
from ..core.qdrant_store import VectorStore
from ..core.rerank import RerankClient
from .pipeline import fts_query, fts_tokenize

log = logging.getLogger(__name__)

_RRF_K = 60
_FILTER_CAP = 1000  # 超过则放弃向量侧前置过滤，改为结果后过滤


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    doc_id: str
    text: str
    heading: str
    page: int | None
    score: float
    title: str
    path: str
    url: str | None
    doc_type: str
    source: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "heading": self.heading,
            "page": self.page,
            "text": self.text,
            "document": {
                "id": self.doc_id,
                "title": self.title,
                "path": self.path,
                "url": self.url,
                "type": self.doc_type,
                "source": self.source,
            },
        }


def rrf_fuse(rankings: list[list[str]], k: int = _RRF_K) -> list[str]:
    """多路排名融合：score = Σ 1/(k+rank)。返回按融合分降序的去重 chunk_id 列表。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda x: -x[1])]


class Retriever:
    def __init__(
        self,
        db: Database,
        store: VectorStore,
        embedder: EmbeddingClient,
        reranker: RerankClient,
        settings: Settings,
    ) -> None:
        self.db = db
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.settings = settings

    def _resolve_filter(self, doc_type: str | None, source: str | None) -> list[str] | None:
        if not doc_type and not source:
            return None
        rows, _ = self.db.list_documents(doc_type=doc_type, source=source, limit=1_000_000)
        return [r["id"] for r in rows]

    def search(
        self,
        query: str,
        topk: int | None = None,
        doc_type: str | None = None,
        source: str | None = None,
    ) -> list[SearchHit]:
        topk = topk or self.settings.search_topk
        candidates = self.settings.search_candidates
        doc_ids = self._resolve_filter(doc_type, source)
        if doc_ids is not None and not doc_ids:
            return []

        # 1) 关键词路（BM25；分数小=相关，转成排名）
        fts_rows = self.db.fts_search(fts_query(query), doc_ids, candidates)
        fts_ranking = [cid for cid, _score in fts_rows]

        # 2) 语义路
        dense_ranking: list[str] = []
        dense_scores: dict[str, float] = {}
        if self.embedder.available:
            try:
                vec = self.embedder.embed([query])[0]
                filter_ids = doc_ids if (doc_ids is not None and len(doc_ids) <= _FILTER_CAP) else None
                for cid, score in self.store.search(vec, candidates, filter_ids):
                    dense_ranking.append(cid)
                    dense_scores[cid] = score
            except Exception as e:
                log.warning("向量检索失败，仅用关键词路: %s | 原因: %r", e, e.__cause__)

        fused = rrf_fuse([fts_ranking, dense_ranking])
        if doc_ids is not None and len(doc_ids) > _FILTER_CAP:
            allow = set(doc_ids)
            fused = [c for c in fused if c.split(":", 1)[0] in allow]
        if not fused:
            return []

        # 3) rerank（可选）：取融合前 30 重排
        pool = fused[: max(topk * 3, 10)]
        rows = self.db.get_chunks(pool)
        if self.reranker.available and rows:
            order = self.reranker.rerank(query, [r.text for r in rows])
            if order:
                by_idx = {i: r for i, r in enumerate(rows)}
                rows = [by_idx[i] for i, _s in order if i in by_idx]
        rows = rows[:topk]

        hits: list[SearchHit] = []
        doc_cache: dict = {}
        for r in rows:
            if r.doc_id not in doc_cache:
                d = self.db.get_document(r.doc_id)
                doc_cache[r.doc_id] = d
            d = doc_cache[r.doc_id]
            if d is None:
                continue
            score = dense_scores.get(r.chunk_id, 0.0)
            hits.append(
                SearchHit(
                    chunk_id=r.chunk_id, doc_id=r.doc_id, text=r.text,
                    heading=r.heading, page=r.page, score=score,
                    title=d["title"], path=d["path"], url=d["url"],
                    doc_type=d["doc_type"], source=d["source"],
                )
            )
        return hits

    def context_block(self, hits: list[SearchHit]) -> str:
        """把检索结果编为带引用编号的上下文块。"""
        parts: list[str] = []
        for i, h in enumerate(hits, 1):
            loc = h.title
            if h.heading:
                loc += f" · {h.heading}"
            if h.page:
                loc += f" · 第{h.page}页"
            parts.append(f"[{i}] {loc}\n{h.text}")
        return "\n\n".join(parts)
