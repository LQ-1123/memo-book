"""桌面版入口：启动服务线程 → 等就绪 → 开原生窗口（pywebview）。

双击 .app / .exe 即运行本模块（PyInstaller 打包入口）。
窗口关闭 → 服务线程退出 → 进程结束。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .desktop_bootstrap import Bootstrap, SingleInstance, default_data_dir, prepare_environment

log = logging.getLogger(__name__)

POLL_SECONDS = 60.0  # 最长等服务就绪的时间（首次启动含 OCR 等重依赖导入）


def _server_url(bs: Bootstrap, with_key: bool = False) -> str:
    url = f"http://127.0.0.1:{bs.port}/"
    if with_key and bs.generated_key:
        url += f"#key={bs.generated_key}"
    return url


def _wait_health(port: int, timeout: float = POLL_SECONDS) -> bool:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _focus_existing(data_dir: Path) -> bool:
    """已有实例在跑时，用浏览器打开它的页面并返回 True。"""
    try:
        port = json.loads((data_dir / "server.json").read_text())["port"]
    except Exception:  # noqa: BLE001
        return False
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2)
    except Exception:  # noqa: BLE001
        return False
    webbrowser.open(f"http://127.0.0.1:{port}/")
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    data_dir = default_data_dir()
    lock = SingleInstance(data_dir)
    if not lock.acquire():
        if _focus_existing(data_dir):
            log.info("已有实例在运行（端口见 server.json），已在浏览器打开")
        else:
            log.error("已有实例在运行但健康检查失败；如确认无残留进程，可删除 %s 后重试", data_dir / ".lock")
        sys.exit(0)

    bs = prepare_environment(data_dir)
    if bs.generated_key:
        log.info("首次运行：已生成 API key（%s），窗口将以该 key 自动登录", data_dir / "api_key.txt")

    import uvicorn

    from .config import get_settings
    from .main import create_app

    settings = get_settings()
    config = uvicorn.Config(
        create_app(settings),
        host=settings.app_host,
        port=bs.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()

    try:
        import webview
    except ImportError:
        # 无 GUI 依赖时的兜底：退化成浏览器模式，服务照常可用
        log.warning("未安装 pywebview，退化为浏览器模式")
        if _wait_health(bs.port):
            webbrowser.open(_server_url(bs, with_key=True))
            try:
                thread.join()
            except KeyboardInterrupt:
                pass
        lock.release()
        return

    if not thread.is_alive():
        # bind 失败等致命错误会让 uvicorn 线程立即退出；此时 8790 上的 health
        # 可能来自别的服务，绝不能把窗口开过去
        log.error("服务线程启动失败（端口 %s 被占用？），退出", bs.port)
        lock.release()
        sys.exit(1)

    if not _wait_health(bs.port):
        log.error("服务在 %.0f 秒内未就绪，退出", POLL_SECONDS)
        lock.release()
        sys.exit(1)

    webview.create_window(
        "Memo Book",
        _server_url(bs, with_key=True),
        width=1360,
        height=900,
        min_size=(960, 640),
    )
    webview.start()  # 阻塞至窗口关闭

    server.should_exit = True
    thread.join(timeout=5)
    lock.release()


if __name__ == "__main__":
    main()
