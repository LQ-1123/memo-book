"""从用户提供的原图（app/static/icons/logo.png，254×248 雾蓝笔画图形）合成 PWA 图标。

不再重绘图形——原图直接缩放放置：白色圆角底（标准版）/ 全出血白底（maskable 与 apple-touch，
后者由 iOS 自切圆角）。maskable 图形收进中心 ~80% 安全区。

用法：.venv/bin/python scripts/gen_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
LOGO = OUT / "logo.png"
BG = (255, 255, 255, 255)   # 原图即白底


def _logo_square() -> Image.Image:
    """原图补成正方形（居中，白底），保持笔图形不变形。"""
    img = Image.open(LOGO).convert("RGBA")
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), BG)
    sq.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    return sq


def make_icon(size: int, maskable: bool, square: bool = False) -> Image.Image:
    logo = _logo_square()
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if maskable or square:
        bg = Image.new("RGBA", (size, size), BG)
    else:
        bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255
        )
        bg.paste(Image.new("RGBA", (size, size), BG), (0, 0), mask)

    k = 0.8 if maskable else 0.92
    lw = int(size * k)
    logo = logo.resize((lw, lw), Image.LANCZOS)
    off = (size - lw) // 2
    out = bg.copy()
    out.alpha_composite(logo, (off, off))
    return out


def main() -> None:
    make_icon(192, maskable=False).save(OUT / "icon-192.png")
    make_icon(512, maskable=False).save(OUT / "icon-512.png")
    make_icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
    make_icon(180, maskable=False, square=True).save(OUT / "apple-touch-icon.png")
    print(f"图标已生成到 {OUT}")
    make_desktop_icons()


def make_desktop_icons() -> None:
    """桌面版打包用图标：macOS .icns + Windows .ico（PyInstaller 引用）。"""
    res = Path(__file__).resolve().parent.parent / "resources" / "icons"
    res.mkdir(parents=True, exist_ok=True)
    icon = make_icon(1024, maskable=False, square=True)
    icon.save(
        res / "icon.ico",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    icon.save(
        res / "icon.icns",
        sizes=[(1024, 1024), (512, 512), (256, 256), (128, 128), (64, 64), (32, 32), (16, 16)],
    )
    print(f"桌面图标已生成到 {res}")


if __name__ == "__main__":
    main()
