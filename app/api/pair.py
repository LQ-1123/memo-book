"""局域网配对：给主机端生成「扫码即用」的带口令链接。

二维码内容形如 http://<局域网IP>:<端口>/#key=<key>，手机扫码打开后由前端
（app.js 的 #key= 交接）自动保存，之后免填。key 不落任何日志/文案，只进二维码。
"""
from __future__ import annotations

import socket

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings

router = APIRouter()

_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def lan_ip() -> str | None:
    """默认路由出口网卡的局域网 IP；UDP connect 只查路由表不实际发包。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        if s:
            s.close()


@router.get("/pair/url")
def pair_url(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    if settings.app_host in _LOOPBACK_HOSTS:
        return {
            "pairable": False,
            "reason": "服务只监听本机（APP_HOST=127.0.0.1），手机无法访问；改为 0.0.0.0 并重启后可配对",
        }
    key = next((k.strip() for k in settings.api_keys.split(",") if k.strip()), None)
    if not key:
        return {"pairable": False, "reason": "服务端未配置访问口令"}
    lan = lan_ip()
    if not lan:
        return {"pairable": False, "reason": "未找到局域网地址（未联网？）"}
    return {
        "pairable": True,
        "url": f"http://{lan}:{settings.app_port}/#key={key}",
        "lan_ip": lan,
        "port": settings.app_port,
    }
