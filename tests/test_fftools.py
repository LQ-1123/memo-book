"""fftools 测试：非冻结环境下回落 PATH；冻结环境优先捆绑目录。"""
import sys

from app.core import fftools


def test_dev_mode_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(fftools, "_bundled_dir", lambda: None)
    # CI/开发机装了 ffmpeg 则返回路径，否则 None —— 两种都必须可用
    path = fftools.ffmpeg_path()
    assert path is None or ("ffmpeg" in path)


def test_bundled_dir_preferred(monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.mkdir()
    (fake / "ffmpeg").write_text("#!/bin/sh\n")
    monkeypatch.setattr(fftools, "_bundled_dir", lambda: fake)

    # 清掉 lru_cache 再测
    fftools.ffmpeg_path.cache_clear()
    try:
        assert fftools.ffmpeg_path() == str(fake / "ffmpeg")
        assert fftools.ffmpeg_available()
        assert fftools.ffmpeg_location() == str(fake)
    finally:
        fftools.ffmpeg_path.cache_clear()


def test_bundled_dir_requires_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert fftools._bundled_dir() is None
