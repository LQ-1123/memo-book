"""视频平台注册表：URL → 平台识别与元信息。

hostname 精确匹配（防 evil-douyin.com 之类仿冒域）；快手（yt-dlp 2026.08 已移除
extractor）与抖音（登录依赖浏览器 cookie，已按需砍掉）识别只为给出明确错误。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Platform:
    key: str
    label: str                       # 笔记来源行显示
    hosts: frozenset[str]
    cookie: str                      # "sessdata" | "browser" | "none"
    unsupported_reason: str = ""     # 非空 = 识别但不支持，给出原因


BILIBILI = Platform("bilibili", "B站", frozenset({
    "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
}), cookie="sessdata")
DOUYIN = Platform("douyin", "抖音", frozenset({
    "douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com",
}), cookie="none", unsupported_reason="抖音已停止支持：视频捕获目前仅支持 B 站链接")
KUAISHOU = Platform("kuaishou", "快手", frozenset({
    "kuaishou.com", "www.kuaishou.com", "v.kuaishou.com",
}), cookie="none", unsupported_reason="当前 yt-dlp 版本已移除快手支持，暂无法下载快手视频")

PLATFORMS = (BILIBILI, DOUYIN, KUAISHOU)


def detect_platform(url: str) -> Platform | None:
    """按 hostname 精确匹配返回平台；非视频平台返回 None。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    for p in PLATFORMS:
        if host in p.hosts:
            return p
    return None

