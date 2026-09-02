"""API Key 认证：X-API-Key 头或 Bearer Token。

本机（回环地址）请求免认证——服务器宿主上的浏览器/脚本无需配置任何口令；
局域网/远程设备必须携带 API_KEYS 之一的令牌（首次在设置页粘贴，存 localStorage）。
"""
from __future__ import annotations

import fastapi
from fastapi import Header, HTTPException, Request

from .config import Settings, get_settings


def _is_loopback(request: Request) -> bool:
    client = request.client
    return bool(client and client.host in ("127.0.0.1", "::1"))


def require_api_key(
    request: Request,
    settings: Settings = fastapi.Depends(get_settings),
    x_api_key: str | None = fastapi.Header(default=None, alias="X-API-Key"),
    authorization: str | None = fastapi.Header(default=None),
) -> str:
    if _is_loopback(request):
        return "local"
    keys = settings.api_key_set
    if not keys:
        raise HTTPException(
            status_code=503,
            detail="服务端未配置 API_KEYS，受保护接口拒绝访问",
        )
    supplied = x_api_key
    if not supplied and authorization:
        scheme, _, token = authorization.partition(" ")
        supplied = token.strip() if scheme.lower() == "bearer" else None
    if not supplied or supplied not in keys:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    return supplied
