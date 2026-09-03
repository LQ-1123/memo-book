"""并发入库测试：多线程 ingest_many 的正确性与统计。"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.core.db import Database
from app.ingest.pipeline import IngestPipeline


class FakeStore:
    """向量库桩：记录 upsert 次数，验证并发下写入调用完整。"""

    def __init__(self) -> None:
        self.upserts = 0
        self.deletes = 0

    def ensure_collection(self) -> None:
        pass

    def delete_doc(self, doc_id: str) -> None:
        self.deletes += 1

    def upsert(self, doc_id: str, chunk_ids: list[str], vectors: list[list[float]]) -> None:
        self.upserts += 1
        assert vectors and all(len(v) == 8 for v in vectors)


class FakeEmbedder:
    available = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 轻微耗时放大并发窗口；返回固定维度向量
        import time
        time.sleep(0.01)
        return [[0.1] * 8 for _ in texts]


def _make_pipeline(tmp_path: Path, workers: int = 4) -> tuple[IngestPipeline, Database, FakeStore]:
    db = Database(tmp_path / "test.db")
    store = FakeStore()
    settings = Settings(
        data_dir=tmp_path / "data", watch_dirs="", ingest_workers=workers,
        qdrant_url="http://127.0.0.1:1", _env_file=None,  # type: ignore[call-arg]
    )
    pipeline = IngestPipeline(db, store, FakeEmbedder(), None, settings, None, None)
    return pipeline, db, store


def test_ingest_many_concurrent_indexes_all(tmp_path: Path):
    pipeline, db, store = _make_pipeline(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for i in range(24):
        f = src / f"note{i}.txt"
        f.write_text(f"并发入库测试文档 {i}：" + "内容。" * 50, encoding="utf-8")
        files.append(f)

    stats = pipeline.ingest_many(files)

    assert stats == {"indexed": 24, "failed": 0}
    assert store.upserts == 24
    rows, total = db.list_documents(source="watch", limit=100)
    assert total == 24
    assert all(r["status"] == "indexed" and r["chunk_count"] >= 1 for r in rows)


def test_ingest_many_workers1_same_result(tmp_path: Path):
    pipeline, db, _ = _make_pipeline(tmp_path, workers=1)
    files = []
    for i in range(6):
        f = tmp_path / f"s{i}.py"
        f.write_text(f"# file {i}\nx = {i}\n", encoding="utf-8")
        files.append(f)
    stats = pipeline.ingest_many(files, workers=1)
    assert stats == {"indexed": 6, "failed": 0}


def test_ingest_many_skips_unsupported_silently(tmp_path: Path):
    pipeline, db, _ = _make_pipeline(tmp_path)
    good = tmp_path / "good.md"
    good.write_text("# 标题\n内容", encoding="utf-8")
    bad = tmp_path / "binary.xyz"  # 不支持的扩展名
    bad.write_bytes(b"\x00\x01")
    stats = pipeline.ingest_many([bad, good])
    assert stats == {"indexed": 1, "failed": 0}


def test_reconcile_uses_concurrent_path(tmp_path: Path):
    pipeline, db, store = _make_pipeline(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}.txt").write_text(f"内容 {i}", encoding="utf-8")

    settings2 = pipeline.settings
    settings2.watch_dirs = str(src)
    stats = pipeline.reconcile()
    assert stats["indexed"] == 5 and stats["failed"] == 0

    # 删除一个源文件 → 对账同步移除
    (src / "f0.txt").unlink()
    stats2 = pipeline.reconcile()
    assert stats2["removed"] == 1
    _, total = db.list_documents(source="watch", limit=100)
    assert total == 4
