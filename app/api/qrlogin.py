"""B 站扫码登录：官方二维码接口（passport.bilibili.com）换取 SESSDATA 登录态。

generate 产出二维码（约 3 分钟有效期），前端轮询 poll；用户手机 B 站 App
确认后，poll 响应的 Set-Cookie 携带 SESSDATA，自动写入运行时配置
（掩码存储，与手动粘贴 Cookie 等价）。仅固定访问 passport.bilibili.com，
不拼接任何用户输入，无 SSRF 面。
"""
from __future__ import annotations

import base64
import io
import logging
import re

import httpx
import qrcode
from fastapi import APIRouter, HTTPException, Request

from ..core.runtime_config import RuntimeConfig

log = logging.getLogger(__name__)

router = APIRouter()

_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
# B站对无浏览器特征的请求返回 412 风控页，必须带 UA
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# B站二维码状态码 → (前端状态, 提示语)
QR_STATUS = {
    86101: ("waiting", "等待扫码…"),
    86090: ("scanned", "已扫码，请在手机上确认"),
    86038: ("expired", "二维码已过期，请重新点击扫码登录"),
}


def _clean_key(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", raw or "")


def _qr_data_url(content: str) -> str:
    qr = qrcode.QRCode(border=2, box_size=6)
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#37352F", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _sessdata_from(resp: httpx.Response) -> str:
    sess = resp.cookies.get("SESSDATA") or ""
    if sess:
        return sess
    for cookie in resp.headers.get_list("set-cookie"):
        m = re.search(r"SESSDATA=([^;]+)", cookie)
        if m:
            return m.group(1)
    return ""


@router.post("/bilibili/qr/start")
def qr_start():
    try:
        with httpx.Client(timeout=15, headers=_HEADERS) as client:
            resp = client.get(_GENERATE_URL)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"B站二维码服务不可达: {e}") from e
    data = {}
    try:
        data = resp.json().get("data") or {}
    except ValueError:
        pass
    url, key = data.get("url"), data.get("qrcode_key")
    if not url or not key:
        raise HTTPException(status_code=502, detail="B站二维码服务无响应")
    return {"qrcode_key": key, "image": _qr_data_url(url), "expires_in": 180}


@router.get("/bilibili/qr/poll")
def qr_poll(qrcode_key: str, request: Request):
    key = _clean_key(qrcode_key)
    if not key:
        raise HTTPException(status_code=422, detail="qrcode_key 无效")
    cfg: RuntimeConfig = request.app.state.cfg
    try:
        with httpx.Client(timeout=15, headers=_HEADERS) as client:
            resp = client.get(_POLL_URL, params={"qrcode_key": key})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"B站二维码服务不可达: {e}") from e
    try:
        body = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="B站二维码服务响应异常")
    data = body.get("data") or {}
    code = data.get("code")
    if code == 0:
        sess = _sessdata_from(resp)
        if not sess:
            raise HTTPException(status_code=502, detail="登录成功但未获取到 SESSDATA")
        cfg.update(bilibili_sessdata=sess)
        log.info("B站扫码登录成功，SESSDATA 已写入运行时配置")
        return {"status": "ok"}
    if code == 86038:
        status, msg = QR_STATUS[86038]
    else:
        status, msg = QR_STATUS.get(code, ("waiting", "等待扫码…"))
    return {"status": status, "message": msg}
