"""SQLite 注册表 + FTS5 中文检索单元测试（离线）。"""
from app.core.db import ChunkRow, Database
from app.ingest.pipeline import fts_tokenize


def make_db(tmp_path):
    return Database(tmp_path / "test.db")


def test_document_upsert_idempotent(tmp_path):
    db = make_db(tmp_path)
    id1 = db.upsert_document_by_path("/a.md", "watch", None, "A", "md", "h1", 10, 1.0, "indexing")
    id2 = db.upsert_document_by_path("/a.md", "watch", None, "A", "md", "h2", 20, 2.0, "indexing")
    assert id1 == id2
    row = db.find_by_path("/a.md")
    assert row["hash"] == "h2" and row["size"] == 20


def test_chunks_replace_and_get(tmp_path):
    db = make_db(tmp_path)
    doc = db.upsert_document_by_path("/b.md", "watch", None, "B", "md", "h", 1, 1.0, "indexing")
    chunks = [ChunkRow(f"{doc}:0000", doc, 0, "标题", None, "第一段内容"), ChunkRow(f"{doc}:0001", doc, 1, "", None, "第二段内容")]
    db.replace_chunks(doc, chunks, [fts_tokenize(c.text) for c in chunks])
    got = db.get_chunks([f"{doc}:0001", f"{doc}:0000"])
    assert [g.text for g in got] == ["第二段内容", "第一段内容"]  # 按请求顺序返回
    assert db.get_document(doc)["chunk_count"] == 0  # set_document_status 才更新


def test_fts_chinese_search(tmp_path):
    db = make_db(tmp_path)
    doc = db.upsert_document_by_path("/c.md", "watch", None, "C", "md", "h", 1, 1.0, "indexing")
    texts = ["个人知识库使用向量检索", "今天天气不错适合散步"]
    chunks = [ChunkRow(f"{doc}:{i:04d}", doc, i, "", None, t) for i, t in enumerate(texts)]
    db.replace_chunks(doc, chunks, [fts_tokenize(t) for t in texts])

    hits = db.fts_search(fts_tokenize("向量检索"), None, 10)
    assert hits and hits[0][0] == f"{doc}:0000"

    hits = db.fts_search(fts_tokenize("散步"), None, 10)
    assert hits and hits[0][0] == f"{doc}:0001"

    # 过滤到不相干文档 → 无结果
    other = db.upsert_document_by_path("/d.md", "watch", None, "D", "md", "h2", 1, 1.0, "indexing")
    assert db.fts_search(fts_tokenize("向量检索"), [other], 10) == []

    # 无匹配词 → 空
    assert db.fts_search(fts_tokenize("完全无关词组"), None, 10) == []


def test_delete_document_cascades(tmp_path):
    db = make_db(tmp_path)
    doc = db.upsert_document_by_path("/e.md", "watch", None, "E", "md", "h", 1, 1.0, "indexing")
    chunks = [ChunkRow(f"{doc}:0000", doc, 0, "", None, "要被删除的内容")]
    db.replace_chunks(doc, chunks, [fts_tokenize(chunks[0].text)])
    assert db.fts_search(fts_tokenize("删除"), None, 5)
    db.delete_document(doc)
    assert db.get_document(doc) is None
    assert db.get_chunks([f"{doc}:0000"]) == []
    assert db.fts_search(fts_tokenize("删除"), None, 5) == []


def test_list_documents_filters(tmp_path):
    db = make_db(tmp_path)
    db.upsert_document_by_path("/f.md", "watch", None, "F", "md", "h", 1, 1.0, "indexed")
    db.upsert_document_by_path("/g.html", "url", "https://x.com", "G", "html", "h2", 1, 1.0, "failed")
    rows, total = db.list_documents(status="failed")
    assert total == 1 and rows[0]["doc_type"] == "html"
    rows, total = db.list_documents(source="watch", status="indexed")
    assert total == 1 and rows[0]["path"] == "/f.md"


def test_tasks_lifecycle(tmp_path):
    db = make_db(tmp_path)
    tid = db.create_task("url_ingest", '{"url": "https://x.com"}')
    db.update_task(tid, "running")
    db.update_task(tid, "done", doc_id="d1")
    row = db.get_task(tid)
    assert row["status"] == "done" and row["doc_id"] == "d1"
