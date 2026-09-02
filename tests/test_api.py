"""API 层测试：认证、文档接口、检索降级路径（无需 Qdrant / API key）。

测试 token 运行时生成，源码中不落任何凭据字面量。
"""
import secrets

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN_A = "tok-" + secrets.token_hex(8)
TOKEN_B = "tok-" + secrets.token_hex(8)
# 隔离真实 Qdrant：指向死端口，lifespan 的 ensure_collection 失败会被捕获。
# （曾因缺这行，配置测试改 embed_dim 触发"维度不一致自动重建"清空了生产向量库。）
DEAD_QDRANT = "http://127.0.0.1:1"


def make_client(tmp_path, api_keys: str | None = None):
    settings = Settings(
        api_keys=f"{TOKEN_A},{TOKEN_B}" if api_keys is None else api_keys,
        data_dir=tmp_path / "data",
        watch_dirs="",
        qdrant_url=DEAD_QDRANT,
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    return TestClient(app)


def test_health_open(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["embed_configured"] is False
        assert body["watching"] is False


def test_protected_requires_valid_token(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/v1/documents").status_code == 401
        bad = {"X-API-Key": TOKEN_A + "-wrong"}
        assert client.get("/api/v1/documents", headers=bad).status_code == 401
        good = {"X-API-Key": TOKEN_B}
        resp = client.get("/api/v1/documents", headers=good)
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "items": []}


def test_bearer_auth_accepted(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.get(
            "/api/v1/documents", headers={"Authorization": f"Bearer {TOKEN_A}"}
        )
        assert resp.status_code == 200


def test_no_keys_configured_fails_closed(tmp_path):
    with make_client(tmp_path, api_keys="") as client:
        assert client.get("/api/v1/documents").status_code == 503
        assert client.get("/api/v1/health").status_code == 200


def test_search_empty_index(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.get(
            "/api/v1/search", params={"q": "任意"}, headers={"X-API-Key": TOKEN_A}
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []


def test_search_requires_query(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/v1/search", headers={"X-API-Key": TOKEN_A})
        assert resp.status_code == 422


def test_document_404(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.delete(
            "/api/v1/documents/nope", headers={"X-API-Key": TOKEN_A}
        )
        assert resp.status_code == 404


def _seed_doc(client, title="旧标题"):
    """直插一条文档记录，返回 doc_id。"""
    db = client.app.state.db
    doc_id = db.upsert_document_by_path(
        path=str(client.app.state.settings.clips_dir / f"{secrets.token_hex(4)}.md"),
        source="clip", url=None, title=title, doc_type="md",
        sha=secrets.token_hex(8), size=10, mtime=0.0, status="indexed",
    )
    return doc_id


def test_rename_document(tmp_path):
    with make_client(tmp_path) as client:
        h = {"X-API-Key": TOKEN_A}
        doc_id = _seed_doc(client)
        resp = client.patch(
            f"/api/v1/documents/{doc_id}", json={"title": "  新标题 "}, headers=h
        )
        assert resp.status_code == 200 and resp.json()["title"] == "新标题"
        doc = client.get(f"/api/v1/documents/{doc_id}", headers=h).json()
        assert doc["title"] == "新标题"


def test_rename_document_validation(tmp_path):
    with make_client(tmp_path) as client:
        h = {"X-API-Key": TOKEN_A}
        doc_id = _seed_doc(client)
        # 空串 / 纯空白
        assert client.patch(f"/api/v1/documents/{doc_id}", json={"title": "   "}, headers=h).status_code == 422
        # 不存在的文档
        assert client.patch("/api/v1/documents/nope", json={"title": "x"}, headers=h).status_code == 404


def test_ingest_url_validation(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/v1/ingest/url",
            json={"url": "ftp://x"},
            headers={"X-API-Key": TOKEN_A},
        )
        assert resp.status_code == 422


def test_ask_without_llm_key(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/v1/ask",
            json={"question": "测试"},
            headers={"X-API-Key": TOKEN_A},
        )
        # 空索引 → 直接返回无结果文案，不触达 LLM
        assert resp.status_code == 200
        assert resp.json()["sources"] == []


def test_config_roundtrip_and_masking(tmp_path):
    import json as _json

    fresh = "fresh-" + secrets.token_hex(12)
    with make_client(tmp_path) as client:
        cfg = client.get("/api/v1/config", headers={"X-API-Key": TOKEN_A}).json()
        assert "embed_api_key" in cfg and "llm_model" in cfg

        r = client.put(
            "/api/v1/config",
            json={"embed_api_key": fresh, "embed_dim": 1024},
            headers={"X-API-Key": TOKEN_A},
        )
        assert r.status_code == 200
        assert r.json()["updated"] == ["embed_api_key", "embed_dim"]

        # 健康状态立即反映，无需重启
        assert client.get("/api/v1/health").json()["embed_configured"] is True

        # key 只以掩码出现
        raw = _json.dumps(client.get("/api/v1/config", headers={"X-API-Key": TOKEN_A}).json())
        assert fresh not in raw
        cfg_embed = client.get(
            "/api/v1/config", headers={"X-API-Key": TOKEN_A}
        ).json()["embed_api_key"]
        assert cfg_embed.startswith("fre***")

    # 持久化：新实例（同 data 目录）加载后依然可用
    settings2 = Settings(
        api_keys=f"{TOKEN_A},{TOKEN_B}", data_dir=tmp_path / "data",
        watch_dirs="", qdrant_url=DEAD_QDRANT, _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings2)) as client2:
        assert client2.get("/api/v1/health").json()["embed_configured"] is True


def test_config_rejects_bad_input(tmp_path):
    with make_client(tmp_path) as client:
        h = {"X-API-Key": TOKEN_A}
        assert client.put("/api/v1/config", json={"nope": 1}, headers=h).status_code == 422
        assert client.put("/api/v1/config", json={"embed_dim": 100}, headers=h).status_code == 422
        assert client.put(
            "/api/v1/config", json={"embed_base_url": "ftp://x"}, headers=h
        ).status_code == 422


def test_static_frontend_served(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "个人知识库" in resp.text
