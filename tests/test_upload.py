"""上传接口测试：写入监听目录 uploads/、目录穿越拒绝、类型/大小限制。"""
import io

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DEAD_QDRANT = "http://127.0.0.1:1"


def make_client(tmp_path):
    token = "tok-" + __import__("secrets").token_hex(8)
    settings = Settings(
        api_keys=token,
        data_dir=tmp_path / "data",
        watch_dirs=str(tmp_path / "watched"),
        qdrant_url=DEAD_QDRANT,
        _env_file=None,  # type: ignore[call-arg]
    )
    (tmp_path / "watched").mkdir()
    return TestClient(create_app(settings)), token, tmp_path / "watched"


def test_upload_saves_into_watch_dir(tmp_path):
    c, token, watch = make_client(tmp_path)
    with c:
        r = c.post(
            "/api/v1/ingest/upload",
            files={"file": ("我的笔记.md", io.BytesIO("# 笔记\n\n内容".encode()), "text/markdown")},
            headers={"X-API-Key": token},
        )
    assert r.status_code == 202
    saved = watch / "uploads" / "我的笔记.md"
    assert saved.read_text(encoding="utf-8").startswith("# 笔记")


def test_upload_strips_path_traversal(tmp_path):
    c, token, watch = make_client(tmp_path)
    with c:
        r = c.post(
            "/api/v1/ingest/upload",
            files={"file": ("../../evil.md", io.BytesIO(b"x"), "text/markdown")},
            headers={"X-API-Key": token},
        )
    assert r.status_code == 202
    assert (watch / "uploads" / "evil.md").exists()
    assert not (tmp_path.parent / "evil.md").exists()


def test_upload_rejects_unsupported_type(tmp_path):
    c, token, _ = make_client(tmp_path)
    with c:
        r = c.post(
            "/api/v1/ingest/upload",
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers={"X-API-Key": token},
        )
    assert r.status_code == 422


def test_upload_rejects_empty(tmp_path):
    c, token, _ = make_client(tmp_path)
    with c:
        r = c.post(
            "/api/v1/ingest/upload",
            files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
            headers={"X-API-Key": token},
        )
    assert r.status_code == 422


def test_upload_requires_auth(tmp_path):
    c, _, _ = make_client(tmp_path)
    r = c.post(
        "/api/v1/ingest/upload",
        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")},
    )
    assert r.status_code == 401
