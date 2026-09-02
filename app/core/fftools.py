"""ffmpeg 定位：优先用随应用捆绑的静态二进制（桌面版），否则回落 PATH。

捆绑位置：PyInstaller onedir 布局下 macOS 在 Contents/Resources/ffmpeg/，
Windows 在 _internal/ffmpeg/；开发/服务器部署时不存在，走 shutil.which。
"""
from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def _bundled_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    # PyInstaller：macOS .app 的资源在 sys._MEIPASS（Contents/Resources），
    # Windows onedir 在 executable 同级的 _internal/
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    bundled = base / "ffmpeg"
    return bundled if bundled.is_dir() else None


def ffmpeg_location() -> str | None:
    """返回可传给 yt-dlp `ffmpeg_location` 的值（目录或可执行文件路径），找不到为 None。"""
    return str(_bundled_dir()) if _bundled_dir() else None


@lru_cache
def ffmpeg_path() -> str | None:
    """返回 ffmpeg 可执行文件路径；捆绑优先，其次 PATH。"""
    bundled = _bundled_dir()
    if bundled:
        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        candidate = bundled / exe
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None
