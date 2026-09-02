"""threads 后端持久化测试：db 层 CRUD + API 常量路径路由（离线）。"""
import json
import secrets
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.db import Database
from app.main import create_app

TOKEN = "tok-" + secrets.token_hex(8)
DEAD_QDRANT = "http://127.0.0.1:1"


def make_client(tmp_path):
    settings = Settings(
        api_keys=TOKEN,
        data_dir=tmp_path / "data",
        watch_dirs="",
        qdrant_url=DEAD_QDRANT,
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    return TestClient(app)


def _auth():
    return {"X-API-Key": TOKEN}


def _thread_body(tid="t1700000000000", title="问题标题", ts=None):
    return {
        "id": tid,
        "title": title,
        "ts": ts or time.time(),
        "blocks": [
            {"r": "q", "t": "什么是 trait？"},
            {"r": "a", "t": "trait 定义共享行为 [1]", "srcs": [{"ref": 1, "title": "Rust 书"}]},
        ],
        "draft": "",
    }


# ---------- db 层 ----------


def test_db_thread_upsert_list_delete(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_thread("t1", json.dumps({"title": "A", "ts": 1, "blocks": [], "draft": ""}), 100.0)
    db.upsert_thread("t2", json.dumps({"title": "B", "ts": 2, "blocks": [], "draft": ""}), 200.0)
    rows = db.list_threads()
    assert [r["id"] for r in rows] == ["t2", "t1"]  # 按 updated_at DESC
    # upsert 覆盖同 id
    db.upsert_thread("t1", json.dumps({"title": "A2", "ts": 1, "blocks": [], "draft": ""}), 300.0)
    rows = db.list_threads()
    assert [r["id"] for r in rows] == ["t1", "t2"]
    assert json.loads(rows[0]["data"])["title"] == "A2"
    assert db.delete_thread("t2") is True
    assert db.delete_thread("t2") is False  # 再删无此行
    assert [r["id"] for r in db.list_threads()] == ["t1"]


def test_db_summary_migration_on_old_db(tmp_path):
    """旧库（documents 无 summary 列）重新打开：ALTER 迁移补列不报错。"""
    import sqlite3

    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL UNIQUE, url TEXT, title TEXT NOT NULL DEFAULT '', doc_type TEXT NOT NULL, hash TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', error TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, indexed_at REAL)")
    conn.commit()
    conn.close()
    db = Database(p)  # __init__ 走迁移
    with db._conn() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(documents)").fetchall()]
    assert "summary" in cols


def test_db_summary_helpers(tmp_path):
    db = Database(tmp_path / "t.db")
    d1 = db.upsert_document_by_path("/tmp/a.md", "watch", None, "A", "md", "h1", 1, 1.0, "indexed")
    db.set_document_status(d1, "indexed", chunk_count=2, mark_indexed=True)
    d2 = db.upsert_document_by_path("/tmp/b.md", "watch", None, "B", "md", "h2", 1, 1.0, "indexed")
    db.set_document_status(d2, "indexed", chunk_count=1, mark_indexed=True)
    assert set(db.doc_ids_missing_summary()) == {d1, d2}
    db.set_document_summary(d1, '{"summary": "s", "questions": []}')
    assert db.doc_ids_missing_summary() == [d2]
    from app.core.db import ChunkRow
    from app.ingest.pipeline import fts_tokenize

    chunks = [ChunkRow(f"{d1}:0000", d1, 0, "", None, "第一段"), ChunkRow(f"{d1}:0001", d1, 1, "", None, "第二段")]
    db.replace_chunks(d1, chunks, [fts_tokenize(c.text) for c in chunks])
    assert db.doc_chunk_texts(d1) == ["第一段", "第二段"]


# ---------- API 层 ----------


def test_threads_post_get_roundtrip(tmp_path):
    with make_client(tmp_path) as client:
        body = _thread_body()
        resp = client.post("/api/v1/threads", json=body, headers=_auth())
        assert resp.status_code == 200 and resp.json()["ok"] is True
        got = client.get("/api/v1/threads", headers=_auth()).json()
        assert got["items"][0]["id"] == body["id"]
        assert got["items"][0]["title"] == "问题标题"
        assert got["items"][0]["blocks"][1]["srcs"][0]["ref"] == 1


def test_threads_requires_auth(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/v1/threads").status_code == 401
        assert client.post("/api/v1/threads", json=_thread_body()).status_code == 401


def test_threads_rejects_bad_id_and_blocks(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/v1/threads", json=_thread_body(tid="bad id!"), headers=_auth()
        )
        assert resp.status_code == 422
        body = _thread_body()
        body["blocks"][0]["r"] = "x"
        resp = client.post("/api/v1/threads", json=body, headers=_auth())
        assert resp.status_code == 422
        body = _thread_body()
        body["blocks"] = [{"r": "q", "t": "x" * 20001}]
        resp = client.post("/api/v1/threads", json=body, headers=_auth())
        assert resp.status_code == 422


def test_threads_delete_by_query_param(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/v1/threads", json=_thread_body(tid="t999"), headers=_auth())
        resp = client.delete("/api/v1/threads?id=t999", headers=_auth())
        assert resp.status_code == 200 and resp.json()["ok"] is True
        assert client.get("/api/v1/threads", headers=_auth()).json()["items"] == []
        # 非法 id
        assert client.delete("/api/v1/threads?id=", headers=_auth()).status_code == 422
