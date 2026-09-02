"""监听目录的动态解析：运行时配置优先，.env 兜底。"""
from __future__ import annotations

from pathlib import Path


def effective_watch_dirs(cfg, settings) -> list[Path]:
    raw = ""
    if cfg is not None:
        try:
            raw = str(cfg.get("watch_dirs") or "").strip()
        except KeyError:
            raw = ""
    if not raw:
        return list(settings.watch_dir_list)
    out: list[Path] = []
    for part in raw.split("\n"):
        p = part.strip()
        if p:
            out.append(Path(p))
    return out
