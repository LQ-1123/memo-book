"""入库管线：文件 → 解析 → 分块 → (FTS + 嵌入向量) → 索引，及删除/重索引/对账。

统一入口：一切文件（含 URL 抓取的剪藏）都经 ingest_path 入库；
无嵌入 key 时优雅降级为仅 FTS 索引，检索仍可用关键词路径。
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import jieba

from ..config import Settings
from ..core.db import ChunkRow, Database
from ..core.embeddings import EmbeddingClient
from ..core.ocr import OcrEngine
from ..core.qdrant_store import VectorStore
from . import chunking
from .parsers import UnsupportedTypeError, detect_type, parse_file

log = logging.getLogger(__name__)

# 递归扫描时整树跳过的目录名：构建产物 / 依赖 / IDE（与逐层 .gitignore 互补，见 iter_project_files）
_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git",
    "dist", "build", "out", "coverage", "output",
    "venv", ".venv", "target", "vendor",
    ".next", ".nuxt", ".gradle", "DerivedData", "Pods",
}
_SKIP_FILE_NAMES = {  # 锁文件等无知识价值的大文件
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "composer.lock", ".DS_Store",
}

_HIDDEN_PARTS = _SKIP_DIRS  # 旧名兼容


def _is_noise(path: Path) -> bool:
    for part in path.parts:
        if part.startswith(".") or part in _SKIP_DIRS:
            return True
    if path.suffix.lower() in {".tmp", ".swp", ".part", ".download"} or path.name.endswith("~"):
        return True
    return path.name in _SKIP_FILE_NAMES


def _rel_under(base_rel: str, rel: str) -> str:
    """rel 相对 base_rel 的路径（.gitignore 规则相对其所在目录匹配）。"""
    if not base_rel:
        return rel
    prefix = base_rel + "/"
    return rel[len(prefix):] if rel.startswith(prefix) else rel


def iter_project_files(root: Path):
    """递归产出项目内候选文件：目录级剪枝（默认排除清单）+ 逐层 .gitignore 语义。

    .gitignore 由所在目录的 spec 匹配（相对该目录），子目录继承父级规则链——
    与 git 的"规则作用于所在子树"语义一致；被忽略的目录整树剪枝不进入。
    """
    import pathspec

    root = root.resolve()
    stack: list[tuple[Path, str, list]] = [(root, "", [])]
    while stack:
        abs_dir, rel_dir, specs = stack.pop()
        gi = abs_dir / ".gitignore"
        if gi.is_file():
            try:
                lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
                specs = [*specs, (rel_dir, pathspec.GitIgnoreSpec.from_lines(lines))]
            except (OSError, ValueError):
                log.warning("无法解析 .gitignore: %s", gi)
        for entry in sorted(abs_dir.iterdir()):
            name = entry.name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if entry.is_dir():
                if name.startswith(".") or name in _SKIP_DIRS:
                    continue
                if any(spec.match_file(_rel_under(base, rel)) for base, spec in specs):
                    continue
                stack.append((entry, rel, specs))
            else:
                if name.startswith(".") or name in _SKIP_DIRS or name in _SKIP_FILE_NAMES:
                    continue
                if any(spec.match_file(_rel_under(base, rel)) for base, spec in specs):
                    continue
                yield entry


def fts_tokenize(text: str) -> str:
    """jieba 搜索级分词（索引用）；每个 token 加引号，防止 FTS MATCH 语法被内容破坏。"""
    tokens = [t.strip() for t in jieba.cut_for_search(text) if t.strip()]
    return " ".join('"' + t.replace('"', "") + '"' for t in tokens)


def fts_query(text: str) -> str:
    """查询分词：token 间用 OR 连接（隐式 AND 过严，一个词不匹配即全无结果），
    BM25 会按匹配词数量与稀有度自然排序；token 去重缩短查询。"""
    seen: dict[str, None] = {}
    for t in jieba.cut_for_search(text):
        t = t.strip()
        if t:
            seen.setdefault(t, None)
    return " OR ".join('"' + t.replace('"', "") + '"' for t in seen)


class IngestPipeline:
    def __init__(
        self,
        db: Database,
        store: VectorStore,
        embedder: EmbeddingClient,
        ocr: OcrEngine,
        settings: Settings,
        vlm=None,
        cfg=None,
        on_indexed=None,
    ) -> None:
        self.db = db
        self.store = store
        self.embedder = embedder
        self.ocr = ocr
        self.settings = settings
        self.vlm = vlm
        self.cfg = cfg
        self.on_indexed = on_indexed  # 索引完成回调（doc_id），main 里接摘要生成
        self._warned_no_embed = False
        self._pdf_lock = threading.Lock()   # PyMuPDF 非线程安全：PDF 解析串行化
        self._store_lock = threading.Lock()  # qdrant local 模式写入串行化（remote 亦无损）
        jieba.initialize()

    # ---------- 入库 ----------

    def ingest_path(self, path: Path, source: str = "watch", url: str | None = None, force: bool = False) -> str:
        """处理单个文件，返回 doc_id。幂等：未变更直接跳过。"""
        path = path.resolve()
        if _is_noise(path):
            raise UnsupportedTypeError(f"忽略噪声文件: {path.name}")
        doc_type = detect_type(path)  # 提前确定类型，注册表与解析结果一致
        try:
            stat = path.stat()
        except OSError as e:
            raise UnsupportedTypeError(f"文件不可访问: {e}") from e

        existing = self.db.find_by_path(str(path))
        if not force and existing and existing["status"] == "indexed" \
                and existing["size"] == stat.st_size and existing["mtime"] == stat.st_mtime:
            return existing["id"]  # 未变更快速路径，免去大文件重哈希

        sha = self._hash_file(path)
        same_hash = self.db.find_by_hash(sha)
        if not force and same_hash and same_hash["path"] != str(path):
            self.db.update_document_path(same_hash["id"], str(path))
            log.info("识别为移动/重命名，复用索引: %s", path.name)
            return same_hash["id"]

        doc_id = self.db.upsert_document_by_path(
            path=str(path), source=source, url=url, title=path.stem,
            doc_type=doc_type,
            sha=sha, size=stat.st_size, mtime=stat.st_mtime, status="indexing",
        )
        try:
            # PyMuPDF 非线程安全：PDF 解析在并发下需串行；其余类型（markitdown/文本/图片）可并行
            if doc_type == "pdf":
                with self._pdf_lock:
                    result = parse_file(path, self.ocr, self.vlm)
            else:
                result = parse_file(path, self.ocr, self.vlm)
            target = self.settings.chunk_target_chars
            overlap = self.settings.chunk_overlap_chars
            if result.doc_type == "md":
                drafts = chunking.chunk_markdown(result.blocks[0][1], target, overlap)
            elif result.doc_type == "pdf":
                drafts = chunking.chunk_pages(result.blocks, target, overlap)
            elif result.doc_type == "code":
                drafts = chunking.chunk_code(result.blocks[0][1], path.name, target)
            elif result.doc_type in ("docx", "pptx", "xlsx", "image"):
                # markitdown/VLM 产出整篇 Markdown，按 MD 规则分块（标题感知）
                drafts = chunking.chunk_markdown(result.blocks[0][1], target, overlap)
            else:
                drafts = [
                    chunking.ChunkDraft(body=b)
                    for b in chunking.merge_blocks(
                        [t for _, t in result.blocks], target, overlap
                    )
                ]

            chunk_rows: list[ChunkRow] = []
            fts_bodies: list[str] = []
            embed_texts: list[str] = []
            for seq, d in enumerate(drafts):
                cid = f"{doc_id}:{seq:04d}"
                chunk_rows.append(ChunkRow(cid, doc_id, seq, d.prefix, d.page, d.body))
                fts_bodies.append(fts_tokenize(f"{d.prefix} {d.body}"))
                embed_texts.append(f"{d.prefix}\n{d.body}" if d.prefix else d.body)

            vectors: list[list[float]] | None = None
            if self.embedder.available and embed_texts:
                try:
                    vectors = self.embedder.embed(embed_texts)
                except Exception as e:
                    log.error("嵌入失败（%s），本文档仅入 FTS 索引: %s", path.name, e)
            if vectors:
                with self._store_lock:
                    self.store.delete_doc(doc_id)
                    self.store.upsert(doc_id, [c.chunk_id for c in chunk_rows], vectors)

            self.db.replace_chunks(doc_id, chunk_rows, fts_bodies)
            self.db.set_document_status(
                doc_id, "indexed", chunk_count=len(chunk_rows),
                title=result.title, mark_indexed=True,
            )
            log.info("已索引 %s（%d 块%s）", path.name, len(chunk_rows),
                     "，含向量" if vectors else "，仅关键词")
            if self.on_indexed is not None:
                try:
                    self.on_indexed(doc_id)
                except Exception:  # noqa: BLE001
                    log.exception("索引后回调失败（不影响入库）")
            return doc_id
        except UnsupportedTypeError:
            raise
        except Exception as e:
            log.exception("入库失败 %s", path)
            self.db.set_document_status(doc_id, "failed", error=str(e)[:500])
            raise

    def ingest_many(self, paths: list[Path], source: str = "watch", force: bool = False,
                    workers: int | None = None) -> dict[str, int]:
        """并发入库多个文件，返回 {indexed, failed}（不支持的类型静默跳过）。

        瓶颈在嵌入 API 的网络等待，线程池即可显著提速；PDF 解析与向量写入
        内部有锁串行化，线程安全。
        """
        stats = {"indexed": 0, "failed": 0}
        if not paths:
            return stats
        n = max(1, int(workers or getattr(self.settings, "ingest_workers", 4)))
        n = min(n, len(paths))
        if n <= 1:
            for p in paths:
                try:
                    self.ingest_path(p, source=source, force=force)
                    stats["indexed"] += 1
                except UnsupportedTypeError:
                    pass
                except Exception:
                    stats["failed"] += 1
            return stats
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="ingest") as ex:
            futs = [ex.submit(self.ingest_path, p, source, None, force) for p in paths]
            for fut in as_completed(futs):
                try:
                    fut.result()
                    stats["indexed"] += 1
                except UnsupportedTypeError:
                    pass
                except Exception:
                    stats["failed"] += 1
        return stats

    def process_url(self, url: str) -> str:
        """抓取 URL 正文，落盘为剪藏 md，走统一文件管线。返回 doc_id。"""
        from .url_fetcher import fetch_url

        title, text = fetch_url(
            url, allow_private=self.settings.ingest_allow_private_urls
        )
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        clip = self.settings.clips_dir / f"{digest}.md"
        content = f"# {title}\n\n> 来源：{url}\n\n{text}\n"
        if clip.exists() and clip.read_text(encoding="utf-8") == content:
            pass  # 内容未变，仅触发幂等检查
        else:
            clip.write_text(content, encoding="utf-8")
        return self.ingest_path(clip, source="url", url=url)

    # ---------- 删除 / 重索引 ----------

    def delete_document(self, doc_id: str) -> bool:
        row = self.db.get_document(doc_id)
        if not row:
            return False
        try:
            self.store.delete_doc(doc_id)
        except Exception as e:
            log.warning("删除向量失败（继续删注册表）: %s", e)
        self.db.delete_document(doc_id)
        if row["source"] in ("url", "video") and row["path"]:
            clip = Path(row["path"])
            if clip.parent == self.settings.clips_dir.resolve():
                clip.unlink(missing_ok=True)
        log.info("已删除文档 %s", doc_id)
        return True

    def handle_deleted_path(self, path: str) -> None:
        row = self.db.find_by_path(path)
        if row:
            self.delete_document(row["id"])
            log.info("文件已删除，索引同步移除: %s", path)

    def reindex_document(self, doc_id: str) -> str:
        row = self.db.get_document(doc_id)
        if not row:
            raise ValueError("文档不存在")
        return self.ingest_path(
            Path(row["path"]), source=row["source"], url=row["url"], force=True
        )

    # ---------- 对账 ----------

    def _supported_file(self, path: Path) -> bool:
        if _is_noise(path) or not path.is_file():
            return False
        try:
            from .parsers import detect_type

            detect_type(path)
            return True
        except UnsupportedTypeError:
            return False

    def reconcile(self, force: bool = False) -> dict[str, int]:
        """全量对账：补漏入库、同步删除。force=True 忽略未变更快速路径（如换嵌入模型后重建向量）。"""
        stats = {"indexed": 0, "skipped": 0, "removed": 0, "failed": 0}
        from ..core.watchdirs import effective_watch_dirs

        watch_dirs = effective_watch_dirs(self.cfg, self.settings)
        seen: set[str] = set()
        todo: list[Path] = []
        for root in watch_dirs:
            if not root.exists():
                log.warning("监听目录不存在: %s", root)
                continue
            for path in iter_project_files(root):
                if not path.is_file() or not self._supported_file(path):
                    continue
                sp = str(path.resolve())
                seen.add(sp)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                existing = self.db.find_by_path(sp)
                if not force and existing and existing["status"] == "indexed" \
                        and existing["size"] == stat.st_size and existing["mtime"] == stat.st_mtime:
                    stats["skipped"] += 1
                    continue
                todo.append(path)
        if todo:
            result = self.ingest_many(todo, force=force)
            stats["indexed"] += result["indexed"]
            stats["failed"] += result["failed"]
        for row, _ in self._watch_docs():
            if row["path"] not in seen:
                self.handle_deleted_path(row["path"])
                stats["removed"] += 1
        return stats

    def _watch_docs(self) -> list[tuple]:
        rows, _ = self.db.list_documents(source="watch", limit=10_000_000)
        return [(r, None) for r in rows]

    @staticmethod
    def _hash_file(path: Path, buf_size: int = 1 << 20) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(buf_size):
                h.update(chunk)
        return h.hexdigest()
