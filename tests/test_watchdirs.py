"""监听目录运行时热改：解析与校验测试（不发网络、不动真实目录）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.runtime_config import RuntimeConfig
from app.core.watchdirs import effective_watch_dirs


class FakeCfg:
    def __init__(self, v: str | None):
        self.v = v

    def get(self, k):
        if k == "watch_dirs":
            return self.v
        raise KeyError(k)


def test_fallback_to_env_when_unset():
    s = get_settings()
    dirs = effective_watch_dirs(FakeCfg(""), s)
    assert dirs == list(s.watch_dir_list)
    dirs2 = effective_watch_dirs(None, s)
    assert dirs2 == dirs


def test_runtime_overrides():
    s = get_settings()
    dirs = effective_watch_dirs(FakeCfg("/tmp/a\n/tmp/b"), s)
    assert dirs == [Path("/tmp/a"), Path("/tmp/b")]


def test_update_validates_absolute_and_expands_home(tmp_path: Path):
    s = get_settings()
    s.data_dir = tmp_path
    cfg = RuntimeConfig(s)
    cfg._path = tmp_path / "rc.json"   # 测试隔离：不写真实 data/runtime_config.json

    out = cfg.update(watch_dirs="~/Desktop, /tmp/x")
    assert "watch_dirs" in out
    assert cfg.get("watch_dirs") == f"{Path.home()}/Desktop\n/tmp/x"

    with pytest.raises(ValueError):
        cfg.update(watch_dirs="relative/path")
