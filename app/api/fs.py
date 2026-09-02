"""本地目录浏览（供设置页选择监听目录）：只列目录名，不读文件内容。

个人本机工具：仅返回目录名列表；路径必须绝对；继承 API key 鉴权。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from pathlib import Path

router = APIRouter()

_MAX_ENTRIES = 800


@router.get("/fs/dirs")
def list_dirs(path: str = Query(default="")):
    p = os.path.expanduser(path.strip()) if path.strip() else str(Path.home())
    if not p.startswith("/"):
        raise HTTPException(status_code=422, detail="必须是绝对路径")
    root = Path(p)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在或不可访问")
    try:
        names = []
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权访问该目录") from None
    names.sort(key=lambda s: (not s.startswith("."), s.lower()))
    return {
        "path": str(root),
        "parent": str(root.parent) if str(root) != "/" else None,
        "dirs": names[:_MAX_ENTRIES],
    }
