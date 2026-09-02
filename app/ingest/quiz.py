"""小测验：从文档内容出题（单选/判断/简答），简答由 LLM 判分。

出题分批进行（每批 10 题，批间注入已出题干防重复）；LLM 输出宽松解析 +
逐题校验规范化（单选选项重排并同步答案下标）。用户可读错误走 QuizError。
"""
from __future__ import annotations

import json
import logging
import random
import re

log = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_COUNT = 50
_MIN_CHARS = 200          # 文档正文过短不出题

_SYSTEM = "你是严谨的出题官，只依据给定材料出题，不编造材料中没有的内容。"


class QuizError(RuntimeError):
    """对用户可读的测验失败原因。"""


def _split_mix(n: int) -> tuple[int, int, int]:
    """题量配比：单选 60% / 判断 20% / 简答 20%（题太少时不安排简答）。"""
    single = max(1, round(n * 0.6))
    rest = n - single
    if n < 5:
        short = 0
    else:
        short = max(1, round(n * 0.2))
    bool_ = max(0, n - single - short)
    # 兜底修正（round 可能溢出）
    while single + bool_ + short > n:
        if bool_ > 0:
            bool_ -= 1
        elif single > 1:
            single -= 1
        else:
            short -= 1
    return single, bool_, short


def _parse_questions(raw: str) -> list[dict]:
    """宽松解析 LLM 输出的 JSON 数组；失败返回空表。"""
    text = (raw or "").strip()
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        data = json.loads(text[i:j + 1])
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _normalize(q: dict) -> dict | None:
    """逐题校验与规范化；不合法返回 None（剔除）。题干字段兼容 q/question/stem。"""
    if not isinstance(q, dict):
        return None
    t = str(q.get("type", "")).lower()
    stem = ""
    for key in ("q", "question", "stem", "title"):
        v = str(q.get(key, "")).strip()
        if v:
            stem = v
            break
    explanation = str(q.get("explanation", "")).strip()
    ref = str(q.get("ref", "") or q.get("source", "")).strip()[:80]
    if not stem:
        return None
    if t == "single":
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        if len(options) < 2:
            return None
        options = options[:4]
        try:
            answer = int(q.get("answer"))
        except (TypeError, ValueError):
            return None
        if not 0 <= answer < len(options):
            return None
        # 重排选项并同步答案下标，防 LLM 把答案全放 A
        order = list(range(len(options)))
        random.shuffle(order)
        new_options = [options[k] for k in order]
        new_answer = order.index(answer)
        return {"type": "single", "q": stem[:200], "options": new_options,
                "answer": new_answer, "explanation": explanation[:400], "ref": ref}
    if t == "bool":
        a = q.get("answer")
        if not isinstance(a, bool):
            if isinstance(a, str) and a.strip() in ("true", "false", "True", "False"):
                a = a.strip().lower() == "true"
            else:
                return None
        return {"type": "bool", "q": stem[:200], "answer": a,
                "explanation": explanation[:400], "ref": ref}
    if t == "short":
        reference = str(q.get("reference", "")).strip()
        if not reference:
            return None
        points = [str(p).strip()[:100] for p in (q.get("points") or []) if str(p).strip()][:5]
        return {"type": "short", "q": stem[:200], "reference": reference[:500],
                "points": points, "explanation": explanation[:400], "ref": ref}
    return None


def _build_prompt(content: str, single: int, bool_: int, short: int, focus: str, asked: list[str]) -> str:
    parts = ["请基于下面的文档材料出测验题。"]
    if focus:
        parts.append(f"题目需围绕主题重点：{focus}。")
    req = []
    parts.append("只输出以下三类题型的 JSON 对象数组（题干字段名必须是 q），每题附 explanation（解析）与 ref（出处：照抄该题依据的材料段头部《标题》· 小节 · 页码）：")
    if single:
        req.append(f'- 单选题 {single} 道：{{"type":"single","q":"题干","options":["选项1","选项2","选项3","选项4"],"answer":正确选项下标0到3,"explanation":"解析","ref":"出处"}}，正确答案的下标要随机分布')
    if bool_:
        req.append(f'- 判断题 {bool_} 道：{{"type":"bool","q":"陈述句","answer":true或false,"explanation":"解析","ref":"出处"}}')
    if short:
        req.append(f'- 简答题 {short} 道：{{"type":"short","q":"题干","reference":"参考答案","points":["得分要点"],"explanation":"解析","ref":"出处"}}')
    parts.extend(req)
    if asked:
        parts.append("以下题干已出过，不要重复或高度相似：\n" + "\n".join(f"- {a}" for a in asked[:60]))
    parts.append("只输出 JSON 数组本身，不要任何其它文字。材料如下：\n" + content)
    return "\n\n".join(parts)


def _material_from_hits(hits, budget: int) -> str:
    """检索命中片段 → 按文档分组的出题材料（每段带《标题》·小节·页码出处头）。"""
    parts: list[str] = []
    used = 0
    for h in hits:
        text = (h.text or "").strip()
        if not text:
            continue
        loc = [h.title or "未命名"]
        if getattr(h, "heading", None):
            loc.append(str(h.heading))
        if getattr(h, "page", None):
            loc.append(f"第{h.page}页")
        seg = f"【{ ' · '.join(loc) }】\n{text}"
        if used + len(seg) > budget and parts:
            break
        parts.append(seg)
        used += len(seg)
    return "\n\n".join(parts)


def generate_quiz(llm, retriever, topic: str, count: int, progress=None) -> dict:
    """出题主流程：主题 → 跨库检索相关片段 → 分批生成 → 校验汇总。返回 {title, questions}。"""
    if not getattr(llm, "available", False):
        raise QuizError("问答模型未配置：请先在「设置」填写 LLM API Key")
    topic = (topic or "").strip()
    if not topic:
        raise QuizError("请输入主题重点")
    count = max(1, min(int(count), MAX_COUNT))

    if progress:
        progress("检索相关资料")
    topk = min(80, count * 4 + 20)
    hits = retriever.search(topic, topk=topk)
    if not hits:
        raise QuizError(f"知识库中没有找到与「{topic}」相关的资料")
    budget = min(6000 + count * 450, 30000)
    content = _material_from_hits(hits, budget)
    if len(content) < _MIN_CHARS:
        raise QuizError(f"与「{topic}」相关的资料太少，无法出题")

    total_batches = (count + BATCH_SIZE - 1) // BATCH_SIZE
    questions: list[dict] = []
    remaining = count
    for b in range(total_batches):
        n = min(BATCH_SIZE, remaining)
        remaining -= n
        if progress:
            progress(f"出题中 ({b + 1}/{total_batches})")
        single, bool_, short = _split_mix(n)
        prompt = _build_prompt(content, single, bool_, short, topic,
                               asked=[q["q"] for q in questions])
        raw = llm.complete([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ])
        batch = [q for q in (_normalize(item) for item in _parse_questions(raw)) if q]
        questions.extend(batch)
    if not questions:
        raise QuizError("出题失败（模型未返回有效题目），请重试")
    # 标题只用主题本身；题数由列表右侧字段单独展示，不拼进标题
    return {"title": topic[:60], "questions": questions}


_GRADE_SYSTEM = "你是公正的阅卷老师，依据参考答案给用户作答评分。"


def grade_short(llm, question: dict, user_answer: str) -> dict:
    """简答判分：档位 2=命中要点 / 1=部分正确 / 0=未命中，附一句评语。"""
    if not getattr(llm, "available", False):
        raise QuizError("问答模型未配置，无法判分")
    answer = (user_answer or "").strip()
    if not answer:
        return {"score": 0, "comment": "未作答", "reference": question.get("reference", "")}
    prompt = (
        "请对比参考答案与用户作答，严格输出 JSON："
        '{"score": 2|1|0, "comment": "一句中文评语"}'
        "（2=命中要点，1=部分正确，0=未命中）。不要输出其它内容。\n\n"
        f"题目：{question.get('q', '')}\n"
        f"参考答案：{question.get('reference', '')}\n"
        f"得分要点：{'；'.join(question.get('points', [])) or '无'}\n"
        f"用户作答：{answer[:1000]}"
    )
    raw = llm.complete([
        {"role": "system", "content": _GRADE_SYSTEM},
        {"role": "user", "content": prompt},
    ])
    m = re.search(r"\{.*\}", raw or "", re.S)
    score, comment = None, ""
    if m:
        try:
            data = json.loads(m.group(0))
            score = data.get("score")
            comment = str(data.get("comment", ""))[:200]
        except (ValueError, TypeError):
            pass
    if score not in (0, 1, 2):
        raise QuizError("判分失败（模型返回异常），请重试提交")
    return {"score": int(score), "comment": comment, "reference": question.get("reference", "")}
