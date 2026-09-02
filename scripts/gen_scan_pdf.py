"""生成"图片型"测试 PDF（纯图像、无文字层，模拟扫描件），用于验证 OCR 入库链路。

用法：.venv/bin/python scripts/gen_scan_pdf.py [输出路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]

LINES = [
    "青鸾计划技术方案（扫描件测试）",
    "",
    "一、项目概述",
    "青鸾计划旨在验证个人知识库对扫描版文档的 OCR 入库能力。",
    "本项目由苏晚晴负责，架构评审由沈砚与林一舟共同参与。",
    "",
    "二、关键参数",
    "音频采样率 48kHz，量化位数 24bit，输出码率 320kbps。",
    "全文共分七章，预计十一月底完成终稿评审。",
    "",
    "三、评审结论",
    "二零二六年八月三十日预评审通过，定于十月十七日进行复审。",
]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("watched/青鸾计划-扫描件.pdf")
    font_path = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
    if not font_path:
        raise SystemExit("未找到可用的中文字体")
    font = ImageFont.truetype(font_path, 34)

    w, h = 1240, 1754  # A4 @150dpi
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    y = 120
    for line in LINES:
        draw.text((90, y), line, fill="black", font=font)
        y += 64

    png = out.with_suffix(".tmp.png")
    img.save(png)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png.read_bytes())
    doc.save(out)
    png.unlink()
    print(f"已生成图片型 PDF（无文字层）: {out}")


if __name__ == "__main__":
    main()
