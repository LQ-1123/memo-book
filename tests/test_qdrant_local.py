"""Qdrant 内嵌本地模式测试：不依赖 Docker/网络，全部走 tmp 目录回环。

注意：本地模式客户端必须显式 close —— 既为释放目录锁，也避免原生存储
线程在解释器后期 GC 时崩溃（曾致全量测试在后续文件随机 SIGTRAP）。
"""
import pytest

from app.config import Settings
from app.core.qdrant_store import VectorStore, point_id

_opened = []


class _Dim4:
    """RuntimeConfig 桩：VectorStore 只读 embed_dim。"""

    def get(self, field):
        return 4


class _Dim8:
    def get(self, field):
        return 8


@pytest.fixture(autouse=True)
def _close_stores():
    yield
    while _opened:
        _opened.pop()._client.close()


def _make_store(tmp_path, cfg=None):
    s = Settings(qdrant_embedded=True, data_dir=tmp_path)
    s.ensure_dirs()
    store = VectorStore(s, cfg or _Dim4())
    _opened.append(store)
    return store


def test_embedded_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    assert store.healthy()
    store.ensure_collection()
    store.upsert("doc1", ["c1", "c2"], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
    store.upsert("doc2", ["c3"], [[0.0, 0.0, 1.0, 0.0]])

    hits = store.search([1.0, 0.0, 0.0, 0.0], limit=2)
    assert hits[0][0] == "c1"
    assert hits[0][1] > 0.9

    # doc 过滤
    hits = store.search([1.0, 0.0, 0.0, 0.0], limit=5, doc_ids=["doc2"])
    assert all(h[0] != "c1" for h in hits)
    # 空 doc_ids：直接空结果
    assert store.search([1.0, 0.0, 0.0, 0.0], limit=5, doc_ids=[]) == []

    # 删除文档
    store.delete_doc("doc1")
    hits = store.search([1.0, 0.0, 0.0, 0.0], limit=5)
    assert [h[0] for h in hits] == ["c3"]


def test_embedded_persistence_and_redim(tmp_path):
    cfg = _Dim4()
    store = _make_store(tmp_path, cfg)
    store.ensure_collection()
    store.upsert("doc1", ["c1"], [[1.0, 0, 0, 0]])
    store._client.close()  # 本地模式独占目录锁，重开前必须释放
    _opened.remove(store)

    # 同目录重新打开：向量仍在（本地模式持久化）
    store2 = _make_store(tmp_path, cfg)
    assert store2.search([1.0, 0, 0, 0], limit=1)[0][0] == "c1"
    store2._client.close()

    # 维度变化 → ensure_collection 自动重建，旧向量作废
    store3 = _make_store(tmp_path, _Dim8())
    store3.ensure_collection()
    assert store3.search([1.0] * 8, limit=1) == []
    store3._client.close()


def test_point_id_stable():
    assert point_id("abc") == point_id("abc")
    assert point_id("abc") != point_id("abd")


def test_embedded_app_end_to_end(tmp_path):
    """进程内端到端：embedded 模式下 /health 应报告 qdrant 可用（不占端口）。"""
    import secrets

    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = Settings(
        api_keys="tok-" + secrets.token_hex(8),
        data_dir=tmp_path / "data",
        watch_dirs="",
        qdrant_embedded=True,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/health").json()
        assert body["qdrant"] is True
        assert (tmp_path / "data" / "qdrant").is_dir()
        client.app.state.store._client.close()
