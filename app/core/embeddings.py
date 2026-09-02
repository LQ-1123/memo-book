"""嵌入客户端：OpenAI 兼容接口（默认智谱 embedding-3），配置热更新。"""
from __future__ import annotations

import logging
import threading
import time

from openai import OpenAI

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        self._client: OpenAI | None = None
        self._sig: tuple[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self._cfg.get("embed_api_key"))

    @property
    def dim(self) -> int:
        return int(self._cfg.get("embed_dim"))

    def _ensure(self) -> OpenAI | None:
        sig = (str(self._cfg.get("embed_base_url")), str(self._cfg.get("embed_api_key")))
        with self._lock:
            if sig != self._sig:
                base_url, api_key = sig
                self._client = (
                    OpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=1)
                    if api_key else None
                )
                self._sig = sig
                if self._client:
                    log.info("嵌入客户端已连接 %s（model=%s）", base_url, self._cfg.get("embed_model"))
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入；空串替换占位以保持下标对齐。失败重试 3 次后抛出。"""
        client = self._ensure()
        if not client:
            raise RuntimeError("embed_api_key 未配置，无法嵌入")
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):
            batch = [t if t.strip() else "（空）" for t in texts[i : i + 16]]
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    resp = client.embeddings.create(
                        model=str(self._cfg.get("embed_model")),
                        input=batch,
                        dimensions=self.dim,
                    )
                    data = sorted(resp.data, key=lambda d: d.index)
                    out.extend(d.embedding for d in data)
                    last_err = None
                    break
                except Exception as e:  # 网络/限流：退避重试
                    last_err = e
                    wait = 1.5 * (attempt + 1)
                    log.warning("嵌入请求失败（第 %d 次），%.1fs 后重试: %s | 原因: %r",
                                attempt + 1, wait, e, e.__cause__)
                    time.sleep(wait)
            if last_err:
                raise last_err
        return out
