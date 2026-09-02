"""LLM 客户端：OpenAI 兼容接口（默认智谱 GLM），支持流式，配置热更新。"""
from __future__ import annotations

import logging
import threading
from typing import Iterator

from openai import OpenAI

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        self._client: OpenAI | None = None
        self._sig: tuple[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self._cfg.get("llm_api_key"))

    def _ensure(self) -> OpenAI | None:
        sig = (str(self._cfg.get("llm_base_url")), str(self._cfg.get("llm_api_key")))
        with self._lock:
            if sig != self._sig:
                base_url, api_key = sig
                self._client = (
                    OpenAI(api_key=api_key, base_url=base_url, timeout=120, max_retries=1)
                    if api_key else None
                )
                self._sig = sig
                if self._client:
                    log.info("LLM 客户端已连接 %s（model=%s）", base_url, self._cfg.get("llm_model"))
        return self._client

    def complete(self, messages: list[dict], temperature: float = 0.3) -> str:
        client = self._ensure()
        if not client:
            raise RuntimeError("llm_api_key 未配置")
        resp = client.chat.completions.create(
            model=str(self._cfg.get("llm_model")),
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict], temperature: float = 0.3) -> Iterator[str]:
        client = self._ensure()
        if not client:
            raise RuntimeError("llm_api_key 未配置")
        resp = client.chat.completions.create(
            model=str(self._cfg.get("llm_model")),
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
