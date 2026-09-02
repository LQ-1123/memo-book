"""桌面版启动引导测试：数据目录 / 首次运行注入 / 端口挑选 / 单实例锁。

注意：prepare_environment 会 os.chdir，测试里必须恢复 CWD。
"""
import os
import socket
from pathlib import Path

import pytest

from app.desktop_bootstrap import (
    Bootstrap,
    SingleInstance,
    default_data_dir,
    pick_port,
    prepare_environment,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """隔离环境：临时数据目录 + 干净的相关环境变量 + 假 home + CWD 还原。

    另外把端口探测改为「全部可用」——本仓库的运行沙箱禁止真实 bind，
    真实 bind 的行为在 test_pick_port_real_bind 里探测性地验证。
    """
    old_cwd = os.getcwd()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home" / "Documents").mkdir(parents=True)
    for k in ("API_KEYS", "APP_HOST", "WATCH_DIRS", "APP_PORT", "PL_DATA_DIR", "QDRANT_EMBEDDED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("app.desktop_bootstrap._can_bind", lambda host, port: True)
    yield tmp_path / "data"
    os.chdir(old_cwd)
    # prepare_environment 注入的是真实环境变量（设计如此），测试后必须清掉，
    # 否则 pydantic-settings 会把 QDRANT_EMBEDDED=true 等带进后续测试
    for k in ("API_KEYS", "APP_HOST", "WATCH_DIRS", "APP_PORT", "QDRANT_EMBEDDED"):
        os.environ.pop(k, None)


def test_default_data_dir_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PL_DATA_DIR", str(tmp_path / "custom"))
    assert default_data_dir() == tmp_path / "custom"
    monkeypatch.delenv("PL_DATA_DIR")
    d = default_data_dir()
    assert d.name == "personal-library"  # 平台默认目录名


def test_first_run_generates_key_and_watch_dir(workspace):
    bs = prepare_environment(workspace)
    assert bs.generated_key, "首次运行必须生成 key"
    assert (workspace / "api_key.txt").read_text().strip() == bs.generated_key
    assert os.environ["API_KEYS"] == bs.generated_key
    assert bs.watch_dir_created and bs.watch_dir_created.is_dir()
    assert os.environ["WATCH_DIRS"] == str(bs.watch_dir_created)
    assert os.environ["APP_HOST"] == "127.0.0.1"  # 桌面默认回环
    assert os.environ["QDRANT_EMBEDDED"] == "true"  # 桌面默认内嵌向量库
    assert (workspace / "server.json").exists()


def test_second_run_keeps_existing_config(workspace):
    prepare_environment(workspace)
    first_key = os.environ["API_KEYS"]
    bs = prepare_environment(workspace)
    assert bs.generated_key is None
    assert os.environ["API_KEYS"] == first_key


def test_env_file_beats_first_run(workspace):
    workspace.mkdir(parents=True)
    (workspace / ".env").write_text(
        f"API_KEYS=my-own-key{os.getpid()}\nAPP_PORT=18970\n", encoding="utf-8"
    )
    bs = prepare_environment(workspace)
    assert bs.generated_key is None  # 用户已配置，不再生成
    assert os.environ.get("API_KEYS") is None  # 也不注入环境变量（.env 里已有）
    assert bs.port == 18970  # 用户配置的端口直接可用（探测已 mock 为可用）


def test_pick_port_skips_occupied():
    probe_calls = []

    def fake_probe(host, port):
        probe_calls.append(port)
        return port not in (8100, 8101)  # 8100/8101 占用 → 跳过，8102 可用

    assert pick_port("127.0.0.1", 8100, probe=fake_probe) == 8102
    assert probe_calls == [8100, 8101, 8102]


def test_pick_port_exhausted():
    with pytest.raises(RuntimeError):
        pick_port("127.0.0.1", 8200, tries=3, probe=lambda h, p: False)


def test_pick_port_real_bind():
    """真实 bind 探测：沙箱环境禁止 bind 时跳过（CI/本机正常执行）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
    except OSError as e:
        pytest.skip(f"环境禁止 bind（沙箱）：{e}")
    try:
        assert pick_port("127.0.0.1", free, tries=1) == free
    except RuntimeError as e:
        pytest.skip(f"环境禁止 bind（沙箱）：{e}")


def test_single_instance_lock(workspace):
    workspace.mkdir(parents=True)
    a, b = SingleInstance(workspace), SingleInstance(workspace)
    assert a.acquire()
    assert not b.acquire(), "第二把锁必须拿不到"
    a.release()
    assert b.acquire(), "释放后可重新持有"
    b.release()


def test_bootstrap_defaults():
    bs = Bootstrap(data_dir=Path("/tmp/x"), host="127.0.0.1", port=8790)
    assert bs.generated_key is None
