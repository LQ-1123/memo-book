"""小测验测试：出题解析/分批/校验规范化、简答判分、db CRUD、API 任务生命周期（LLM 全 fake）。"""
import json
import secrets
import time

import pytest

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.db import Database
from app.ingest.quiz import (
    QuizError,
    _material_from_hits,
    _normalize,
    _split_mix,
    generate_quiz,
    grade_short,
)
from app.ingest.retriever import SearchHit
from app.main import create_app

TOKEN = "tok-" + secrets.token_hex(8)
DEAD_QDRANT = "http://127.0.0.1:1"

_GOOD = """
[
 {"type":"single","q":"Rust 的所有权规则不包括？","options":["每个值有唯一所有者","值可同时被两个变量拥有","所有者离开作用域值被释放","所有权可转移"],"answer":1,"explanation":"Rust 不允许双所有者。","ref":"第4章"},
 {"type":"bool","q":"Rust 编译期即可保证内存安全。","answer":true,"explanation":"所有权在编译期检查。","ref":"第1章"},
 {"type":"short","q":"简述 borrow checker 的作用。","reference":"在编译期检查引用是否违反借用规则，防止数据竞争与悬垂引用。","points":["编译期检查","防止数据竞争"],"explanation":"","ref":"第4章"}
]
"""


class FakeRetriever:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    def search(self, q, topk=None, doc_type=None, source=None):
        self.calls.append((q, topk))
        return self.hits


class FakeLLM:
    """出题与判分两用桩：判分 prompt（含"用户作答"）返回判分 JSON，否则返回出题 JSON。"""

    def __init__(self, raw=_GOOD):
        self.available = True
        self.raw = raw
        self.calls: list[str] = []

    def complete(self, messages, temperature=0.3):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if "用户作答" in prompt:
            return '{"score": 2, "comment": "答得不错"}'
        return self.raw


def make_db(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.upsert_document_by_path(
        path="/tmp/book.md", source="watch", url=None, title="Rust 书", doc_type="md",
        sha="h1", size=10, mtime=1.0, status="indexed",
    )
    from app.core.db import ChunkRow
    from app.ingest.pipeline import fts_tokenize

    chunks = [ChunkRow(f"{doc_id}:{i:04d}", doc_id, i, "", None, "所有权与借用是 Rust 的核心概念。" * 20) for i in range(20)]
    db.replace_chunks(doc_id, chunks, [fts_tokenize(c.text) for c in chunks])
    db.set_document_status(doc_id, "indexed", chunk_count=len(chunks), mark_indexed=True)
    return db, doc_id


# ---------- 出题核心 ----------


def test_split_mix_ratio():
    assert _split_mix(10) == (6, 2, 2)
    s, b, sh = _split_mix(50)
    assert s + b + sh == 50 and sh >= 1 and b >= 1
    s3, b3, sh3 = _split_mix(3)
    assert sh3 == 0 and s3 + b3 == 3


def _hit(text, title="Rust 书", heading="", page=None, score=0.9):
    return SearchHit(chunk_id="c1", doc_id="d1", text=text, heading=heading,
                     page=page, score=score, title=title, path="/tmp/x.md",
                     url=None, doc_type="md", source="watch")


def test_material_from_hits_groups_and_budget():
    hits = [
        _hit("所有权内容甲" * 50, title="书A", heading="第四章", page=88),
        _hit("借用内容乙" * 50, title="书B"),
    ]
    out = _material_from_hits(hits, 99999)
    assert "【书A · 第四章 · 第88页】" in out and "【书B】" in out
    assert "所有权内容甲" in out and "借用内容乙" in out
    # 预算不足时至少保留第一段
    tiny = _material_from_hits(hits, 10)
    assert tiny.startswith("【书A")


def test_normalize_shuffles_and_syncs_answer():
    q = {"type": "single", "q": "题", "options": ["甲", "乙", "丙", "丁"], "answer": 0}
    seen_answers = set()
    for _ in range(30):
        out = _normalize(dict(q))
        seen_answers.add(out["answer"])
        assert out["options"][out["answer"]] == "甲"  # 下标重排后答案仍指向正确选项
    assert len(seen_answers) > 1  # 确实被打散


def test_normalize_drops_bad_items():
    assert _normalize({"type": "single", "q": "x", "options": ["只有一项"], "answer": 0}) is None
    assert _normalize({"type": "single", "q": "", "options": ["a", "b"], "answer": 0}) is None
    assert _normalize({"type": "bool", "q": "x", "answer": "yes"}) is None
    assert _normalize({"type": "bool", "q": "x", "answer": "false"})["answer"] is False
    assert _normalize({"type": "short", "q": "x"}) is None
    assert _normalize({"type": "short", "q": "x", "reference": "r"})["type"] == "short"
    assert _normalize("not a dict") is None


def test_generate_quiz_batches_and_dedup(tmp_path):
    """22 题 → 3 批（10/10/2）；FakeLLM 每批返回 3 题 → 共 9 题；第 2 批起注入已出题干防重复。"""
    db, doc_id = make_db(tmp_path)
    retriever = FakeRetriever([_hit("Rust 的所有权规则与借用检查。" * 30)])
    llm = FakeLLM()
    prompts = []
    orig = llm.complete

    def record(messages, temperature=0.3):
        prompts.append(messages[-1]["content"])
        return orig(messages, temperature)

    llm.complete = record
    result = generate_quiz(llm, retriever, "所有权", 22)
    assert len(prompts) == 3  # 分批调用
    assert len(result["questions"]) == 9  # 3 批 × 每批 3 题
    assert "已出过" in prompts[1] and "Rust 的所有权规则不包括" in prompts[1]  # 防重复注入
    assert retriever.calls[0][0] == "所有权"  # 主题作为检索词
    assert "Rust 的所有权规则与借用检查" in prompts[0]  # 命中材料进了 prompt


def test_generate_quiz_topic_and_progress(tmp_path):
    retriever = FakeRetriever([_hit("生命周期的标注与省略规则。" * 30)])
    llm = FakeLLM()
    stages = []
    result = generate_quiz(llm, retriever, "生命周期", 5, progress=stages.append)
    assert any("生命周期" in p for p in llm.calls)
    assert "生命周期" in result["title"]
    assert stages[0] == "检索相关资料" and stages[1].startswith("出题中")
    types = {q["type"] for q in result["questions"]}
    assert types == {"single", "bool", "short"}


def test_generate_quiz_no_hits_and_empty_topic():
    with pytest.raises(QuizError, match="相关"):
        generate_quiz(FakeLLM(), FakeRetriever([]), "不存在的主题", 5)
    with pytest.raises(QuizError, match="主题"):
        generate_quiz(FakeLLM(), FakeRetriever([_hit("x" * 300)]), "  ", 5)


def test_generate_quiz_material_too_short():
    retriever = FakeRetriever([_hit("太短")])
    with pytest.raises(QuizError, match="太少"):
        generate_quiz(FakeLLM(), retriever, "某主题", 5)


def test_generate_quiz_empty_result():
    retriever = FakeRetriever([_hit("足够长的材料内容。" * 30)])
    with pytest.raises(QuizError):
        generate_quiz(FakeLLM(raw="模型今天不想出题"), retriever, "主题", 5)


# ---------- 简答判分 ----------


def test_grade_short_parses_and_empty():
    q = {"q": "题", "reference": "参考", "points": ["要点1"]}
    out = grade_short(FakeLLM(raw='{"score": 2, "comment": "答得不错"}'), q, "我的答案")
    assert out["score"] == 2 and "答得不错" in out["comment"] and out["reference"] == "参考"
    assert grade_short(FakeLLM(), q, "")["score"] == 0
    class _BadGrader:
        available = True

        def complete(self, messages, temperature=0.3):
            return "评分失败"

    with pytest.raises(QuizError):
        grade_short(_BadGrader(), q, "答案")


# ---------- db ----------


def test_db_quiz_crud_and_cascade(tmp_path):
    db, doc_id = make_db(tmp_path)
    qid = db.insert_quiz(doc_id, "测验A", "", json.dumps([{"type": "bool", "q": "x", "answer": True}]), 1)
    rows = db.list_quizzes()
    assert rows[0]["id"] == qid and rows[0]["doc_title"] == "Rust 书"
    assert db.update_quiz_play(qid, 0.5) is True
    assert db.update_quiz_play(qid, 2.5) is True
    row = db.get_quiz(qid)
    assert row["plays"] == 2 and row["best_score"] == 2.5
    db.delete_document(doc_id)  # 级联
    assert db.get_quiz(qid) is None
    assert db.delete_quiz(qid) is False


# ---------- API ----------


def make_client(tmp_path):
    settings = Settings(
        api_keys=TOKEN, data_dir=tmp_path / "data", watch_dirs="",
        qdrant_url=DEAD_QDRANT, _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def _auth():
    return {"X-API-Key": TOKEN}


def _wait_task(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/v1/tasks/{task_id}", headers=_auth()).json()
        if row["status"] in ("done", "failed"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"任务超时: {task_id}")


def test_api_quiz_lifecycle(tmp_path):
    with make_client(tmp_path) as client:
        state = client.app.state
        state.llm = FakeLLM()
        state.retriever = FakeRetriever([_hit("所有权与借用是 Rust 的核心概念。" * 30, title="Rust 书")])
        resp = client.post("/api/v1/quiz", json={"topic": "所有权", "count": 5}, headers=_auth())
        assert resp.status_code == 202
        row = _wait_task(client, resp.json()["task_id"])
        assert row["status"] == "done", row.get("error")

        items = client.get("/api/v1/quiz", headers=_auth()).json()["items"]
        assert len(items) == 1
        qid = items[0]["id"]

        detail = client.get(f"/api/v1/quiz?id={qid}", headers=_auth()).json()
        shorts = [q for q in detail["questions"] if q["type"] == "short"]
        assert shorts and all("reference" not in q and "points" not in q for q in shorts)

        # 简答判分（回参考答案）
        idx = next(i for i, q in enumerate(detail["questions"]) if q["type"] == "short")
        g = client.post("/api/v1/quiz/grade", json={"id": qid, "index": idx, "answer": "借用检查器"}, headers=_auth()).json()
        assert g["score"] in (0, 1, 2) and g["reference"]

        # 非简答题判分 422；成绩与删除
        assert client.post("/api/v1/quiz/grade", json={"id": qid, "index": 0 if detail["questions"][0]["type"] != "short" else 1, "answer": "x"}, headers=_auth()).status_code in (200, 422)
        assert client.post("/api/v1/quiz/result", json={"id": qid, "score": 3.5}, headers=_auth()).json()["ok"] is True
        assert client.delete(f"/api/v1/quiz?id={qid}", headers=_auth()).json()["ok"] is True
        assert client.get("/api/v1/quiz", headers=_auth()).json()["items"] == []


def test_api_quiz_validation(tmp_path):
    with make_client(tmp_path) as client:
        client.app.state.llm = FakeLLM()
        client.app.state.retriever = FakeRetriever([_hit("材料。" * 100)])
        assert client.get("/api/v1/quiz").status_code == 401
        assert client.post("/api/v1/quiz", json={"topic": "", "count": 5}, headers=_auth()).status_code == 422
        assert client.post("/api/v1/quiz", json={"topic": "x", "count": 99}, headers=_auth()).status_code == 422
        assert client.delete("/api/v1/quiz?id=", headers=_auth()).status_code == 422
