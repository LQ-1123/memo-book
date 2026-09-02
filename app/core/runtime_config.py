"""运行时可变配置：模型服务的 base_url / key / model 存 JSON，修改即生效无需重启。

.env 提供初始默认值；此后 data/runtime_config.json 的覆盖值优先。
客户端（Embedding/LLM/Rerank）每次调用前读取，感知变化自动重建连接。
安全约定：对外展示一律经 as_dict(mask=True)，key 只以掩码出现。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RUNTIME_FIELDS = [
    "embed_base_url", "embed_api_key", "embed_model", "embed_dim",
    "llm_base_url", "llm_api_key", "llm_model",
    "rerank_base_url", "rerank_api_key", "rerank_model",
    "asr_base_url", "asr_api_key", "asr_model",   # 留空回落 llm_*（智谱 GLM-ASR）
    "bilibili_sessdata", "vision_model",
    "watch_dirs",   # 运行时可改监听目录（每行一个绝对路径；空 = 用 .env 默认）
]
# 对外展示一律掩码的字段：各类 API key + 平台登录态
_SECRET_FIELDS = {f for f in RUNTIME_FIELDS if f.endswith("_api_key")} | {"bilibili_sessdata"}


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:3] + "***" + value[-4:]


class RuntimeConfig:
    def __init__(self, settings) -> None:
        self._lock = threading.Lock()
        self._path: Path = settings.data_dir / "runtime_config.json"
        self._values: dict[str, Any] = {f: getattr(settings, f) for f in RUNTIME_FIELDS}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for f in RUNTIME_FIELDS:
                    if isinstance(data.get(f), (str, int)) and data[f] != "":
                        self._values[f] = data[f]
            except Exception as e:
                log.warning("runtime_config.json 读取失败，使用 .env 默认值: %s", e)

    def get(self, field: str) -> Any:
        return self._values[field]

    def as_dict(self, mask: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in RUNTIME_FIELDS:
            v = self._values[f]
            out[f] = _mask(v) if (mask and f in _SECRET_FIELDS) else v
        return out

    def update(self, **kwargs: Any) -> list[str]:
        """更新并持久化；返回实际变更的字段名。None/缺省 = 不变，"" = 清空 key。"""
        changed: list[str] = []
        with self._lock:
            for f in RUNTIME_FIELDS:
                if f not in kwargs or kwargs[f] is None:
                    continue
                v = kwargs[f]
                if f == "embed_dim":
                    v = int(v)
                    if not 256 <= v <= 8192:
                        raise ValueError("embed_dim 取值范围 256-8192")
                elif f == "watch_dirs":
                    import os
                    parts = [x.strip() for x in str(v).replace("\n", ",").split(",") if x.strip()]
                    norm = []
                    for x in parts:
                        exp = os.path.expanduser(x)
                        if not exp.startswith("/"):
                            raise ValueError("监听目录必须是绝对路径: " + x)
                        norm.append(exp)
                    v = "\n".join(norm)
                elif f.endswith("_base_url"):
                    v = str(v).strip().rstrip("/")
                    if v and not v.startswith(("http://", "https://")):
                        raise ValueError(f"{f} 必须是 http(s) URL")
                else:
                    v = str(v).strip()
                if v != self._values[f]:
                    self._values[f] = v
                    changed.append(f)
            if changed:
                data = {f: self._values[f] for f in RUNTIME_FIELDS}
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self._path)
                log.info("运行时配置已更新: %s", changed)
        return changed
