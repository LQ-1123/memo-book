"""对话线程路由：对话历史后端持久化（localStorage 降级为缓存）。

路径全部为常量（/threads、DELETE 用 ?id= 查询参数），前端不拼动态 id。
"""
from __future__ import annotations

import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_BLOCKS = 400
_MAX_BLOCK_TEXT = 20000


class ThreadBlock(BaseModel):
    r: str = Field(pattern="^(q|a)$")
    t: str = Field(default="", max_length=_MAX_BLOCK_TEXT)
    srcs: list[dict] | None = None
    draft: str | None = None


class ThreadBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)
    ts: float = 0
    blocks: list[ThreadBlock] = Field(default_factory=list, max_length=_MAX_BLOCKS)
    draft: str = Field(default="", max_length=20000)


def _dump_data(body: ThreadBody) -> str:
    return json.dumps(
        {
            "title": body.title,
            "ts": body.ts,
            "blocks": [
                ({"r": b.r, "t": b.t, "srcs": b.srcs} if b.srcs else {"r": b.r, "t": b.t})
                for b in body.blocks
            ],
            "draft": body.draft,
        },
        ensure_ascii=False,
    )


@router.get("/threads")
def list_threads(request: Request):
    rows = request.app.state.db.list_threads(limit=100)
    items = []
    for r in rows:
        try:
            data = json.loads(r["data"])
        except (ValueError, TypeError):
            continue  # 脏数据跳过，不让单条坏线程拖垮整个列表
        items.append(
            {
                "id": r["id"],
                "updated_at": r["updated_at"],
                "title": data.get("title", ""),
                "ts": data.get("ts", 0),
                "blocks": data.get("blocks", []),
                "draft": data.get("draft", ""),
            }
        )
    return {"items": items}


@router.post("/threads")
def upsert_thread(body: ThreadBody, request: Request):
    if not _THREAD_ID_RE.match(body.id):
        raise HTTPException(status_code=422, detail="线程 id 只允许字母数字_-、最长 64 字符")
    request.app.state.db.upsert_thread(body.id, _dump_data(body), body.ts or time.time())
    return {"ok": True, "id": body.id}


@router.delete("/threads")
def delete_thread(request: Request, id: str = ""):
    if not _THREAD_ID_RE.match(id):
        raise HTTPException(status_code=422, detail="缺少合法的线程 id")
    ok = request.app.state.db.delete_thread(id)
    return {"ok": ok, "id": id}
