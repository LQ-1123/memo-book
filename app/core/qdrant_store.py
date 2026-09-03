"""Qdrant 稠密向量存取。SQLite 是事实源，此处仅存向量 + 最小 payload，可随时重建。"""
from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient, models

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "chunk:" + chunk_id))


class VectorStore:
    def __init__(self, settings: Settings, cfg: RuntimeConfig) -> None:
        self.collection = settings.qdrant_collection
        self._cfg = cfg
        if settings.qdrant_embedded:
            # 内嵌本地模式（桌面版）：向量持久化到 data/qdrant/，无需独立服务
            path = settings.data_dir / "qdrant"
            path.mkdir(parents=True, exist_ok=True)
            log.info("Qdrant 内嵌模式，存储目录: %s", path)
            self._client = QdrantClient(path=str(path), check_compatibility=False)
        else:
            # prefer_grpc：gRPC（6334）不读 macOS 系统代理，绕开 httpx 被
            # 系统级代理劫持 127.0.0.1 请求导致 502/超时的问题；体积也更小
            self._client = QdrantClient(
                url=settings.qdrant_url, timeout=10, check_compatibility=False,
                prefer_grpc=True,
            )

    @property
    def dim(self) -> int:
        return int(self._cfg.get("embed_dim"))

    def healthy(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception as e:
            log.warning("Qdrant 不可达: %s", e)
            return False

    def scroll_all(self) -> dict[str, list[list[float]]]:
        """拉取集合全部 {doc_id: [向量, …]}，供图谱相似度计算。"""
        per_doc: dict[str, list[list[float]]] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for p in points:
                doc_id = (p.payload or {}).get("doc_id")
                vec = p.vector
                if isinstance(vec, dict):  # 命名向量布局
                    vec = list(vec.values())[0] if vec else None
                if doc_id and vec:
                    per_doc.setdefault(doc_id, []).append(list(vec))
            if offset is None:
                break
        return per_doc

    def ensure_collection(self) -> None:
        """确保集合存在且维度与当前配置一致；不一致自动重建（旧向量作废，需重索引）。"""
        dim = self.dim
        if self._client.collection_exists(self.collection):
            info = self._client.get_collection(self.collection)
            existing = info.config.params.vectors.size
            if existing == dim:
                return
            log.warning("向量维度不一致（集合=%s 配置=%s），重建集合", existing, dim)
            self._client.delete_collection(self.collection)
        self._client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        log.info("已创建 Qdrant 集合 %s（dim=%d）", self.collection, dim)

    def upsert(self, doc_id: str, chunk_ids: list[str], vectors: list[list[float]]) -> None:
        points = [
            models.PointStruct(
                id=point_id(cid),
                vector=vec,
                payload={"doc_id": doc_id, "chunk_id": cid},
            )
            for cid, vec in zip(chunk_ids, vectors)
        ]
        self._client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete_doc(self, doc_id: str) -> None:
        self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id", match=models.MatchValue(value=doc_id)
                        )
                    ]
                )
            ),
        )

    def search(
        self, vector: list[float], limit: int, doc_ids: list[str] | None = None
    ) -> list[tuple[str, float]]:
        """返回 [(chunk_id, score)]，score 越大越相关。"""
        query_filter = None
        if doc_ids is not None:
            if not doc_ids:
                return []
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids))
                ]
            )
        res = self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            (p.payload.get("chunk_id", ""), p.score)
            for p in res.points
            if p.payload.get("chunk_id")
        ]
