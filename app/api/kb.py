"""知识库管理路由：文档列表/详情/删除/重索引、URL 入库、文件上传、任务状态。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..ingest.parsers import UnsupportedTypeError, detect_type
from ..ingest.url_fetcher import UrlFetchError

log = logging.getLogger(__name__)

router = APIRouter()

_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


def _pipeline(request: Request):
    return request.app.state.pipeline


def _db(request: Request):
    return request.app.state.db


# ---------- 文档 ----------


@router.get("/documents")
def list_documents(
    request: Request,
    doc_type: str | None = None,
    source: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    rows, total = _db(request).list_documents(
        doc_type=doc_type, source=source, status=status,
        limit=min(limit, 500), offset=max(offset, 0),
    )
    return {
        "total": total,
        "items": [
            {
                "id": r["id"], "title": r["title"], "doc_type": r["doc_type"],
                "source": r["source"], "url": r["url"], "path": r["path"],
                "status": r["status"], "error": r["error"],
                "chunk_count": r["chunk_count"], "size": r["size"],
                "created_at": r["created_at"], "indexed_at": r["indexed_at"],
                "summary": r["summary"],
            }
            for r in rows
        ],
    }


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request):
    row = _db(request).get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = _db(request).get_chunks(
        [r["chunk_id"] for r in _db(request).chunk_ids_preview(doc_id, 20)]
    )
    return {
        "id": row["id"], "title": row["title"], "doc_type": row["doc_type"],
        "source": row["source"], "url": row["url"], "path": row["path"],
        "status": row["status"], "error": row["error"],
        "chunk_count": row["chunk_count"], "size": row["size"],
        "hash": row["hash"], "created_at": row["created_at"],
        "indexed_at": row["indexed_at"], "summary": row["summary"],
        "chunks_preview": [
            {"chunk_id": c.chunk_id, "seq": c.seq, "heading": c.heading,
             "page": c.page, "text": c.text[:400]}
            for c in chunks
        ],
    }


@router.get("/documents/{doc_id}/file")
def get_document_file(doc_id: str, request: Request):
    """下发原始文件供预览（PDF/HTML 原文渲染）。路径来自注册表而非用户输入。"""
    row = _db(request).get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="源文件不存在或已被移动")
    return FileResponse(path, filename=path.name)


@router.get("/documents/{doc_id}/pages")
def document_page_count(doc_id: str, request: Request):
    """PDF 总页数（按页出图预览用）。"""
    row = _db(request).get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    if row["doc_type"] != "pdf":
        raise HTTPException(status_code=400, detail="仅 PDF 支持按页预览")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="源文件不存在或已被移动")
    import fitz

    with fitz.open(path) as doc:
        return {"pages": doc.page_count}


@router.get("/documents/{doc_id}/pages/{page_no}")
def document_page_image(doc_id: str, page_no: int, request: Request):
    """按页出图（1 起始，JPEG）。路径来自注册表；页码经 FastAPI 转整再校验。"""
    row = _db(request).get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    if row["doc_type"] != "pdf":
        raise HTTPException(status_code=400, detail="仅 PDF 支持按页预览")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="源文件不存在或已被移动")
    import fitz

    with fitz.open(path) as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise HTTPException(status_code=404, detail="页码超出范围")
        pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        data = pix.tobytes("jpeg")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "immutable, max-age=31536000"},
    )


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, request: Request):
    if not _pipeline(request).delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"deleted": True, "id": doc_id}


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/documents/{doc_id}")
def rename_document(doc_id: str, body: RenameBody, request: Request):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    if not _db(request).rename_document(doc_id, title):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"renamed": True, "id": doc_id, "title": title}


@router.post("/documents/{doc_id}/reindex")
def reindex_document(doc_id: str, request: Request):
    try:
        new_id = _pipeline(request).reindex_document(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重索引失败: {e}") from e
    return {"reindexed": True, "doc_id": new_id}


# ---------- URL 入库 ----------


class UrlIngestBody(BaseModel):
    url: str = Field(min_length=8)


@router.post("/ingest/url", status_code=202)
def ingest_url(body: UrlIngestBody, request: Request):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="仅支持 http(s) URL")
    state = request.app.state
    task_id = state.db.create_task("url_ingest", json.dumps({"url": url}))

    def _job() -> None:
        try:
            state.db.update_task(task_id, "running")
            doc_id = state.pipeline.process_url(url)
            state.db.update_task(task_id, "done", doc_id=doc_id)
        except UrlFetchError as e:
            state.db.update_task(task_id, "failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("URL 入库失败")
            state.db.update_task(task_id, "failed", error=str(e)[:500])

    state.executor.submit(_job)
    return {"task_id": task_id, "status": "queued"}


@router.post("/ingest/video", status_code=202)
def ingest_video(body: UrlIngestBody, request: Request):
    """B站视频 → 字幕 → LLM 笔记 → 入库（后台任务，阶段经 detail 汇报）。"""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="仅支持 http(s) URL")
    state = request.app.state
    task_id = state.db.create_task("video_ingest", json.dumps({"url": url}))

    def _job() -> None:
        from ..ingest.video_summarizer import VideoIngestError, run_video_ingest

        try:
            state.db.update_task(task_id, "running", detail="排队")
            doc_id = run_video_ingest(
                db=state.db,
                pipeline=state.pipeline,
                llm=state.llm,
                asr=state.asr,
                cfg=state.cfg,
                settings=state.settings,
                url=url,
                progress=lambda stage: state.db.update_task(task_id, "running", detail=stage),
            )
            state.db.update_task(task_id, "done", doc_id=doc_id)
        except VideoIngestError as e:
            state.db.update_task(task_id, "failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("视频入库失败")
            state.db.update_task(task_id, "failed", error=str(e)[:500])

    state.executor.submit(_job)
    return {"task_id": task_id, "status": "queued"}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request):
    row = _db(request).get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return dict(row)


@router.get("/tasks")
def list_tasks(request: Request, limit: int = 50):
    return {"items": [dict(r) for r in _db(request).list_tasks(min(limit, 200))]}


@router.post("/ingest/upload", status_code=202)
async def ingest_upload(file: UploadFile, request: Request):
    """上传文件 → 写入监听目录 uploads/ → 目录监听自动索引（规定目录、零手工）。"""
    settings = request.app.state.settings
    from ..core.watchdirs import effective_watch_dirs

    watch_roots = effective_watch_dirs(request.app.state.cfg, settings)
    if not watch_roots:
        raise HTTPException(status_code=422, detail="服务端未配置监听目录（WATCH_DIRS）")
    name = Path(file.filename or "").name  # 去掉任何路径成分，防目录穿越
    if not name or name.startswith("."):
        raise HTTPException(status_code=422, detail="缺少合法文件名")
    try:
        detect_type(Path(name))
    except UnsupportedTypeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    data = await file.read()
    if len(data) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 50MB 上限；大文件请直接放入监听目录")
    if not data:
        raise HTTPException(status_code=422, detail="空文件")
    dest_dir = watch_roots[0] / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(data)
    log.info("上传文件已落盘 %s（%.1f KB），等待目录监听索引", dest, len(data) / 1024)
    return {"saved": str(dest), "status": "自动索引中"}


@router.post("/ingest/reconcile")
def reconcile_now(request: Request, force: bool = False):
    """手动触发全量对账；force=true 忽略未变更缓存，全部重新解析嵌入（如换模型后重建向量）。"""
    stats = _pipeline(request).reconcile(force=force)
    return {"reconciled": stats}


# ---------- 入库即消化（LLM 摘要） ----------


class DigestBody(BaseModel):
    doc_id: str = Field(min_length=1, max_length=64)


def _submit_digest(state, doc_id: str) -> str:
    from ..ingest.digest import digest_document

    task_id = state.db.create_task("digest", json.dumps({"doc_id": doc_id}))

    def _job() -> None:
        try:
            state.db.update_task(task_id, "running")
            ok = digest_document(state.llm, state.db, doc_id)
            state.db.update_task(task_id, "done" if ok else "failed",
                                 doc_id=doc_id,
                                 error=None if ok else "生成失败（LLM 不可用或内容过短）")
        except Exception as e:  # noqa: BLE001
            log.exception("摘要任务失败")
            state.db.update_task(task_id, "failed", error=str(e)[:500], doc_id=doc_id)

    state.executor.submit(_job)
    return task_id


@router.post("/documents/digest", status_code=202)
def digest_doc(body: DigestBody, request: Request):
    """为单篇文档生成/重新生成摘要（后台任务）。"""
    row = _db(request).get_document(body.doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"task_id": _submit_digest(request.app.state, body.doc_id), "status": "queued"}


@router.post("/documents/digest-missing")
def digest_missing(request: Request):
    """为所有还没有摘要的已索引文档批量生成摘要。"""
    state = request.app.state
    if not state.llm.available:
        raise HTTPException(status_code=503, detail="llm_api_key 未配置，无法生成摘要")
    ids = state.db.doc_ids_missing_summary()
    for doc_id in ids:
        _submit_digest(state, doc_id)
    return {"queued": len(ids)}
