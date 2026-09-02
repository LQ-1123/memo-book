"""运行时配置路由：查看（key 掩码）与热更新，改完即生效无需重启。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Request

from ..core.runtime_config import RUNTIME_FIELDS

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/config")
def get_config(request: Request):
    return request.app.state.cfg.as_dict(mask=True)


@router.put("/config")
@router.post("/config")
def update_config(request: Request, body: dict = Body(...)):
    unknown = set(body) - set(RUNTIME_FIELDS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的字段: {sorted(unknown)}")
    try:
        changed = request.app.state.cfg.update(**body)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    state = request.app.state
    reindex_needed = False
    if "watch_dirs" in changed:
        watcher = getattr(state, "watcher", None)
        if watcher is not None:
            import threading

            watcher.reconfigure()   # 热切换监听目录（含初始扫描，旧目录已入库文档保留）
            log.info("监听目录已热切换")
    if changed:
        if any(f.startswith("embed") for f in changed):
            try:
                state.store.ensure_collection()  # 含维度不一致自动重建
            except Exception as e:
                log.warning("Qdrant 集合同步失败: %s", e)
            reindex_needed = "embed_model" in changed or "embed_dim" in changed
    return {
        "updated": changed,
        "reindex_recommended": reindex_needed,
        "config": state.cfg.as_dict(mask=True),
    }
