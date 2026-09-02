"""Rerank 客户端：SiliconFlow 兼容 /rerank（可选，未配置时优雅降级），配置热更新。"""
from __future__ import annotations

import logging

import httpx

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)


class RerankClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg

    @property
    def available(self) -> bool:
        return bool(self._cfg.get("rerank_api_key"))

    def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]] | None:
        """返回 [(原文档下标, 相关性分数)]，按分数降序；失败返回 None（调用方回退原排序）。"""
        if not self.available or not documents:
            return None
        try:
            resp = httpx.post(
                f"{self._cfg.get('rerank_base_url')}/rerank",
                headers={"Authorization": f"Bearer {self._cfg.get('rerank_api_key')}"},
                json={
                    "model": self._cfg.get("rerank_model"),
                    "query": query,
                    "documents": documents,
                },
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [(r["index"], float(r["relevance_score"])) for r in results]
        except Exception as e:
            log.warning("rerank 失败，回退 RRF 排序: %s", e)
            return None
