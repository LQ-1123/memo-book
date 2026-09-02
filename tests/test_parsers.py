"""解析器与 RRF 融合、查询分词的单元测试（离线）。"""
from pathlib import Path

from app.ingest.parsers import UnsupportedTypeError, detect_type, parse_file
from app.ingest.pipeline import fts_tokenize
from app.ingest.retriever import rrf_fuse as real_rrf


def test_rrf_fuse_order():
    fused = real_rrf([["a", "b"], ["b", "c"]])
    # b 两路都命中 → 第一
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c"}


def test_rrf_fuse_empty():
    assert real_rrf([], ) == []
    assert real_rrf([[], []]) == []


def test_fts_tokenize_quotes_tokens():
    q = fts_tokenize('个人 OR 知识"库')
    assert '"' in q
    # 无裸 OR 运算符残留（被引号包裹）
    assert " OR " not in q.replace('" OR "', "")


def test_fts_query_uses_or_and_dedupes():
    from app.ingest.pipeline import fts_query

    q = fts_query("混合检索怎么工作")
    assert " OR " in q
    # 查询词不在文档也应命中包含其余词的文档（OR 而非 AND）
    assert q.count('"') % 2 == 0
    assert len(fts_query("检索 检索 检索").split(" OR ")) == 1  # 去重


def test_detect_type(tmp_path: Path):
    assert detect_type(tmp_path / "a.md") == "md"
    assert detect_type(tmp_path / "a.PDF") == "pdf"
    assert detect_type(tmp_path / "a.py") == "code"
    assert detect_type(tmp_path / "a.html") == "html"
    assert detect_type(tmp_path / "a.docx") == "docx"  # v0.10 起 markitdown 支持 Office
    try:
        detect_type(tmp_path / "a.doc")  # 旧版 doc 仍不支持
        raise AssertionError("应当抛出 UnsupportedTypeError")
    except UnsupportedTypeError:
        pass


def test_parse_markdown(tmp_path: Path):
    p = tmp_path / "note.md"
    p.write_text("# 我的标题\n\n正文内容", encoding="utf-8")
    result = parse_file(p, ocr=None)
    assert result.title == "我的标题"
    assert result.doc_type == "md"
    assert result.blocks[0][1].startswith("# 我的标题")


def test_parse_code_reads_gbk(tmp_path: Path):
    p = tmp_path / "legacy.py"
    p.write_bytes("# 中文注释（GBK 编码）\nprint(1)\n".encode("gbk"))
    result = parse_file(p, ocr=None)
    assert "中文注释" in result.blocks[0][1]


def test_parse_html(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(
        "<html><head><title>测试页</title></head><body><article>"
        "<h1>RAG 知识库实践指南</h1>"
        + "<p>检索增强生成是一种结合检索与生成的技术方案，" * 5
        + "</p></article></body></html>",
        encoding="utf-8",
    )
    result = parse_file(p, ocr=None)
    assert "检索增强生成" in result.blocks[0][1]
    assert result.doc_type == "html"
