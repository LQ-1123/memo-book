"""分块：按文档类型差异化，统一字符预算制（目标 ~1100 字符，重叠 ~150）。

ChunkDraft.prefix 为上下文前缀（标题路径 / 文件路径），嵌入时拼接、展示时不混入正文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ChunkDraft:
    prefix: str = ""
    body: str = ""
    page: int | None = None


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_BOUNDARY = re.compile(
    r"^\s*(def\s|class\s|func\s|function\s|fn\s|struct\s|impl\s|export\s|async\s+def\s)"
)
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _split_oversize(text: str, target: int, overlap: int) -> list[str]:
    """单个超长段落按字符硬切，带重叠。"""
    step = max(target - overlap, 1)
    return [text[i : i + target] for i in range(0, len(text), step)] or [""]


def merge_blocks(blocks: list[str], target: int, overlap: int) -> list[str]:
    """把段落块聚合成目标尺寸的 chunk，超长段落二次切分。"""
    chunks: list[str] = []
    buf = ""
    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        if len(block) > target:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_oversize(block, target, overlap))
            continue
        candidate = f"{buf}\n{block}" if buf else block
        if len(candidate) > target and buf:
            chunks.append(buf)
            if overlap > 0 and len(buf) > overlap:
                buf = buf[-overlap:] + "\n" + block
            else:
                buf = block
        else:
            buf = candidate
    if buf.strip():
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def chunk_markdown(text: str, target: int, overlap: int) -> list[ChunkDraft]:
    """按 ATX 标题切 section，携带标题层级路径前缀。"""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []  # (heading path, lines)
    cur_path, cur_lines = "", []
    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            if any(l.strip() for l in cur_lines):
                sections.append((cur_path, cur_lines))
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_path = " > ".join(t for _, t in stack)
            cur_lines = []
        else:
            cur_lines.append(line)
    if any(l.strip() for l in cur_lines):
        sections.append((cur_path, cur_lines))

    drafts: list[ChunkDraft] = []
    for path, lines in sections:
        blocks = _PARA_SPLIT.split("\n".join(lines))
        body_budget = max(target - len(path) - 2, target // 2)
        for body in merge_blocks(blocks, body_budget, overlap):
            drafts.append(ChunkDraft(prefix=path, body=body))
    return drafts


def chunk_pages(pages: list[tuple[int, str]], target: int, overlap: int) -> list[ChunkDraft]:
    """PDF 等按页文本分块，chunk 带页码（页内聚合，不跨页）。"""
    drafts: list[ChunkDraft] = []
    for page_no, page_text in pages:
        if not page_text.strip():
            continue
        blocks = _PARA_SPLIT.split(page_text)
        for body in merge_blocks(blocks, target, overlap):
            drafts.append(ChunkDraft(prefix="", body=body, page=page_no))
    return drafts


def chunk_code(text: str, path: str, target: int, overlap: int = 80) -> list[ChunkDraft]:
    """代码：按 def/class/函数 边界切块，超长块按行窗口切，前缀带相对路径。"""
    lines = text.splitlines()
    blocks: list[str] = []
    cur: list[str] = []
    for line in lines:
        if _CODE_BOUNDARY.match(line) and cur:
            blocks.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))

    chunks: list[str] = []
    for block in blocks:
        if len(block) <= target * 1.8:
            chunks.append(block)
        else:
            # 按行窗口切，窗口间保留 ~overlap 字符的尾部行作重叠。
            # （曾把 target//4 当"行数"回填 buf，导致内容反复重复、产出膨胀百余倍）
            buf: list[str] = []
            size = 0
            for line in block.splitlines():
                if size + len(line) > target and buf:
                    chunks.append("\n".join(buf))
                    tail: list[str] = []
                    tail_chars = 0
                    for l in reversed(buf):
                        if tail_chars + len(l) > overlap or len(tail) >= 5:
                            break
                        tail.insert(0, l)
                        tail_chars += len(l) + 1
                    buf = [*tail, line]
                    size = sum(len(x) + 1 for x in buf)
                else:
                    buf.append(line)
                    size += len(line) + 1
            if buf:
                chunks.append("\n".join(buf))
    merged = merge_blocks(chunks, target, overlap) if any(len(c) < target // 4 for c in chunks) else chunks
    return [ChunkDraft(prefix=path, body=c) for c in merged if c.strip()]
