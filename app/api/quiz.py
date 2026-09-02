"""小测验路由：出题（异步任务）/ 列表与详情 / 简答判分 / 成绩 / 删除。

路径全部为常量（DELETE 用 ?id=）；简答题的参考答案在判分前不下发。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

_QUIZ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class QuizBody(BaseModel):
    topic: str = Field(min_length=1, max_length=100)
    count: int = Field(default=10, ge=1, le=50)


class GradeBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    index: int = Field(ge=0, le=49)
    answer: str = Field(min_length=1, max_length=2000)


class ResultBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=0)


def _strip_secret(questions: list[dict]) -> list[dict]:
    """简答题剥除参考答案与要点（判分后才由 /quiz/grade 下发）。"""
    out = []
    for q in questions:
        if isinstance(q, dict) and q.get("type") == "short":
            q = {k: v for k, v in q.items() if k not in ("reference", "points")}
        out.append(q)
    return out


@router.post("/quiz", status_code=202)
def create_quiz(body: QuizBody, request: Request):
    state = request.app.state
    task_id = state.db.create_task("quiz", json.dumps({"topic": body.topic, "count": body.count}))

    def _job() -> None:
        from ..ingest.quiz import QuizError, generate_quiz

        try:
            state.db.update_task(task_id, "running", detail="检索相关资料")
            result = generate_quiz(
                state.llm, state.retriever, body.topic.strip(), body.count,
                progress=lambda s: state.db.update_task(task_id, "running", detail=s),
            )
            qid = state.db.insert_quiz(
                None, result["title"], body.topic.strip(),
                json.dumps(result["questions"], ensure_ascii=False), len(result["questions"]),
            )
            state.db.update_task(task_id, "done", detail=f"已生成 {len(result['questions'])} 题")
            log.info("测验已生成 quiz=%s 主题=%s 题数=%d", qid, body.topic, len(result["questions"]))
        except QuizError as e:
            state.db.update_task(task_id, "failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("出题任务失败")
            state.db.update_task(task_id, "failed", error=str(e)[:500])

    state.executor.submit(_job)
    return {"task_id": task_id, "status": "queued"}


@router.get("/quiz")
def get_quizzes(request: Request, id: str = ""):
    db = request.app.state.db
    if id:
        row = db.get_quiz(id)
        if not row:
            raise HTTPException(status_code=404, detail="测验不存在")
        questions = _strip_secret(json.loads(row["questions"]))
        return {"id": row["id"], "doc_id": row["doc_id"], "doc_title": row["doc_title"],
                "title": row["title"], "focus": row["focus"], "count": row["count"],
                "created_at": row["created_at"], "best_score": row["best_score"],
                "plays": row["plays"], "questions": questions}
    return {"items": [
        {"id": r["id"], "doc_id": r["doc_id"], "doc_title": r["doc_title"],
         "title": r["title"], "count": r["count"], "created_at": r["created_at"],
         "best_score": r["best_score"], "plays": r["plays"]}
        for r in db.list_quizzes()
    ]}


@router.post("/quiz/grade")
def grade_answer(body: GradeBody, request: Request):
    from ..ingest.quiz import QuizError, grade_short

    state = request.app.state
    row = state.db.get_quiz(body.id)
    if not row:
        raise HTTPException(status_code=404, detail="测验不存在")
    questions = json.loads(row["questions"])
    if body.index >= len(questions):
        raise HTTPException(status_code=422, detail="题目序号超出范围")
    q = questions[body.index]
    if q.get("type") != "short":
        raise HTTPException(status_code=422, detail="该题不是简答题")
    try:
        return grade_short(state.llm, q, body.answer)
    except QuizError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/quiz/result")
def submit_result(body: ResultBody, request: Request):
    if not _QUIZ_ID_RE.match(body.id):
        raise HTTPException(status_code=422, detail="缺少合法的测验 id")
    ok = request.app.state.db.update_quiz_play(body.id, body.score)
    return {"ok": ok, "id": body.id}


@router.delete("/quiz")
def delete_quiz(request: Request, id: str = ""):
    if not _QUIZ_ID_RE.match(id):
        raise HTTPException(status_code=422, detail="缺少合法的测验 id")
    ok = request.app.state.db.delete_quiz(id)
    return {"ok": ok, "id": id}
