"""B站扫码登录辅助函数测试（不发网络请求）。"""
from __future__ import annotations

import httpx

from app.api.qrlogin import QR_STATUS, _clean_key, _sessdata_from


def test_clean_key_strips_injection():
    assert _clean_key("abc123-XYZ") == "abc123-XYZ"
    assert _clean_key("abc|rm -rf/@x") == "abcrm-rfx"
    assert _clean_key("") == ""


def test_qr_status_mapping():
    assert QR_STATUS[86101][0] == "waiting"
    assert QR_STATUS[86090][0] == "scanned"
    assert QR_STATUS[86038][0] == "expired"
    assert 0 not in QR_STATUS  # 成功码走 SESSDATA 捕获分支


def test_sessdata_from_headers():
    resp = httpx.Response(
        200,
        headers=[
            ("Set-Cookie", "DedeUserID=123; Path=/"),
            ("Set-Cookie", "SESSDATA=abc%2Cdef%2Cghi; Path=/; HttpOnly"),
        ],
        request=httpx.Request("GET", "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"),
    )
    assert _sessdata_from(resp) == "abc%2Cdef%2Cghi"


def test_sessdata_from_cookies():
    resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"),
    )
    resp.cookies.set("SESSDATA", "direct")
    assert _sessdata_from(resp) == "direct"
