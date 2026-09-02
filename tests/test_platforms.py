"""视频平台注册表测试：识别矩阵、仿冒域负例（离线）。"""
import pytest

from app.ingest.platforms import detect_platform


@pytest.mark.parametrize("url,key", [
    ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili"),
    ("https://b23.tv/abc123", "bilibili"),
    ("https://www.kuaishou.com/short-video/3x123", "kuaishou"),
])
def test_detect_platform_matrix(url, key):
    p = detect_platform(url)
    assert p is not None and p.key == key


@pytest.mark.parametrize("url", [
    "https://evil-bilibili.com/video/BV1xx",
    "https://evil-douyin.com/video/123",
    "https://douyin.com.evil.com/video/123",
    "https://youtube.com/watch?v=x",
    "https://example.com",
    "not a url",
])
def test_detect_platform_negatives(url):
    assert detect_platform(url) is None


@pytest.mark.parametrize("url", [
    "https://www.kuaishou.com/short-video/3x",
    "https://www.douyin.com/video/7345678901234567890",
    "https://v.douyin.com/iRNBhs5/",
])
def test_unsupported_platforms_have_reason(url):
    """快手（yt-dlp 移除）与抖音（已砍）识别只为给出明确错误，不做下载。"""
    p = detect_platform(url)
    assert p is not None and p.unsupported_reason
