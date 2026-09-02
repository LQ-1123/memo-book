"""问答多轮 history 与元问题路由测试（离线，retriever/LLM 全 fake）。"""
import secrets

from fastapi.testclient import TestClient

from app.config import Settings
from app.ingest.retriever import SearchHit
from app.main import create_app

TOKEN = "tok-" + secrets.token_hex(8)
DEAD_QDRANT = "http://127.0.0.1:1"  # 死端口隔离真实 Qdrant（历史事故防复发）


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


def _hit(text="trait 允许抽象共用行为"):
    return SearchHit(
        chunk_id="d1:0001", doc_id="d1", text=text, heading="",
        page=1, score=0.9, title="Rust 书", path="/tmp/x.pdf",
        url=None, doc_type="pdf", source="watch",
    )


class FakeRetriever:
    def __init__(self):
        self.calls: list[str] = []

    def search(self, query, topk=None, doc_type=None, source=None):
        self.calls.append(query)
        return [_hit()]

    def context_block(self, hits):
        return "[1] Rust 书\n" + hits[0].text


class FakeLLM:
    def __init__(self, available=True):
        self.available = available
        self.calls: list[list[dict]] = []

    def complete(self, messages, temperature=0.3):
        self.calls.append(messages)
        return "这是回答 [1]"

    def stream(self, messages, temperature=0.3):
        self.calls.append(messages)
        yield "这"
        yield "是回答 [1]"


AUTH = None  # 用 TOKEN 构造


def _auth():
    return {"X-API-Key": TOKEN}


def _seed_doc(db, path, title, doc_type="md", url=None):
    doc_id = db.upsert_document_by_path(
        path=path, source="watch", url=url, title=title, doc_type=doc_type,
        sha=path + title, size=10, mtime=1.0, status="indexed",
    )
    db.set_document_status(doc_id, "indexed", chunk_count=3, title=title, mark_indexed=True)
    return doc_id


# ---------- 多轮 history ----------


def test_history_included_in_messages(tmp_path):
    with make_client(tmp_path) as client:
        retriever, llm = FakeRetriever(), FakeLLM()
        client.app.state.retriever = retriever
        client.app.state.llm = llm
        resp = client.post(
            "/api/v1/ask",
            json={
                "question": "第二个框架呢？",
                "history": [
                    {"role": "user", "content": "GUI 框架哪个好？"},
                    {"role": "assistant", "content": "推荐 A [1]"},
                ],
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        msgs = llm.calls[0]
        roles = [m["role"] for m in msgs]
        assert roles == ["system", "user", "assistant", "user"]
        assert msgs[1]["content"] == "GUI 框架哪个好？"
        assert "问题：第二个框架呢？" in msgs[-1]["content"]


def test_history_truncated_to_last_12_and_2000_chars(tmp_path):
    with make_client(tmp_path) as client:
        llm = FakeLLM()
        client.app.state.retriever = FakeRetriever()
        client.app.state.llm = llm
        history = [{"role": "user", "content": f"问题{i}"} for i in range(20)]
        history[0]["content"] = "长" * 5000
        resp = client.post(
            "/api/v1/ask", json={"question": "q", "history": history}, headers=_auth()
        )
        assert resp.status_code == 200
        msgs = llm.calls[0]
        hist = msgs[1:-1]
        assert len(hist) == 12
        assert hist[0]["content"] == "问题8"
        # 全部条目都被截到 ≤2000 字符
        assert all(len(m["content"]) <= 2000 for m in hist)


def test_history_rejects_bad_role(tmp_path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/v1/ask",
            json={"question": "q", "history": [{"role": "system", "content": "x"}]},
            headers=_auth(),
        )
        assert resp.status_code == 422


# ---------- 元问题路由 ----------


def test_meta_question_answers_from_document_table(tmp_path):
    with make_client(tmp_path) as client:
        retriever, llm = FakeRetriever(), FakeLLM()
        client.app.state.retriever = retriever
        client.app.state.llm = llm
        _seed_doc(client.app.state.db, "/tmp/a.md", "Rust 入门笔记")
        _seed_doc(client.app.state.db, "/tmp/b.pdf", "Rust 程序设计语言", doc_type="pdf")
        resp = client.post(
            "/api/v1/ask", json={"question": "知识库里有哪些文档？"}, headers=_auth()
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "Rust 入门笔记" in body["answer"] and "Rust 程序设计语言" in body["answer"]
        assert len(body["sources"]) == 2
        assert {s["title"] for s in body["sources"]} == {"Rust 入门笔记", "Rust 程序设计语言"}
        assert retriever.calls == [] and llm.calls == []  # 不触达检索与 LLM


def test_meta_question_stream_emits_sources_delta_done(tmp_path):
    with make_client(tmp_path) as client:
        _seed_doc(client.app.state.db, "/tmp/a.md", "横评笔记")
        resp = client.post(
            "/api/v1/ask",
            json={"question": "列出所有文档", "stream": True},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.text.startswith("data: ")
        assert '"type": "sources"' in resp.text
        assert '"type": "delta"' in resp.text and "横评笔记" in resp.text
        assert '"type": "done"' in resp.text


def test_normal_question_not_hijacked_by_meta(tmp_path):
    with make_client(tmp_path) as client:
        retriever, llm = FakeRetriever(), FakeLLM()
        client.app.state.retriever = retriever
        client.app.state.llm = llm
        resp = client.post(
            "/api/v1/ask", json={"question": "Rust 有哪些特性？"}, headers=_auth()
        )
        assert resp.status_code == 200
        assert retriever.calls  # 走了正常检索
        assert llm.calls


def test_meta_topic_filter(tmp_path):
    with make_client(tmp_path) as client:
        client.app.state.retriever = FakeRetriever()
        client.app.state.llm = FakeLLM()
        _seed_doc(client.app.state.db, "/tmp/a.md", "Rust 入门笔记")
        _seed_doc(client.app.state.db, "/tmp/b.md", "Python 爬虫笔记")
        resp = client.post(
            "/api/v1/ask", json={"question": "有哪些关于 Rust 的笔记？"}, headers=_auth()
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "Rust 入门笔记" in body["answer"]
        assert "Python 爬虫笔记" not in body["answer"]


def test_meta_empty_library(tmp_path):
    with make_client(tmp_path) as client:
        client.app.state.retriever = FakeRetriever()
        client.app.state.llm = FakeLLM()
        resp = client.post(
            "/api/v1/ask", json={"question": "最近入库了什么"}, headers=_auth()
        )
        assert resp.status_code == 200
        assert "没有已索引的文档" in resp.json()["answer"]
