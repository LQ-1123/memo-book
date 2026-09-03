"""v0.10：markitdown/pymupdf4llm 转 MD + 图像理解（VLM）解析测试。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from app.ingest.parsers import UnsupportedTypeError, detect_type, parse_file


class FakeVLM:
    """测试桩：available 可控，describe_page 返回固定描述并记录调用页码。"""

    def __init__(self, available: bool = True, reply: str = "这是测试图表描述"):
        self.available = available
        self.reply = reply
        self.calls: list[int] = []

    def describe_page(self, _jpg: bytes, page_no: int) -> str:
        self.calls.append(page_no)
        return self.reply


# ---------- detect_type ----------

def test_detect_office_and_image_types(tmp_path: Path):
    assert detect_type(tmp_path / "a.docx") == "docx"
    assert detect_type(tmp_path / "a.pptx") == "pptx"
    assert detect_type(tmp_path / "a.xlsx") == "xlsx"
    assert detect_type(tmp_path / "a.xls") == "xls"  # 老格式 Excel
    assert detect_type(tmp_path / "a.png") == "image"
    assert detect_type(tmp_path / "a.jpeg") == "image"
    with pytest.raises(UnsupportedTypeError):
        detect_type(tmp_path / "a.doc")  # 旧版 doc 仍不支持


def test_detect_html_code_text_types(tmp_path: Path):
    """html / 代码 / 纯文本类型钉住（防白名单回归）。"""
    assert detect_type(tmp_path / "a.html") == "html"
    assert detect_type(tmp_path / "a.htm") == "html"
    assert detect_type(tmp_path / "a.py") == "code"
    assert detect_type(tmp_path / "a.ts") == "code"
    assert detect_type(tmp_path / "a.rs") == "code"
    assert detect_type(tmp_path / "a.json") == "code"
    assert detect_type(tmp_path / "a.yaml") == "code"
    assert detect_type(tmp_path / "a.txt") == "text"


# ---------- 最小 docx 构造 ----------

def _make_minimal_docx(p: Path, text: str) -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
        "</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr("word/document.xml", body)


def test_docx_converted_to_markdown(tmp_path: Path):
    p = tmp_path / "会议纪要.docx"
    _make_minimal_docx(p, "季度目标：完成向量检索优化")
    result = parse_file(p, ocr=None)
    assert result.doc_type == "docx"
    assert "向量检索优化" in result.blocks[0][1]


# ---------- xls / html / 代码文件 ----------

def test_xls_converted_to_markdown(tmp_path: Path):
    xlwt = pytest.importorskip("xlwt")  # 造样本用；读取侧依赖 xlrd（主依赖）
    p = tmp_path / "实验数据.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("结果")
    ws.write(0, 0, "指标")
    ws.write(0, 1, "数值")
    ws.write(1, 0, "命中率")
    ws.write(1, 1, 0.92)
    wb.save(str(p))
    result = parse_file(p, ocr=None)
    assert result.doc_type == "xls"
    assert "命中率" in result.blocks[0][1]


def test_html_file_parsed_with_title(tmp_path: Path):
    p = tmp_path / "note.html"
    p.write_text(
        "<html><head><title>归档页</title></head>"
        "<body><h1>归档页</h1><p>正文段落内容。</p></body></html>",
        encoding="utf-8",
    )
    result = parse_file(p, ocr=None)
    assert result.doc_type == "html"
    assert result.blocks and "正文段落内容" in result.blocks[0][1]


def test_code_file_parsed_as_text(tmp_path: Path):
    p = tmp_path / "util.py"
    p.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    result = parse_file(p, ocr=None)
    assert result.doc_type == "code"
    assert "def add" in result.blocks[0][1]


# ---------- PDF：pymupdf4llm → MD + 视觉理解 ----------

def _make_text_pdf(p: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Personal Library Markdown Test")
    page.insert_text((72, 130), "第二行：检索增强生成。")
    doc.save(str(p))
    doc.close()


def _make_pdf_with_image(p: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "figure page")
    # 足量正文文本，避免该页被判定为扫描页而走整页视觉转写
    page.insert_text((72, 80), "这是一份包含图表的数字版文档页面。" * 3)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8))
    pix.clear_with(120)
    page.insert_image(fitz.Rect(60, 100, 220, 260), pixmap=pix)
    doc.save(str(p))
    doc.close()


def test_pdf_parsed_via_markdown(tmp_path: Path):
    p = tmp_path / "md.pdf"
    _make_text_pdf(p)
    result = parse_file(p, ocr=None)
    assert result.doc_type == "pdf"
    assert result.blocks and "Markdown Test" in result.blocks[0][1]


def test_pdf_image_page_gets_vision_text(tmp_path: Path):
    p = tmp_path / "figure.pdf"
    _make_pdf_with_image(p)
    vlm = FakeVLM(reply="这是一张测试流程图，包含三个步骤。")
    result = parse_file(p, ocr=None, vlm=vlm)
    assert vlm.calls, "含图页面应触发视觉理解"
    joined = "\n".join(t for _, t in result.blocks)
    assert "图像理解" in joined and "流程图" in joined


def test_pdf_scan_page_uses_vlm_transcription(tmp_path: Path):
    # 无文本层的页（len(text)<32）+ VLM → 整页视觉转写替代 OCR
    doc = fitz.open()
    doc.new_page()
    p = tmp_path / "scan.pdf"
    doc.save(str(p))
    doc.close()
    vlm = FakeVLM(reply="扫描页整页转写内容")
    result = parse_file(p, ocr=None, vlm=vlm)
    assert result.blocks and result.blocks[0][1] == "扫描页整页转写内容"


# ---------- 独立图片 ----------

def _make_png(p: Path) -> None:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16))
    pix.clear_with(200)
    pix.save(str(p))


def test_image_requires_vlm(tmp_path: Path):
    p = tmp_path / "pic.png"
    _make_png(p)
    with pytest.raises(UnsupportedTypeError):
        parse_file(p, ocr=None, vlm=None)


def test_image_described_by_vlm(tmp_path: Path):
    p = tmp_path / "架构图.png"
    _make_png(p)
    vlm = FakeVLM(reply="一张系统架构图，分为接入层与服务层。")
    result = parse_file(p, ocr=None, vlm=vlm)
    assert result.doc_type == "image"
    assert "架构图" in result.blocks[0][1]
    assert "【图像理解】" in result.blocks[0][1]
