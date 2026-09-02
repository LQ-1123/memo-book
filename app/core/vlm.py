"""VisionClient：图像理解客户端（OpenAI 兼容多模态接口，默认智谱 GLM-4V-Flash，免费）。

配置热更新同 LLM/Embedding：每次调用前读取 RuntimeConfig（vision_model 为空 = 图像理解关闭）。
base_url / api_key 复用问答模型的 llm_* 配置——同一服务商，少填三项。
"""
from __future__ import annotations

import base64
import logging
import threading

from openai import OpenAI

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)

_PROMPT = (
    "这是文档中的一页页面图。请用中文描述本页的视觉内容："
    "包含的图表/流程图/照片/示意图及其含义，图表中的关键数据或结论；"
    "如页面主要是文字版式（如联系方式、列表），概括其要点。"
    "控制在 250 字以内，直接给出内容，不要开场白。"
)


class VisionClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        self._client: OpenAI | None = None
        self._sig: tuple[str, str, str] | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(str(self._cfg.get("vision_model") or "").strip()) and bool(
            self._cfg.get("llm_api_key")
        )

    def _ensure(self) -> OpenAI | None:
        sig = (
            str(self._cfg.get("llm_base_url")),
            str(self._cfg.get("llm_api_key")),
            str(self._cfg.get("vision_model") or ""),
        )
        with self._lock:
            if sig != self._sig:
                base_url, api_key, _model = sig
                self._client = (
                    OpenAI(api_key=api_key, base_url=base_url, timeout=120, max_retries=1)
                    if api_key and _model.strip()
                    else None
                )
                self._sig = sig
        return self._client

    def describe_page(self, jpeg_bytes: bytes, page_no: int) -> str:
        """描述一页页面图（JPEG 字节），返回图像理解文本。"""
        client = self._ensure()
        if not client:
            raise RuntimeError("vision_model 未配置")
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        resp = client.chat.completions.create(
            model=str(self._cfg.get("vision_model") or "").strip(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": f"（第 {page_no} 页）{_PROMPT}"},
                    ],
                }
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
