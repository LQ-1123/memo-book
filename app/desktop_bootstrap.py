"""桌面版启动引导（纯逻辑，不 import webview，便于单测）。

职责：确定数据目录并 chdir（此后 .env / 相对路径都落在数据目录）、
首次运行自动生成 API key 与默认监听目录、挑可用端口、单实例锁。

约定：本模块只改 os.environ / 落盘标记文件，不直接构造 Settings ——
pydantic-settings 的环境变量优先级高于 .env，注入即生效。
"""
from __future__ import annotations

import os
import secrets
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

APP_NAME = "personal-library"
DEFAULT_PORT = 8790
PORT_SCAN_TRIES = 20


@dataclass
class Bootstrap:
    data_dir: Path
    host: str
    port: int
    generated_key: str | None = None  # 首次运行自动生成的 key（需带 #key= 交给前端）
    watch_dir_created: Path | None = None
    extras: dict = field(default_factory=dict)


def default_data_dir() -> Path:
    """冻结环境的数据目录：macOS ~/Library/Application Support，Windows %APPDATA%。"""
    override = os.environ.get("PL_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME}"  # linux 兜底


def _read_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：只为判断某键是否已由用户配置。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def _has_config(data_dir: Path, key: str) -> bool:
    """用户是否在任何生效位置配置过该键（环境变量或数据目录 .env）。"""
    if os.environ.get(key):
        return True
    return bool(_read_env_file(data_dir / ".env").get(key))


def prepare_environment(data_dir: Path) -> Bootstrap:
    """进入数据目录并注入首次运行默认值；返回启动信息（含生成的 key）。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)

    bs = Bootstrap(data_dir=data_dir, host="127.0.0.1", port=DEFAULT_PORT)

    # 首次运行：自动生成 API key（写盘便于查看/迁移），否则受保护接口全部拒绝
    if not _has_config(data_dir, "API_KEYS"):
        key = secrets.token_urlsafe(24)
        os.environ["API_KEYS"] = key
        keyfile = data_dir / "api_key.txt"
        if not keyfile.exists():
            keyfile.write_text(key + "\n", encoding="utf-8")
        bs.generated_key = key

    # 桌面默认只监听回环（安全）；用户显式配置 APP_HOST 才对外
    if not _has_config(data_dir, "APP_HOST"):
        os.environ["APP_HOST"] = "127.0.0.1"

    # 桌面默认用内嵌向量库（无需 Docker）；服务器形态可在 .env 显式关闭
    if not _has_config(data_dir, "QDRANT_EMBEDDED"):
        os.environ["QDRANT_EMBEDDED"] = "true"

    # 首次运行：默认监听目录（文稿下），否则 watch_dirs 为空无法入库
    if not _has_config(data_dir, "WATCH_DIRS"):
        docs = Path.home() / "Documents" / "personal-library-docs"
        try:
            docs.mkdir(parents=True, exist_ok=True)
        except OSError:
            docs = data_dir / "watched"
            docs.mkdir(parents=True, exist_ok=True)
        os.environ["WATCH_DIRS"] = str(docs)
        bs.watch_dir_created = docs

    # 端口：尊重用户配置；默认 8790 被占则向上扫描
    cfg_port = _read_env_file(data_dir / ".env").get("APP_PORT") or os.environ.get("APP_PORT")
    start = int(cfg_port) if cfg_port else DEFAULT_PORT
    bs.port = pick_port(bs.host, start)
    if not cfg_port:
        os.environ["APP_PORT"] = str(bs.port)

    # 记录端口：二次启动时用来把已有实例带到前台（浏览器）
    (data_dir / "server.json").write_text(
        f'{{"port": {bs.port}}}', encoding="utf-8"
    )
    return bs


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, start: int, tries: int = PORT_SCAN_TRIES, probe=None) -> int:
    """从 start 起找第一个可绑定的端口（首选项直接可用则原样返回）。

    probe 可注入（单测沙箱可能禁止真实 bind）。
    """
    probe = probe or _can_bind
    for port in range(start, start + tries):
        if probe(host, port):
            return port
    raise RuntimeError(f"{start}-{start + tries - 1} 端口均被占用")


class SingleInstance:
    """数据目录锁文件上的排它锁：持有失败说明已有实例在跑。"""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / ".lock"
        self._fh = None

    def acquire(self) -> bool:
        self._fh = open(self._path, "w")  # noqa: SIM115 - 生命周期与进程一致
        try:
            if fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                else:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self._fh.close()
                self._fh = None
