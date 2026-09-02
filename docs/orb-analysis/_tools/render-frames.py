# render-frames.py — 把 frames.json 渲染成透明底 PNG 序列（模拟 canvas arc 的 source-over 合成）
# 点色单一（#A5B4BF），source-over 合成退化为 alpha 通道累积：A = src + A*(1-src)
# 边缘抗锯齿：1px 线性过渡（近似浏览器 canvas arc 渲染）
import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).parent
FRAMES_DIR = HERE.parent / "frames"
FRAMES_DIR.mkdir(exist_ok=True)

data = json.loads((HERE / "frames.json").read_text())
S = data["size"]
R, G, B = (0xA5, 0xB4, 0xBF)

for f in data["frames"]:
    alpha = np.zeros((S, S), dtype=np.float32)
    for x, y, r, a in f["dots"]:
        r_out = r + 0.5
        x0, x1 = max(int(np.floor(x - r_out)), 0), min(int(np.ceil(x + r_out)) + 1, S)
        y0, y1 = max(int(np.floor(y - r_out)), 0), min(int(np.ceil(y + r_out)) + 1, S)
        if x1 <= x0 or y1 <= y0:
            continue
        xs = np.arange(x0, x1, dtype=np.float32) - x
        ys = np.arange(y0, y1, dtype=np.float32) - y
        d = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)
        cov = np.clip(r + 0.5 - d, 0.0, 1.0)  # 1px 线性边缘
        src = cov * a
        region = alpha[y0:y1, x0:x1]
        region += src * (1.0 - region)  # source-over（单色）

    img = np.zeros((S, S, 4), dtype=np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = R, G, B
    img[..., 3] = np.round(alpha * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(FRAMES_DIR / f"f{f['k']:03d}.png", optimize=True)

print(f"rendered {len(data['frames'])} frames -> {FRAMES_DIR}")
total = sum(p.stat().st_size for p in FRAMES_DIR.glob('*.png'))
print(f"total {total / 1e6:.2f} MB")
