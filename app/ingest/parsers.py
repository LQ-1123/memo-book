"""解析器：MD / PDF(→MD+视觉) / HTML / 代码 / 纯文本 / Office(markitdown→MD) / 图片(VLM 描述)。

统一产出 ParseResult(title, blocks)；blocks 为 (page|None, text) 列表，交由 chunking 分块。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.ocr import OcrEngine

log = logging.getLogger(__name__)

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".c", ".h", ".cpp",
    ".hpp", ".rs", ".rb", ".sh", ".sql", ".json", ".yaml", ".yml", ".toml",
    ".swift", ".kt", ".php", ".lua",
}
MD_EXTS = {".md", ".markdown"}
HTML_EXTS = {".html", ".htm", ".xhtml"}
TEXT_EXTS = {".txt", ".text", ".log", ".rst", ".org"}
OFFICE_EXTS = {".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx", ".xls": "xls"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

_PDF_SCAN_PAGE_MIN_CHARS = 32  # 少于此字符数的 PDF 页疑似扫描页
_TAG_RE = re.compile(r"<[^>]+>")


class UnsupportedTypeError(ValueError):
    pass


@dataclass(slots=True)
class ParseResult:
    title: str
    doc_type: str
    blocks: list[tuple[int | None, str]]


def detect_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MD_EXTS:
        return "md"
    if ext == ".pdf":
        return "pdf"
    if ext in HTML_EXTS:
        return "html"
    if ext in CODE_EXTS:
        return "code"
    if ext in TEXT_EXTS:
        return "text"
    if ext in OFFICE_EXTS:
        return OFFICE_EXTS[ext]
    if ext in IMAGE_EXTS:
        return "image"
    raise UnsupportedTypeError(f"不支持的文件类型: {ext or '(无扩展名)'}")


def _strip_md(text: str) -> str:
    """去掉标题里的 Markdown 语法（# 前缀、**、`），避免源码漏出。"""
    s = re.sub(r"^#{1,6}\s*", "", text.strip())
    return s.replace("**", "").replace("`", "").strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "big5", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _parse_markdown(path: Path) -> ParseResult:
    text = _decode(path.read_bytes())
    title = path.stem
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            cleaned = _strip_md(m.group(1))
            if cleaned:
                title = cleaned
            break
    return ParseResult(title=title, doc_type="md", blocks=[(None, text)])


def _parse_text_or_code(path: Path, doc_type: str) -> ParseResult:
    return ParseResult(title=path.stem, doc_type=doc_type, blocks=[(None, _decode(path.read_bytes()))])


def _parse_html(path: Path) -> ParseResult:
    html = _decode(path.read_bytes())
    text, title = _extract_html(html)
    return ParseResult(title=title or path.stem, doc_type="html", blocks=[(None, text)])


def _extract_html(html: str) -> tuple[str, str | None]:
    """trafilatura 提取正文（markdown 结构），失败退化为去标签文本。"""
    title: str | None = None
    try:
        from trafilatura import bare_extraction, extract

        extracted = extract(
            html, output_format="markdown", include_tables=True,
            include_formatting=True, include_comments=False,
        )
        meta = bare_extraction(html, with_metadata=True)
        if meta:
            title = getattr(meta, "title", None) or (meta.get("title") if isinstance(meta, dict) else None)
        if extracted:
            return extracted.strip(), title
    except Exception as e:
        log.warning("trafilatura 提取失败，退化为去标签: %s", e)
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"\s{2,}", "\n", text).strip()
    return text, title


def _parse_pdf(path: Path, ocr: OcrEngine, vlm=None) -> ParseResult:
    """PDF → Markdown：pymupdf4llm 按页转 MD；扫描页用视觉转写（无 VLM 时退 OCR）；
    含图片的数字页追加【图像理解】描述，使图表含义可被检索。"""
    import fitz  # PyMuPDF

    page_mds: list[str] | None = None
    try:
        import pymupdf4llm

        chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        page_mds = [(c.get("text") or "") for c in chunks]
    except Exception as e:
        log.warning("pymupdf4llm 转 MD 失败，退化为纯文本提取: %s", e)

    title = path.stem   # 以文件名显示（08-31 用户要求），不再取内容首行
    blocks: list[tuple[int | None, str]] = []
    with fitz.open(path) as doc:
        for page in doc:
            no = page.number + 1
            if page_mds and page.number < len(page_mds):
                text = page_mds[page.number].strip()
            else:
                text = page.get_text("text").strip()

            scanned = len(text) < _PDF_SCAN_PAGE_MIN_CHARS
            described = False
            if scanned and vlm is not None and getattr(vlm, "available", False):
                # 扫描/图像页：整页视觉转写（比 OCR 更能还排版与图表含义）
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                try:
                    desc = vlm.describe_page(pix.tobytes("jpeg"), no)
                    if desc.strip():
                        log.info("PDF 第 %d 页为扫描/图像页，视觉转写完成（%d 字）", no, len(desc))
                        text = desc
                        described = True
                except Exception as e:
                    log.warning("PDF 第 %d 页视觉转写失败: %s", no, e)
            if scanned and not described and ocr.available:
                pix = page.get_pixmap(dpi=200)
                ocr_text = ocr.image_png(pix.tobytes("png"))
                if ocr_text.strip():
                    log.info("PDF 第 %d 页为扫描页，OCR 完成（%d 字）", no, len(ocr_text))
                    text = ocr_text
            if (not described) and vlm is not None and getattr(vlm, "available", False) and page.get_images(full=True):
                # 数字页内嵌图片：追加图像理解，使图表含义进入索引
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                try:
                    desc = vlm.describe_page(pix.tobytes("jpeg"), no)
                    if desc.strip():
                        text = (text + f"\n\n【图像理解·第{no}页】{desc}").strip()
                        described = True
                except Exception as e:
                    log.warning("PDF 第 %d 页图像理解失败: %s", no, e)
            if not text.strip():
                continue
            blocks.append((no, text))
    return ParseResult(title=title, doc_type="pdf", blocks=blocks)


def _parse_office(path: Path, doc_type: str) -> ParseResult:
    """Office 文件经 markitdown 转 Markdown（docx/pptx/xlsx）。"""
    try:
        from markitdown import MarkItDown
    except ImportError as e:
        raise UnsupportedTypeError("解析 Office 文件需要安装 markitdown") from e
    md = (MarkItDown().convert(str(path)).text_content or "").strip()
    if not md:
        raise UnsupportedTypeError(f"未能从 {path.name} 提取到内容")
    return ParseResult(title=path.stem, doc_type=doc_type, blocks=[(None, md)])


def _parse_image(path: Path, vlm) -> ParseResult:
    """独立图片文件：VLM 生成图像理解描述入库。"""
    if vlm is None or not getattr(vlm, "available", False):
        raise UnsupportedTypeError("图片入库需要配置图像理解模型（设置 → 高级 → 图像理解模型）")
    import fitz  # PyMuPDF

    pix = fitz.Pixmap(str(path))
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    desc = vlm.describe_page(pix.tobytes("jpeg"), 1)
    if not desc.strip():
        raise UnsupportedTypeError("图像理解未返回内容")
    return ParseResult(title=path.stem, doc_type="image", blocks=[(1, f"【图像理解】{desc}")])


def parse_file(path: Path, ocr: OcrEngine, vlm=None) -> ParseResult:
    doc_type = detect_type(path)
    if doc_type == "md":
        return _parse_markdown(path)
    if doc_type == "pdf":
        return _parse_pdf(path, ocr, vlm)
    if doc_type == "html":
        return _parse_html(path)
    if doc_type == "image":
        return _parse_image(path, vlm)
    if doc_type in OFFICE_EXTS.values():
        return _parse_office(path, doc_type)
    return _parse_text_or_code(path, doc_type)
