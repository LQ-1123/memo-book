"""程序化生成品牌图标：粒子线圈球（与 ai-orb.js 加载动画同一几何）。

品牌本体是加载球（Originkit OrbCoil：螺旋线圈 + 透视 + 深度透明层次），
logo 由其几何 1:1 移植渲染而成，保证与应用内加载动画完全同源。
合成管线：暖纸圆角底（标准版）/ 全出血（maskable 与 apple-touch，后者由 iOS
自切圆角）。maskable 图形收进中心 ~80% 安全区。

用法：.venv/bin/python scripts/gen_icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BG = (0xF6, 0xF4, 0xEF, 255)   # 暖纸（应用 --bg-frame）

TAU = math.pi * 2
PERSPECTIVE = 3.5
BASE_SPREAD = 0.3
SCALE = 2.1                    # logo 占比放大（动画默认比例适合 24px，图标需撑满）
DOT = (0xA5, 0xB4, 0xBF)       # 雾蓝（品牌色）
DOT_NEAR = (0x8B, 0x9F, 0xAD)  # 近处加深一档


def _frame(t: float, dot_n: int, turn_lo=2.0, turn_hi=8.0, fall=0.45) -> list:
    """ai-orb.js frame()：相位 t 的 3D 点列（含预旋转 spin(0.3, 0.36)）。"""
    turns = turn_lo + (turn_hi - turn_lo) * (0.5 - 0.5 * math.cos(TAU * t))
    pts = []
    for i in range(dot_n):
        u = (i / dot_n + t) % 1.0
        th = math.pi * u
        sr = math.sin(th)
        az = u * TAU * turns + TAU * t
        f = math.sin(math.pi * u) ** fall
        x, y, z = math.cos(az) * sr, math.cos(th), math.sin(az) * sr
        ca, sa = math.cos(0.3), math.sin(0.3)
        rx, rz = x * ca - z * sa, x * sa + z * ca
        co, so = math.cos(0.36), math.sin(0.36)
        y2, z2 = y * co - rz * so, y * so + rz * co
        pts.append((rx, y2, z2, 0.6 + 0.9 * f, f))
    return pts


def _project(pts: list, size: int, yaw: float, pitch: float, depth_exp=1.25) -> list:
    """ai-orb.js project()：二次旋转 + 透视 + 深度排序 → (x, y, r, alpha, z)。"""
    c = size / 2
    r_base = size * BASE_SPREAD * 0.4 * SCALE
    ca, sa = math.cos(yaw), math.sin(yaw)
    co, so = math.cos(pitch), math.sin(pitch)
    out = []
    for x, y, z, qw, qa in pts:
        rx, rz = x * ca - z * sa, x * sa + z * ca
        y2, z2 = y * co - rz * so, y * so + rz * co
        s = PERSPECTIVE / (PERSPECTIVE - z2)
        f = max(0.0, min(1.0, (z2 + 1.1) / 2.2))
        out.append([
            c + rx * r_base * s, c + y2 * r_base * s,
            0.4 * SCALE + 1.6 * f * s * qw * SCALE,
            (0.07 + 0.93 * (f ** depth_exp)) * qa,
            z2,
        ])
    out.sort(key=lambda p: p[4])
    return out


def render_orb(size: int, ss: int = 3) -> Image.Image:
    """渲染一帧线圈球（品牌 logo，透明底 RGBA）。t=0.5 即呼吸最饱满的 8 圈相位。"""
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    yaw = math.radians(5.0) + TAU * 3 * 0.5
    for x, y, r, a, z in _project(_frame(0.5, 1100), S, yaw, math.radians(-45)):
        rr = r * ss
        if rr <= 0.5 or a <= 0.01:
            continue
        col = DOT_NEAR if z > 0.25 else DOT
        d.ellipse([x - rr, y - rr, x + rr, y + rr],
                  fill=col + (int(min(1.0, a * 1.6) * 255),))
    return img.resize((size, size), Image.LANCZOS)


def make_icon(size: int, maskable: bool, square: bool = False) -> Image.Image:
    orb = render_orb(1024)
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

    k = 0.86 if maskable else 0.94
    lw = int(size * k)
    orb = orb.resize((lw, lw), Image.LANCZOS)
    off = (size - lw) // 2
    out = bg.copy()
    out.alpha_composite(orb, (off, off))
    return out


def main() -> None:
    render_orb(512).save(OUT / "logo.png")   # hero / 向导用（透明底）
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
