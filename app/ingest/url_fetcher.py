"""URL 抓取：取网页正文转 markdown，落盘为剪藏文件后走统一文件管线。

SSRF 防护：首跳与每次重定向的目标都必须解析为公网地址
（与 video_summarizer.validate_url 同一规则），个人工具如需抓内网页面
可在 .env 设 INGEST_ALLOW_PRIVATE_URLS=true 显式放行（默认拒绝）。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse

import httpx

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
_MAX_REDIRECTS = 5


class UrlFetchError(RuntimeError):
    pass


def _assert_public_url(url: str, allow_private: bool = False) -> None:
    """仅放行 http(s) 且解析结果全部为公网地址的 URL；允许时跳过内网检查。"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlFetchError("仅支持 http(s) 链接")
    host = parsed.hostname or ""
    if not host:
        raise UrlFetchError("URL 缺少主机名")
    if allow_private:
        return
    try:
        addrs = [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise UrlFetchError(f"域名解析失败: {host}") from e
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    for addr in addrs:
        if (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise UrlFetchError(
                f"拒绝非公网地址: {addr}（如确需抓取内网页面，设置 INGEST_ALLOW_PRIVATE_URLS=true）"
            )


def fetch_url(
    url: str, client: httpx.Client | None = None, allow_private: bool = False
) -> tuple[str, str]:
    """抓取并提取正文，返回 (title, markdown 正文)。手动逐跳跟随重定向，每跳校验目标。

    client 参数供测试注入 MockTransport；生产路径默认自建。
    """
    owned = client is None
    c = client or httpx.Client(
        timeout=20, headers=_HEADERS, follow_redirects=False
    )
    try:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_public_url(current, allow_private=allow_private)
            resp = c.get(current)
            if resp.is_redirect:
                loc = resp.headers.get("location", "")
                if not loc:
                    break
                current = str(httpx.URL(current).join(loc))
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if "pdf" in ctype:
                raise UrlFetchError("目标是 PDF，请下载后放入监听目录入库")
            if "html" not in ctype and "text" not in ctype and ctype:
                raise UrlFetchError(f"不支持的内容类型: {ctype}")

            from .parsers import _extract_html

            text, title = _extract_html(resp.text)
            if not text.strip():
                raise UrlFetchError("未能提取到正文（页面可能是纯脚本渲染）")
            return title or url, text
        raise UrlFetchError(f"重定向次数超过 {_MAX_REDIRECTS}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise UrlFetchError(
                "目标站点拒绝访问（403）：该内容可能需要登录态，或稍后再试"
            ) from e
        raise UrlFetchError(f"抓取失败: {e}") from e
    except httpx.HTTPError as e:
        raise UrlFetchError(f"抓取失败: {e}") from e
    finally:
        if owned:
            c.close()
