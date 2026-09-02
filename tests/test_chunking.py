"""分块单元测试（离线）。"""
from app.ingest.chunking import (
    ChunkDraft,
    chunk_code,
    chunk_markdown,
    chunk_pages,
    merge_blocks,
)


def test_markdown_heading_path():
    md = """# 总览
intro 段落。

## 安装
pip install 步骤说明。

### 源码安装
git clone 后本地编译。
"""
    drafts = chunk_markdown(md, target=1100, overlap=150)
    assert len(drafts) == 3
    assert drafts[0].prefix == "总览"
    assert drafts[1].prefix == "总览 > 安装"
    assert drafts[2].prefix == "总览 > 安装 > 源码安装"
    assert "pip install" in drafts[1].body


def test_markdown_long_section_splits():
    md = "# 标题\n\n" + "\n\n".join(f"段落{i} " + "内容" * 80 for i in range(10))
    drafts = chunk_markdown(md, target=500, overlap=50)
    assert len(drafts) > 1
    assert all(len(d.body) <= 500 + 60 for d in drafts)  # 允许少量超预算
    assert all(d.prefix == "标题" for d in drafts)


def test_merge_blocks_respects_target():
    blocks = [f"block{i} " + "x" * 100 for i in range(20)]
    chunks = merge_blocks(blocks, target=400, overlap=50)
    assert all(len(c) <= 400 + 120 for c in chunks)
    assert len(chunks) >= 5


def test_chunk_pages_carries_page_no():
    pages = [(1, "第一页内容 " * 50), (2, "第二页内容 " * 50)]
    drafts = chunk_pages(pages, target=300, overlap=50)
    assert drafts
    assert {d.page for d in drafts} == {1, 2}


def test_chunk_code_boundaries():
    code = "\n".join(
        [f"def fn_{i}():\n    return {i}\n# 分隔注释行内容补充长度\n" for i in range(30)]
    )
    drafts = chunk_code(code, "demo.py", target=600)
    assert len(drafts) > 1
    assert all(d.prefix == "demo.py" for d in drafts)
    assert any(d.body.startswith("def fn_0") for d in drafts[:1])


def test_chunk_draft_defaults():
    d = ChunkDraft(body="x")
    assert d.prefix == "" and d.page is None
