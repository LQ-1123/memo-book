"""API Key 认证：X-API-Key 头或 Bearer Token；多 key 支持多设备。"""
from __future__ import annotations

import fastapi
from fastapi import Header, HTTPException

from .config import Settings, get_settings


def require_api_key(
    settings: Settings = fastapi.Depends(get_settings),
    x_api_key: str | None = fastapi.Header(default=None, alias="X-API-Key"),
    authorization: str | None = fastapi.Header(default=None),
) -> str:
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
