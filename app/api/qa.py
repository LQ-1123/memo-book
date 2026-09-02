"""检索与问答路由：/search 返回片段+出处；/ask 支持 JSON 与 SSE 流式。

/ask 支持携带 history 多轮上下文；知识库元问题（有哪些文档等）直接
从文档表生成确定性回答，不走向量检索与 LLM。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

_SYSTEM_PROMPT = (
    "你是用户的个人知识库助手。只依据提供的上下文回答问题，"
    "回答中使用引用编号标注来源，格式如 [1]、[2][3]。"
    "如果上下文不足以回答，明确说明知识库中没有相关内容，不要编造。"
    "若提供了对话历史，结合它理解当前问题里的指代与省略（如“它”“第二个”），"
    "但回答内容与引用编号只对应本次提供的上下文。"
    "用简洁的中文回答。"
)


@router.get("/search")
def search(
    q: str,
    request: Request,
    topk: int = 0,
    doc_type: str | None = None,
    source: str | None = None,
):
    if not q.strip():
        raise HTTPException(status_code=422, detail="查询不能为空")
    retriever = request.app.state.retriever
    hits = retriever.search(
        q, topk=topk or None, doc_type=doc_type, source=source
    )
    return {"query": q, "results": [h.to_dict() for h in hits]}


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    stream: bool = False
    topk: int = Field(default=0, ge=0, le=20)
    history: list[HistoryItem] | None = None


def _sources(hits) -> list[dict]:
    out = []
    for i, h in enumerate(hits, 1):
        loc = {"ref": i, "title": h.title, "doc_id": h.doc_id, "type": h.doc_type}
        if h.heading:
            loc["heading"] = h.heading
        if h.page:
            loc["page"] = h.page
        if h.url:
            loc["url"] = h.url
        else:
            loc["path"] = h.path
        loc["chunk_id"] = h.chunk_id
        loc["text"] = (h.text or "")[:400]   # 引用原句：右栏点击展开即见
        out.append(loc)
    return out


# ---------- 元问题路由：问“库里有什么”直接查文档表，确定性回答 ----------

# 必须命中“文档/笔记/…”等对象词或“什么/哪些”，避免“Rust 有哪些特性”误伤
_META_PATTERNS = [
    r"(有哪些|列出了?|列出所有|清单|列表|多少[篇个条]|几[篇个条])[^。?？!]{0,12}(文档|笔记|资料|视频|文章|书|知识库)",
    r"知识库[^。?？!]{0,4}(有|包含|存了?|收录了?)(什么|哪些)",
    r"(最近|最新)(收录|入库|添加|保存|收藏)[^。?？!]{0,6}(什么|哪些|文档|笔记|资料|视频|文章|书)",
]
_META_RES = [re.compile(p) for p in _META_PATTERNS]
_TOPIC_RE = re.compile(r"关于(.{1,24}?)(的)?(笔记|文档|资料|文章|视频)")
_TYPE_NAMES = {
    "pdf": "PDF", "md": "笔记 / Markdown", "html": "网页剪藏", "video": "视频笔记",
    "docx": "Word", "pptx": "PPT", "xlsx": "表格", "code": "代码", "image": "图片",
}


def _rel_time(ts: float | None) -> str:
    if not ts:
        return ""
    sec = max(0, time.time() - ts)
    if sec < 60:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时前"
    return f"{int(sec // 86400)} 天前"


def _meta_answer(question: str, db) -> dict | None:
    """命中元问题意图时返回 {answer, sources}，否则 None（走正常检索问答）。"""
    q = question.strip()
    if not any(rx.search(q) for rx in _META_RES):
        return None
    rows, total = db.list_documents(status="indexed", limit=200)
    m = _TOPIC_RE.search(q)
    topic = (m.group(1).strip() if m else "").strip("「」“”\"'")
    if topic:
        rows = [r for r in rows if topic.lower() in (r["title"] or "").lower()]
    if not rows:
        if topic and total:
            return {"answer": f"没有找到标题包含「{topic}」的文档（知识库共 {total} 篇，可问「知识库里有哪些文档」查看全部）。", "sources": []}
        return {"answer": "知识库当前没有已索引的文档。把文件放进监听目录、上传或粘贴链接即可入库。", "sources": []}

    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["doc_type"], []).append(r)
    head = f"知识库共有 {total} 篇已索引文档"
    if topic:
        head += f"，其中标题包含「{topic}」的有 {len(rows)} 篇"
    lines = [head + "："]
    for t, items in by_type.items():
        lines.append(f"\n{_TYPE_NAMES.get(t, t)}（{len(items)} 篇）：")
        for r in items:
            lines.append(f"- 《{r['title']}》（{_rel_time(r['indexed_at'] or r['created_at'])}入库）")

    sources = []
    for i, r in enumerate(rows[:20], 1):
        loc = {"ref": i, "title": r["title"], "doc_id": r["id"], "type": r["doc_type"]}
        if r["url"]:
            loc["url"] = r["url"]
        else:
            loc["path"] = r["path"]
        loc["chunk_id"] = ""
        loc["text"] = f"《{r['title']}》 · {_TYPE_NAMES.get(r['doc_type'], r['doc_type'])} · {_rel_time(r['indexed_at'] or r['created_at'])}入库"
        sources.append(loc)
    return {"answer": "\n".join(lines), "sources": sources}


@router.post("/ask")
def ask(body: AskBody, request: Request):
    state = request.app.state

    meta = _meta_answer(body.question, state.db)
    if meta is not None:
        if body.stream:
            def sse_meta():
                yield f"data: {json.dumps({'type': 'sources', 'sources': meta['sources']}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'delta', 'text': meta['answer']}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"

            return StreamingResponse(
                sse_meta(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return {"answer": meta["answer"], "sources": meta["sources"]}

    hits = state.retriever.search(
        body.question, topk=body.topk or state.settings.ask_topk
    )
    if not hits:
        return {
            "answer": "知识库中没有检索到与问题相关的内容，无法回答。",
            "sources": [],
        }
    context = state.retriever.context_block(hits)
    history = [
        {"role": h.role, "content": h.content[:2000]}
        for h in (body.history or [])[-12:]
    ]
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
        {
            "role": "user",
            "content": f"上下文：\n\n{context}\n\n问题：{body.question}",
        },
    ]

    if not body.stream or not state.llm.available:
        if not state.llm.available:
            raise HTTPException(status_code=503, detail="llm_api_key 未配置，无法问答")
        answer = state.llm.complete(messages)
        return {"answer": answer, "sources": _sources(hits)}

    def sse():
        yield f"data: {json.dumps({'type': 'sources', 'sources': _sources(hits)}, ensure_ascii=False)}\n\n"
        try:
            for delta in state.llm.stream(messages):
                yield f"data: {json.dumps({'type': 'delta', 'text': delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("流式问答失败")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
