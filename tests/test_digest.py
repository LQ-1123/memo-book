"""入库即消化（LLM 摘要）测试：digest_document 单测 + API 任务生命周期（全 fake）。"""
import json
import secrets
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.db import ChunkRow, Database
from app.ingest.digest import digest_document, parse_digest
from app.ingest.pipeline import fts_tokenize
from app.main import create_app

TOKEN = "tok-" + secrets.token_hex(8)
DEAD_QDRANT = "http://127.0.0.1:1"

_RAW = "摘要：这本书系统介绍了 Rust 的所有权与并发模型。\n问题1：什么是所有权？\n问题2：trait 是什么？\n问题3：async 如何工作？"


class FakeLLM:
    def __init__(self, available=True, raw=_RAW):
        self.available = available
        self.raw = raw
        self.calls: list[list[dict]] = []

    def complete(self, messages, temperature=0.3):
        self.calls.append(messages)
        return self.raw

    def stream(self, messages, temperature=0.3):
        yield self.raw


def make_db(tmp_path):
    return Database(tmp_path / "t.db")


def _seed_indexed_doc(db, text="Rust 是一门系统级编程语言。" * 30):
    doc_id = db.upsert_document_by_path(
        "/tmp/book.md", "watch", None, "Rust 书", "md", "sha1", 10, 1.0, "indexing"
    )
    chunks = [ChunkRow(f"{doc_id}:0000", doc_id, 0, "", None, text)]
    db.replace_chunks(doc_id, chunks, [fts_tokenize(text)])
    db.set_document_status(doc_id, "indexed", chunk_count=1, title="Rust 书", mark_indexed=True)
    return doc_id


# ---------- digest_document / parse_digest ----------


def test_digest_document_writes_summary_json(tmp_path):
    db = make_db(tmp_path)
    doc_id = _seed_indexed_doc(db)
    llm = FakeLLM()
    assert digest_document(llm, db, doc_id) is True
    summary = json.loads(db.get_document(doc_id)["summary"])
    assert "所有权" in summary["summary"]
    assert len(summary["questions"]) == 3


def test_digest_skips_short_or_unavailable(tmp_path):
    db = make_db(tmp_path)
    doc_short = _seed_indexed_doc(db, text="太短")  # <200 字符
    assert digest_document(FakeLLM(), db, doc_short) is False
    doc_ok = _seed_indexed_doc(db)
    # 换路径再种一篇长文
    doc_id = db.upsert_document_by_path("/tmp/long.md", "watch", None, "L", "md", "sha2", 10, 1.0, "indexing")
    chunks = [ChunkRow(f"{doc_id}:0000", doc_id, 0, "", None, "内容" * 300)]
    db.replace_chunks(doc_id, chunks, [fts_tokenize(chunks[0].text)])
    db.set_document_status(doc_id, "indexed", chunk_count=1, mark_indexed=True)
    assert digest_document(FakeLLM(available=False), db, doc_id) is False  # LLM 不可用
    assert digest_document(FakeLLM(), db, doc_ok) is True


def test_parse_digest_lenient():
    d = parse_digest("摘要：一句话\n问题1：a\n问题2：b\n问题3：c")
    assert d == {"summary": "一句话", "questions": ["a", "b", "c"]}
    # 格式不符：全文当摘要，问题留空
    d2 = parse_digest("随便一段没有格式的输出")
    assert d2["summary"].startswith("随便")
    assert d2["questions"] == []


def test_digest_exception_does_not_raise(tmp_path):
    db = make_db(tmp_path)
    doc_id = _seed_indexed_doc(db)

    class Boom:
        available = True

        def complete(self, messages, temperature=0.3):
            raise RuntimeError("网络错误")

    assert digest_document(Boom(), db, doc_id) is False
    assert db.get_document(doc_id)["summary"] is None


# ---------- API 端点 ----------


def make_client(tmp_path):
    settings = Settings(
        api_keys=TOKEN,
        data_dir=tmp_path / "data",
        watch_dirs="",
        qdrant_url=DEAD_QDRANT,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def _wait_task(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/v1/tasks/{task_id}", headers={"X-API-Key": TOKEN}).json()
        if row["status"] in ("done", "failed"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"任务超时未完成: {task_id}")


def test_digest_endpoint_task_lifecycle(tmp_path):
    with make_client(tmp_path) as client:
        state = client.app.state
        state.llm = FakeLLM()
        doc_id = _seed_indexed_doc(state.db)
        resp = client.post(
            "/api/v1/documents/digest", json={"doc_id": doc_id}, headers={"X-API-Key": TOKEN}
        )
        assert resp.status_code == 202
        row = _wait_task(client, resp.json()["task_id"])
        assert row["status"] == "done"
        assert "所有权" in json.loads(state.db.get_document(doc_id)["summary"])["summary"]


def test_digest_endpoint_404(tmp_path):
    with make_client(tmp_path) as client:
        client.app.state.llm = FakeLLM()
        resp = client.post(
            "/api/v1/documents/digest", json={"doc_id": "no-such"}, headers={"X-API-Key": TOKEN}
        )
        assert resp.status_code == 404


def test_digest_missing_batch(tmp_path):
    with make_client(tmp_path) as client:
        state = client.app.state
        state.llm = FakeLLM()
        _seed_indexed_doc(state.db)
        resp = client.post("/api/v1/documents/digest-missing", headers={"X-API-Key": TOKEN})
        assert resp.status_code == 200
        assert resp.json()["queued"] == 1


def test_documents_list_returns_summary(tmp_path):
    with make_client(tmp_path) as client:
        state = client.app.state
        doc_id = _seed_indexed_doc(state.db)
        state.db.set_document_summary(doc_id, '{"summary": "s", "questions": ["q"]}')
        item = client.get("/api/v1/documents", headers={"X-API-Key": TOKEN}).json()["items"][0]
        assert item["summary"] == '{"summary": "s", "questions": ["q"]}'
