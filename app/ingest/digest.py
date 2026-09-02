"""入库即消化：文档索引完成后用 LLM 生成「一句话摘要 + 3 个关键问题」。

结果以 JSON 字符串存 documents.summary 列（{"summary": str, "questions": [str]}）。
LLM 不可用或失败仅记日志，绝不阻塞入库主流程。
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_HEAD_CHARS = 3000
_TAIL_CHARS = 2000
_MIN_CHARS = 200   # 过短的文档不值得摘要

_SYSTEM = "你是知识库管理员，负责为入库文档生成简明的内容摘要。"

_PROMPT = """请阅读以下文档内容，输出：
1. 摘要：1-2 句话概括这份文档讲了什么（不超过 80 字）
2. 关键问题：3 个读完这份文档能回答的具体问题（用于提示用户可以问什么）

严格按下面的格式输出，不要添加其它内容：
摘要：<一句话>
问题1：<问题>
问题2：<问题>
问题3：<问题>

文档内容：
{content}"""


def _sample_text(texts: list[str]) -> str | None:
    full = "\n".join(texts).strip()
    if len(full) < _MIN_CHARS:
        return None
    if len(full) <= _HEAD_CHARS + _TAIL_CHARS:
        return full
    return full[:_HEAD_CHARS] + "\n……（中段省略）……\n" + full[-_TAIL_CHARS:]


def parse_digest(raw: str) -> dict:
    """宽松解析 LLM 输出，解析失败时全文当摘要、问题留空。"""
    summary_m = re.search(r"摘\s*要\s*[:：]?\s*(.+)", raw)
    questions = re.findall(r"问题\s*\d\s*[:：]?\s*(.+)", raw)
    summary = (summary_m.group(1).strip() if summary_m else "").strip()
    if not summary:
        summary = raw.strip()[:120]
    summary = summary.strip("<>「」“”\"' ")
    return {"summary": summary, "questions": [q.strip()[:120].strip("<>「」“”\"' ") for q in questions[:3]]}


def digest_document(llm, db, doc_id: str) -> bool:
    """为单篇文档生成摘要并落库；返回是否成功写入。"""
    if not getattr(llm, "available", False):
        return False
    doc = db.get_document(doc_id)
    if not doc or doc["status"] != "indexed" or not doc["chunk_count"]:
        return False
    texts = db.doc_chunk_texts(doc_id)
    content = _sample_text(texts)
    if content is None:
        return False
    try:
        raw = llm.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT.format(content=content)},
            ]
        )
    except Exception as e:  # noqa: BLE001
        log.warning("文档 %s 摘要生成失败：%s", doc_id, e)
        return False
    digest = parse_digest(raw or "")
    if not digest["summary"]:
        return False
    db.set_document_summary(doc_id, json.dumps(digest, ensure_ascii=False))
    log.info("文档 %s 摘要已生成", doc_id)
    return True
