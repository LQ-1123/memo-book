"""配对接口测试：扫码链接生成、fail-closed、认证门槛。

测试 token 运行时生成，源码中不落任何凭据字面量。
"""
import secrets

from fastapi.testclient import TestClient

from app.api import pair
from app.config import Settings
from app.main import create_app

TOKEN = "tok-" + secrets.token_hex(8)
DEAD_QDRANT = "http://127.0.0.1:1"


def make_client(tmp_path, *, api_keys: str = TOKEN, app_host: str = "0.0.0.0"):
    settings = Settings(
        api_keys=api_keys,
        app_host=app_host,
        data_dir=tmp_path / "data",
        watch_dirs="",
        qdrant_url=DEAD_QDRANT,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def test_pair_url_contains_key_for_qr(tmp_path, monkeypatch):
    monkeypatch.setattr(pair, "lan_ip", lambda: "192.168.1.7")
    with make_client(tmp_path) as client:
        resp = client.get("/api/v1/pair/url", headers={"X-API-Key": TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["pairable"] is True
        assert body["lan_ip"] == "192.168.1.7"
        assert body["url"] == f"http://192.168.1.7:{body['port']}/#key={TOKEN}"


def test_pair_requires_auth(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/v1/pair/url").status_code == 401


def test_pair_refused_when_loopback_only(tmp_path):
    with make_client(tmp_path, app_host="127.0.0.1") as client:
        body = client.get("/api/v1/pair/url", headers={"X-API-Key": TOKEN}).json()
        assert body["pairable"] is False
        assert "127.0.0.1" in body["reason"]


def test_pair_refused_without_keys(tmp_path):
    """未配置 key 时认证层先行 fail-closed（503），链接无从生成。"""
    with make_client(tmp_path, api_keys="") as client:
        assert client.get("/api/v1/pair/url").status_code == 503


def test_lan_ip_returns_str_or_none(monkeypatch):
    import socket

    def boom(*_a, **_k):
        raise OSError("no route")

    monkeypatch.setattr(socket, "socket", boom)
    assert pair.lan_ip() is None
