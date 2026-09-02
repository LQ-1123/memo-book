"""SQLite 注册表：documents / chunks / chunks_fts(FTS5) / tasks / threads。事实源。

安全约定：全部 SQL 为单行字面量、全部外部输入经占位符传入；
可选过滤条件用 `? IS NULL OR` 模式，IN 集合用 json_each(?) 传参。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


def _now() -> float:
    return time.time()


@dataclass(slots=True)
class ChunkRow:
    chunk_id: str
    doc_id: str
    seq: int
    heading: str
    page: int | None
    text: str


class Database:
    """连接即开即用（WAL），写操作串行化。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL UNIQUE, url TEXT, title TEXT NOT NULL DEFAULT '', doc_type TEXT NOT NULL, hash TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', error TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, indexed_at REAL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
            c.execute("CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, seq INTEGER NOT NULL, heading TEXT NOT NULL DEFAULT '', page INTEGER, text TEXT NOT NULL, nchars INTEGER NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, chunk_id UNINDEXED)")
            c.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', error TEXT, payload TEXT NOT NULL DEFAULT '{}', doc_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            try:
                c.execute("ALTER TABLE tasks ADD COLUMN detail TEXT")  # 旧库迁移：任务阶段进度
            except sqlite3.OperationalError:
                pass  # 列已存在
            try:
                c.execute("ALTER TABLE documents ADD COLUMN summary TEXT")  # 旧库迁移：入库即消化的 LLM 摘要（JSON）
            except sqlite3.OperationalError:
                pass  # 列已存在
            c.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS quizzes (id TEXT PRIMARY KEY, doc_id TEXT REFERENCES documents(id) ON DELETE CASCADE, title TEXT NOT NULL DEFAULT '', focus TEXT, questions TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, best_score REAL, plays INTEGER NOT NULL DEFAULT 0)")
            # 旧表迁移：doc_id 曾为 NOT NULL（按文档出题时代），改为可空（主题出题不绑定文档）
            _cols = c.execute("PRAGMA table_info(quizzes)").fetchall()
            if any(r["name"] == "doc_id" and r["notnull"] for r in _cols):
                c.execute("ALTER TABLE quizzes RENAME TO quizzes_old")
                c.execute("CREATE TABLE quizzes (id TEXT PRIMARY KEY, doc_id TEXT REFERENCES documents(id) ON DELETE CASCADE, title TEXT NOT NULL DEFAULT '', focus TEXT, questions TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, best_score REAL, plays INTEGER NOT NULL DEFAULT 0)")
                c.execute("INSERT INTO quizzes(id, doc_id, title, focus, questions, count, created_at, best_score, plays) SELECT id, NULLIF(doc_id, ''), title, focus, questions, count, created_at, best_score, plays FROM quizzes_old")
                c.execute("DROP TABLE quizzes_old")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- documents ----------

    def find_by_path(self, path: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()

    def find_by_hash(self, sha: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM documents WHERE hash=? AND status='indexed' LIMIT 1", (sha,)).fetchone()

    def get_document(self, doc_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def list_documents(self, doc_type: str | None = None, source: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
        args = (doc_type, doc_type, source, source, status, status)
        with self._conn() as c:
            rows = c.execute("SELECT * FROM documents WHERE (? IS NULL OR doc_type=?) AND (? IS NULL OR source=?) AND (? IS NULL OR status=?) ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM documents WHERE (? IS NULL OR doc_type=?) AND (? IS NULL OR source=?) AND (? IS NULL OR status=?)", args).fetchone()[0]
        return rows, total

    def upsert_document_by_path(self, path: str, source: str, url: str | None, title: str, doc_type: str, sha: str, size: int, mtime: float, status: str) -> str:
        """按 path 幂等 upsert（首录或重扫），返回 doc_id。"""
        with self._write_lock, self._conn() as c:
            row = c.execute("SELECT id FROM documents WHERE path=?", (path,)).fetchone()
            if row:
                c.execute("UPDATE documents SET source=?, url=?, title=?, doc_type=?, hash=?, size=?, mtime=?, status=?, error=NULL, updated_at=? WHERE id=?", (source, url, title, doc_type, sha, size, mtime, status, _now(), row["id"]))
                return row["id"]
            doc_id = uuid.uuid4().hex
            c.execute("INSERT INTO documents(id, source, path, url, title, doc_type, hash, size, mtime, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (doc_id, source, path, url, title, doc_type, sha, size, mtime, status, _now(), _now()))
            return doc_id

    def update_document_path(self, doc_id: str, new_path: str) -> None:
        """同 hash 移动：只改路径，不重新嵌入。"""
        with self._write_lock, self._conn() as c:
            c.execute("UPDATE documents SET path=?, updated_at=? WHERE id=?", (new_path, _now(), doc_id))

    def set_document_status(self, doc_id: str, status: str, error: str | None = None, chunk_count: int | None = None, title: str | None = None, mark_indexed: bool = False) -> None:
        with self._write_lock, self._conn() as c:
            c.execute("UPDATE documents SET status=?, error=?, updated_at=?, chunk_count=COALESCE(?, chunk_count), title=COALESCE(?, title), indexed_at=COALESCE(?, indexed_at) WHERE id=?", (status, error, _now(), chunk_count, title, _now() if mark_indexed else None, doc_id))

    def delete_document(self, doc_id: str) -> None:
        with self._write_lock, self._conn() as c:
            for cid in c.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall():
                c.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (cid["chunk_id"],))
            c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    def set_document_summary(self, doc_id: str, summary: str) -> None:
        with self._write_lock, self._conn() as c:
            c.execute("UPDATE documents SET summary=?, updated_at=? WHERE id=?", (summary, _now(), doc_id))

    def rename_document(self, doc_id: str, title: str) -> bool:
        with self._write_lock, self._conn() as c:
            cur = c.execute("UPDATE documents SET title=?, updated_at=? WHERE id=?", (title, _now(), doc_id))
            return cur.rowcount > 0

    def doc_ids_missing_summary(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT id FROM documents WHERE status='indexed' AND chunk_count>0 AND summary IS NULL ORDER BY updated_at DESC").fetchall()
        return [r["id"] for r in rows]

    def doc_chunk_texts(self, doc_id: str, limit: int = 5000) -> list[str]:
        """按 seq 顺序取某文档的 chunk 正文（最多 limit 条，摘要用）。"""
        with self._conn() as c:
            rows = c.execute("SELECT text FROM chunks WHERE doc_id=? ORDER BY seq LIMIT ?", (doc_id, limit)).fetchall()
        return [r["text"] for r in rows]

    # ---------- chunks ----------

    def replace_chunks(self, doc_id: str, chunks: list[ChunkRow], fts_bodies: list[str]) -> None:
        """整体替换某文档的 chunks（含 FTS），单事务。"""
        if len(chunks) != len(fts_bodies):
            raise ValueError("chunks 与 fts_bodies 数量不一致")
        with self._write_lock, self._conn() as c:
            for row in c.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall():
                c.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (row["chunk_id"],))
            c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            for ch, body in zip(chunks, fts_bodies):
                c.execute("INSERT INTO chunks(chunk_id, doc_id, seq, heading, page, text, nchars) VALUES (?,?,?,?,?,?,?)", (ch.chunk_id, ch.doc_id, ch.seq, ch.heading, ch.page, ch.text, len(ch.text)))
                c.execute("INSERT INTO chunks_fts(body, chunk_id) VALUES (?,?)", (body, ch.chunk_id))

    def get_chunks(self, chunk_ids: Sequence[str]) -> list[ChunkRow]:
        if not chunk_ids:
            return []
        with self._conn() as c:
            rows = c.execute("SELECT * FROM chunks WHERE chunk_id IN (SELECT value FROM json_each(?))", (json.dumps(list(chunk_ids)),)).fetchall()
        by_id = {r["chunk_id"]: ChunkRow(r["chunk_id"], r["doc_id"], r["seq"], r["heading"], r["page"], r["text"]) for r in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def count_chunks(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def chunk_ids_preview(self, doc_id: str, limit: int) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT chunk_id FROM chunks WHERE doc_id=? ORDER BY seq LIMIT ?", (doc_id, limit)).fetchall()

    # ---------- FTS ----------

    def fts_search(self, tokenized_query: str, doc_ids: list[str] | None, limit: int) -> list[tuple[str, float]]:
        """返回 [(chunk_id, bm25分数)]，分数越小越相关（SQLite bm25 语义）。"""
        if not tokenized_query.strip():
            return []
        with self._conn() as c:
            if doc_ids is None:
                rows = c.execute("SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?", (tokenized_query, limit)).fetchall()
            else:
                if not doc_ids:
                    return []
                rows = c.execute("SELECT f.chunk_id, bm25(chunks_fts) AS score FROM chunks_fts f JOIN chunks ch ON ch.chunk_id = f.chunk_id WHERE chunks_fts MATCH ? AND ch.doc_id IN (SELECT value FROM json_each(?)) ORDER BY score LIMIT ?", (tokenized_query, json.dumps(doc_ids), limit)).fetchall()
        return [(r["chunk_id"], r["score"]) for r in rows]

    # ---------- tasks ----------

    def create_task(self, kind: str, payload: str) -> str:
        task_id = uuid.uuid4().hex
        with self._write_lock, self._conn() as c:
            c.execute("INSERT INTO tasks(id, kind, status, payload, created_at, updated_at) VALUES (?,?,'queued',?,?,?)", (task_id, kind, payload, _now(), _now()))
        return task_id

    def update_task(self, task_id: str, status: str, error: str | None = None, doc_id: str | None = None, detail: str | None = None) -> None:
        with self._write_lock, self._conn() as c:
            c.execute("UPDATE tasks SET status=?, error=?, doc_id=COALESCE(?, doc_id), detail=COALESCE(?, detail), updated_at=? WHERE id=?", (status, error, doc_id, detail, _now(), task_id))

    def get_task(self, task_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def list_tasks(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def fail_stuck_tasks(self) -> int:
        """服务重启后把遗留的 running/queued 任务标记为 failed（防前台永远显示生成中）。"""
        with self._write_lock, self._conn() as c:
            cur = c.execute("UPDATE tasks SET status='failed', error='服务重启，任务已中断，请重新提交', updated_at=? WHERE status IN ('running','queued')", (_now(),))
        return cur.rowcount

    # ---------- threads（对话持久化；data 存 title/ts/blocks/draft 的 JSON） ----------

    def upsert_thread(self, tid: str, data: str, updated_at: float) -> None:
        with self._write_lock, self._conn() as c:
            c.execute("INSERT INTO threads(id, data, updated_at) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at", (tid, data, updated_at))

    def list_threads(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM threads ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()

    def delete_thread(self, tid: str) -> bool:
        with self._write_lock, self._conn() as c:
            cur = c.execute("DELETE FROM threads WHERE id=?", (tid,))
        return bool(cur.rowcount)

    # ---------- quizzes（小测验） ----------

    def insert_quiz(self, doc_id: str, title: str, focus: str, questions_json: str, count: int) -> str:
        qid = uuid.uuid4().hex
        with self._write_lock, self._conn() as c:
            c.execute("INSERT INTO quizzes(id, doc_id, title, focus, questions, count, created_at, plays) VALUES (?,?,?,?,?,?,?,0)", (qid, doc_id, title, focus, questions_json, count, _now()))
        return qid

    def list_quizzes(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT q.*, d.title AS doc_title FROM quizzes q LEFT JOIN documents d ON d.id = q.doc_id ORDER BY q.created_at DESC LIMIT ?", (limit,)).fetchall()

    def get_quiz(self, qid: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT q.*, d.title AS doc_title FROM quizzes q LEFT JOIN documents d ON d.id = q.doc_id WHERE q.id=?", (qid,)).fetchone()

    def update_quiz_play(self, qid: str, score: float) -> bool:
        with self._write_lock, self._conn() as c:
            row = c.execute("SELECT best_score FROM quizzes WHERE id=?", (qid,)).fetchone()
            if not row:
                return False
            best = score if row["best_score"] is None else max(row["best_score"], score)
            c.execute("UPDATE quizzes SET plays=plays+1, best_score=? WHERE id=?", (best, qid))
        return True

    def delete_quiz(self, qid: str) -> bool:
        with self._write_lock, self._conn() as c:
            cur = c.execute("DELETE FROM quizzes WHERE id=?", (qid,))
        return bool(cur.rowcount)
