"""视频入库测试：URL 解析 / SSRF 校验 / 字幕清洗选优 / 主流程（yt-dlp 与 LLM 全 mock）。

测试 token 与 SESSDATA 一律运行时生成，源码中不落凭据字面量。
"""
import secrets
import time
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.core.db import Database
from app.ingest import video_summarizer as vs
from app.ingest.video_summarizer import (
    VideoIngestError,
    build_article,
    extract_bvid,
    is_bilibili_url,
    resolve_url,
    run_video_ingest,
    srt_to_transcript,
    validate_url,
    write_cookie_file,
    _pick_subtitle,
    _split_transcript,
)

BV = "BV1AbCdEfGh2"
FINAL_URL = f"https://www.bilibili.com/video/{BV}/"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """固定域名解析结果：单测不依赖网络，只保留 IP 字面量的拒绝逻辑。"""

    def _fake(host, *a, **k):
        ip = "127.0.0.1" if str(host).lower() == "localhost" else "93.184.216.34"
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(vs.socket, "getaddrinfo", _fake)


# ---------- URL 解析 ----------


def test_extract_bvid_variants():
    assert extract_bvid(f"https://www.bilibili.com/video/{BV}/?p=3") == BV
    assert extract_bvid(f"https://b23.tv/{BV}") == BV
    assert extract_bvid(f"https://www.bilibili.com/video/av123?bvid={BV}") == BV
    assert extract_bvid("https://www.bilibili.com/video/av123") is None
    assert extract_bvid("https://www.bilibili.com/") is None


def test_is_bilibili_url():
    assert is_bilibili_url("https://www.bilibili.com/video/BV1AbCdEfGh2")
    assert is_bilibili_url("https://b23.tv/abcd")
    assert is_bilibili_url("https://m.bilibili.com/video/BV1AbCdEfGh2")
    assert not is_bilibili_url("https://example.com/video/BV1AbCdEfGh2")
    assert not is_bilibili_url("https://evil-bilibili.com/video/BV1AbCdEfGh2")
    assert not is_bilibili_url("https://bilibili.com.evil.io/")


# ---------- SSRF 校验 ----------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://bilibili.com/video",
        "http://localhost/x",
        "http://127.0.0.1:8790/",
        "http://192.168.1.2/x",
        "http://10.0.0.1/x",
        "http://172.16.5.5/x",
        "http://169.254.9.9/x",
        "http://224.0.0.1/x",
        "http://240.0.0.1/x",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
    ],
)
def test_validate_url_rejects_private_and_non_http(url):
    with pytest.raises(VideoIngestError):
        validate_url(url)


def test_validate_url_accepts_public_without_dns():
    assert validate_url(FINAL_URL, resolve_dns=False) == FINAL_URL


def test_validate_url_rejects_unresolvable(monkeypatch):
    def _fail(*a, **k):
        raise vs.socket.gaierror("no dns")

    monkeypatch.setattr(vs.socket, "getaddrinfo", _fail)
    with pytest.raises(VideoIngestError):
        validate_url("https://no-such-host.invalid/")


def test_resolve_url_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "b23.tv":
            return httpx.Response(302, headers={"Location": FINAL_URL})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert resolve_url("https://b23.tv/xyz", client=client) == FINAL_URL


def test_resolve_url_rejects_private_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "http://192.168.0.5/secret"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(VideoIngestError):
        resolve_url("https://b23.tv/xyz", client=client)


# ---------- 字幕清洗与选优 ----------

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
<c.color>第一句话讲清楚了混合检索的基本思路</c>

2
00:01:02,000 --> 00:01:05,000
第二句补充了向量库选型的三个关键考量点
"""


def test_srt_to_transcript_groups_and_stamps():
    out = srt_to_transcript(SAMPLE_SRT)
    lines = out.splitlines()
    assert lines[0].startswith("[00:01] ")
    assert "第一句话" in lines[0]
    assert any(l.startswith("[01:02] ") and "第二句" in l for l in lines)
    assert "-->" not in out and "<c" not in out


def test_pick_subtitle_priority(tmp_path):
    def mk(name: str) -> Path:
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        return p

    en, ai, cc = mk("sub.en.srt"), mk("sub.ai-zh.srt"), mk("sub.zh-Hans.srt")
    assert _pick_subtitle([en, ai, cc]) == cc
    assert _pick_subtitle([en, ai]) == ai
    assert _pick_subtitle([en]) == en


def test_split_transcript_sizes():
    paras = "\n".join("x" * 500 for _ in range(100))  # 50k+ 字符
    segs = _split_transcript(paras, size=24_000)
    assert len(segs) >= 2
    assert all(len(s) <= 24_000 + 501 for s in segs)


def test_write_cookie_file_format(tmp_path):
    sessdata = "sd-" + secrets.token_hex(8)
    p = write_cookie_file(tmp_path, sessdata)
    content = p.read_text(encoding="utf-8")
    assert ".bilibili.com" in content and "SESSDATA" in content
    assert sessdata in content


# ---------- 文章组装 ----------


def test_build_article_layout():
    info = {
        "title": "测试视频",
        "uploader": "测试UP",
        "duration": 75,
        "upload_date": "20240101",
    }
    out = build_article(info, "## 一句话总结\n内容", "[00:00] 你好", "AI字幕", FINAL_URL)
    assert out.startswith("# 【视频】测试视频")
    assert "UP主：测试UP" in out and "时长 01:15" in out
    assert "发布于 2024-01-01" in out
    assert f"原链接：{FINAL_URL}" in out
    assert "## 附录：完整字幕" in out and "[00:00] 你好" in out


# ---------- 主流程（全 mock） ----------


class FakeLLM:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []

    def complete(self, messages, temperature: float = 0.3) -> str:
        self.calls.append(messages[-1]["content"])
        return "## 一句话总结\n这是笔记"


class StubPipeline:
    def __init__(self) -> None:
        self.calls = []

    def ingest_path(self, path, source=None, url=None):
        self.calls.append((str(path), source, url))
        return "doc-1"


def make_env(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", watch_dirs="", _env_file=None)
    settings.ensure_dirs()
    db = Database(settings.db_path)
    llm = FakeLLM()
    pipeline = StubPipeline()
    return settings, db, llm, pipeline


def test_run_video_ingest_happy_path(tmp_path, monkeypatch):
    settings, db, llm, pipeline = make_env(tmp_path)
    monkeypatch.setattr(vs, "resolve_url", lambda url, client=None: FINAL_URL)
    monkeypatch.setattr(
        vs, "fetch_video_info",
        lambda url, cookie, browser=None: {"title": "测试视频", "uploader": "测试UP",
                                           "duration": 75, "upload_date": "20240101"},
    )

    def fake_sub(url, cookie, tmpdir, browser=None):
        p = tmpdir / "sub.ai-zh.srt"
        p.write_text(SAMPLE_SRT, encoding="utf-8")
        return p

    monkeypatch.setattr(vs, "fetch_subtitle", fake_sub)
    stages: list[str] = []

    doc = run_video_ingest(
        db=db, pipeline=pipeline, llm=llm,
        cfg={"bilibili_sessdata": ""}, settings=settings,
        url="https://b23.tv/xyz", progress=stages.append,
    )
    assert doc == "doc-1"
    clip = settings.clips_dir / f"bilibili-{BV}.md"
    content = clip.read_text(encoding="utf-8")
    assert "# 【视频】测试视频" in content and "字幕：AI字幕" in content
    assert "## 附录：完整字幕" in content and "第一句" in content
    assert pipeline.calls[0][1] == "video" and pipeline.calls[0][2] == FINAL_URL
    assert "下载字幕" in stages
    assert any("生成笔记" in s for s in stages)
    assert stages[-1] == "入库索引"
    assert any("第一句" in c for c in llm.calls)  # 字幕确实进了提示词


def test_run_video_ingest_dedup_by_bvid(tmp_path, monkeypatch):
    settings, db, llm, pipeline = make_env(tmp_path)
    clip = (settings.clips_dir / f"bilibili-{BV}.md").resolve()
    db.upsert_document_by_path(
        path=str(clip), source="video", url=FINAL_URL, title="已存在",
        doc_type="md", sha="x", size=1, mtime=1.0, status="indexed",
    )
    monkeypatch.setattr(vs, "resolve_url", lambda url, client=None: FINAL_URL)

    def _boom(*a, **k):
        raise AssertionError("已入库视频不应再次拉取")

    monkeypatch.setattr(vs, "fetch_video_info", _boom)
    doc = run_video_ingest(
        db=db, pipeline=pipeline, llm=llm,
        cfg={}, settings=settings, url=FINAL_URL,
    )
    assert doc and pipeline.calls == []


def test_run_video_ingest_no_subtitle_guides_sessdata(tmp_path, monkeypatch):
    settings, db, llm, pipeline = make_env(tmp_path)
    monkeypatch.setattr(vs, "resolve_url", lambda url, client=None: FINAL_URL)
    monkeypatch.setattr(
        vs, "fetch_video_info", lambda url, cookie, browser=None: {"title": "t", "uploader": "u", "duration": 60}
    )
    monkeypatch.setattr(vs, "fetch_subtitle", lambda url, cookie, tmpdir, browser=None: None)
    with pytest.raises(VideoIngestError) as ei:
        run_video_ingest(
            db=db, pipeline=pipeline, llm=llm,
            cfg={}, settings=settings, url=FINAL_URL,
        )
    assert "sessdata" in str(ei.value).lower()


def test_run_video_ingest_rejects_non_bilibili(tmp_path, monkeypatch):
    settings, db, llm, pipeline = make_env(tmp_path)
    monkeypatch.setattr(
        vs, "resolve_url", lambda url, client=None: "https://example.com/video/x"
    )
    with pytest.raises(VideoIngestError):
        run_video_ingest(
            db=db, pipeline=pipeline, llm=llm,
            cfg={}, settings=settings, url="https://b23.tv/xyz",
        )


def test_run_video_ingest_requires_llm(tmp_path):
    settings, db, llm, pipeline = make_env(tmp_path)
    llm.available = False
    with pytest.raises(VideoIngestError):
        run_video_ingest(
            db=db, pipeline=pipeline, llm=llm,
            cfg={}, settings=settings, url=FINAL_URL,
        )


# ---------- 任务 detail 与 API ----------


def test_task_detail_roundtrip(tmp_path):
    db = Database(tmp_path / "t.db")
    tid = db.create_task("video_ingest", "{}")
    db.update_task(tid, "running", detail="下载字幕")
    assert db.get_task(tid)["detail"] == "下载字幕"
    db.update_task(tid, "running")  # 不带 detail 时保留旧值
    assert db.get_task(tid)["detail"] == "下载字幕"


def test_api_ingest_video_lifecycle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    token = "tok-" + secrets.token_hex(8)
    settings = Settings(
        api_keys=token, data_dir=tmp_path / "data", watch_dirs="",
        qdrant_url="http://127.0.0.1:1",  # 隔离真实 Qdrant，lifespan 连接失败会被捕获
        _env_file=None,
    )
    monkeypatch.setattr(
        vs, "run_video_ingest",
        lambda **kwargs: (kwargs["progress"]("下载字幕"), "doc-9")[1],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ingest/video",
            json={"url": FINAL_URL},
            headers={"X-API-Key": token},
        )
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]
        row = {"status": "queued"}
        for _ in range(60):
            row = client.get(
                f"/api/v1/tasks/{task_id}", headers={"X-API-Key": token}
            ).json()
            if row["status"] == "done":
                break
            time.sleep(0.05)
        assert row["status"] == "done" and row["doc_id"] == "doc-9"

        bad = client.post(
            "/api/v1/ingest/video",
            json={"url": "ftp://x"},
            headers={"X-API-Key": token},
        )
        assert bad.status_code == 422


def test_api_bilibili_sessdata_masked(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    token = "tok-" + secrets.token_hex(8)
    sessdata = "sd-" + secrets.token_hex(16)
    settings = Settings(
        api_keys=token, data_dir=tmp_path / "data", watch_dirs="",
        qdrant_url="http://127.0.0.1:1",  # 隔离真实 Qdrant
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        put = client.put(
            "/api/v1/config", json={"bilibili_sessdata": sessdata},
            headers={"X-API-Key": token},
        )
        assert put.status_code == 200 and "bilibili_sessdata" in put.json()["updated"]
        got = client.get("/api/v1/config", headers={"X-API-Key": token}).json()
        assert got["bilibili_sessdata"] != sessdata
        assert "***" in got["bilibili_sessdata"]
