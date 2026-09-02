"""SSRF 防护测试：URL 抓取目标必须为公网地址（含重定向逐跳校验）。"""
import httpx
import pytest

from app.ingest.url_fetcher import UrlFetchError, _assert_public_url, fetch_url

BAD_URLS = [
    "http://127.0.0.1/x",
    "http://localhost/x",
    "http://192.168.1.1/admin",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data",  # 云元数据端点
    "http://[::1]/",
    "file:///etc/passwd",
    "ftp://example.com/f",
    "http://0.0.0.0/",
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_rejects_non_public_targets(url):
    with pytest.raises(UrlFetchError):
        _assert_public_url(url)


def test_accepts_public_ip_literal():
    _assert_public_url("http://93.184.216.34/")  # 不触发 DNS


def test_allow_private_flag_bypasses_guard():
    _assert_public_url("http://192.168.1.1/admin", allow_private=True)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_redirect_to_private_blocked():
    """首跳公网 → 302 跳内网：必须拒绝（经典 SSRF 绕过）。"""
    client = _mock_client(
        lambda req: httpx.Response(302, headers={"location": "http://192.168.0.1/secret"})
    )
    with pytest.raises(UrlFetchError, match="非公网地址"):
        fetch_url("http://93.184.216.34/a", client=client)


def test_redirect_chain_ok():
    """公网 → 公网重定向 → 正常返回正文。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "http://8.8.8.8/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>文章</title><body><article><p>"
            + "远程影像诊断平台介绍。" * 30
            + "</p></article></body></html>",
        )

    title, text = fetch_url("http://93.184.216.34/a", client=_mock_client(handler))
    assert title == "文章"
    assert "远程影像诊断" in text


def test_redirect_loop_capped():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(req.url)})

    with pytest.raises(UrlFetchError, match="重定向"):
        fetch_url("http://93.184.216.34/a", client=_mock_client(handler))


def test_pdf_target_rejected():
    client = _mock_client(
        lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    )
    with pytest.raises(UrlFetchError, match="PDF"):
        fetch_url("http://93.184.216.34/a.pdf", client=client)
