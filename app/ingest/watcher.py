"""目录监听：watchdog 事件 + 防抖批量处理 + 周期全量对账双保险。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..config import Settings
from .pipeline import IngestPipeline, _is_noise
from .parsers import UnsupportedTypeError

log = logging.getLogger(__name__)

_DEBOUNCE_SEC = 1.2


class _Events(FileSystemEventHandler):
    def __init__(self, sink: queue.Queue[str]) -> None:
        self._sink = sink

    def _push(self, path: str) -> None:
        try:
            self._sink.put_nowait(path)
        except queue.Full:
            log.warning("事件队列已满，丢弃: %s", path)

    def on_created(self, e) -> None:
        if not e.is_directory:
            self._push(e.src_path)

    def on_modified(self, e) -> None:
        if not e.is_directory:
            self._push(e.src_path)

    def on_moved(self, e) -> None:
        if not e.is_directory:
            self._push(e.dest_path)  # 新路径入库；旧路径由对账清理

    def on_deleted(self, e) -> None:
        if not e.is_directory:
            self._push(e.src_path)


class WatchManager:
    def __init__(self, settings: Settings, pipeline: IngestPipeline, cfg=None) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.cfg = cfg   # 运行时配置：watch_dirs 可热改
        self._queue: queue.Queue[str] = queue.Queue(maxsize=10_000)
        self._observer = Observer()
        self._observer_started = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return bool(self._threads) and not self._stop.is_set()

    def start(self) -> None:
        from ..core.watchdirs import effective_watch_dirs

        all_dirs = effective_watch_dirs(self.cfg, self.settings)
        dirs = [d for d in all_dirs if d.exists()]
        for d in all_dirs:
            if not d.exists():
                log.error("监听目录不存在，跳过: %s", d)
        if not dirs:
            log.warning("未配置监听目录（WATCH_DIRS），仅 API/URL 入库可用")
            return
        for d in dirs:
            self._observer.schedule(_Events(self._queue), str(d), recursive=True)
        self._observer.start()
        self._observer_started = True
        self._spawn("worker", self._worker_loop)
        self._spawn("reconciler", self._reconcile_loop)
        self._spawn("initial-scan", self._initial_scan)
        log.info("目录监听已启动: %s", [str(d) for d in dirs])

    def stop(self) -> None:
        self._stop.set()
        if self._observer_started:
            self._observer.stop()
            self._observer.join(timeout=5)
        for t in self._threads:
            t.join(timeout=5)
        log.info("目录监听已停止")

    def reconfigure(self) -> None:
        """热改监听目录：停掉旧监听，按当前配置重启（含初始扫描）。"""
        self.stop()
        self._stop = threading.Event()
        self._observer = Observer()
        self._observer_started = False
        self._threads = []
        self.start()

    def _spawn(self, name: str, target) -> None:
        t = threading.Thread(target=target, name=f"watch-{name}", daemon=True)
        t.start()
        self._threads.append(t)

    def _initial_scan(self) -> None:
        try:
            stats = self.pipeline.reconcile()
            log.info("启动对账完成: %s", stats)
        except Exception:
            log.exception("启动对账失败")

    def _reconcile_loop(self) -> None:
        interval = self.settings.reconcile_interval_sec
        while not self._stop.wait(interval):
            try:
                stats = self.pipeline.reconcile()
                if stats["indexed"] or stats["removed"] or stats["failed"]:
                    log.info("周期对账: %s", stats)
            except Exception:
                log.exception("周期对账失败")

    def _worker_loop(self) -> None:
        pending: set[str] = set()
        last_event = 0.0
        while not self._stop.is_set():
            try:
                path = self._queue.get(timeout=0.5)
                pending.add(path)
                last_event = time.monotonic()
            except queue.Empty:
                pass
            if pending and time.monotonic() - last_event >= _DEBOUNCE_SEC:
                batch, pending = pending, set()
                self._process_batch(batch)

    def _process_batch(self, paths: set[str]) -> None:
        for sp in sorted(paths):
            p = Path(sp)
            if _is_noise(p):
                continue
            try:
                if p.exists():
                    self.pipeline.ingest_path(p)
                else:
                    self.pipeline.handle_deleted_path(sp)
            except UnsupportedTypeError:
                continue
            except Exception:
                log.exception("处理文件事件失败: %s", sp)
