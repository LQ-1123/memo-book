"""运维工具：日志轮转 / 数据备份 / launchd 服务安装。

子命令：
  install    生成并加载两个 LaunchAgent（常驻服务 + 每日维护）
  uninstall  卸载并删除 plist
  status     查看两个服务在 launchd 里的状态
  rotate     手动轮转日志
  backup     手动备份（SQLite + 运行时配置，保留最近 7 份）
  run        轮转 + 备份（维护定时器每日调用）
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "library.db"
RUNTIME_CFG = DATA_DIR / "runtime_config.json"

SERVER_LABEL = "com.personal-library.server"
MAINTAIN_LABEL = "com.personal-library.maintain"
_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_KEEP = 3
BACKUP_KEEP = 7


def _gui() -> str:
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip()
    return f"gui/{uid}"


def rotate_logs(log_dir: Path, max_bytes: int = MAX_LOG_BYTES, keep: int = LOG_KEEP) -> list[str]:
    """超过 max_bytes 的 *.log 压缩轮转为 *.1.gz，旧代数顺移并剪到 keep 份。"""
    rotated: list[str] = []
    for log in sorted(Path(log_dir).glob("*.log")):
        if log.stat().st_size < max_bytes:
            continue
        for i in range(keep - 1, 0, -1):
            src = log.with_suffix(f".log.{i}.gz")
            if src.exists():
                src.replace(log.with_suffix(f".log.{i + 1}.gz"))
        tmp = log.with_suffix(".log.1.tmp")
        with log.open("rb") as fin, gzip.open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        log.unlink()
        tmp.replace(log.with_suffix(".log.1.gz"))
        rotated.append(log.name)
    return rotated


def backup(
    db_path: Path = DB_PATH,
    backup_dir: Path = BACKUP_DIR,
    extra: tuple[Path, ...] = (),
    keep: int = BACKUP_KEEP,
    stamp: str | None = None,
) -> Path:
    """用 sqlite3 backup API 在线备份（WAL 安全），附加运行时配置，滚动保留 keep 份。"""
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(backup_dir) / f"backup-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target / db_path.name)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for f in extra:
        f = Path(f)
        if f.exists():
            shutil.copy2(f, target / f.name)
    for old in sorted(Path(backup_dir).glob("backup-*"))[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def plist_xml(
    label: str, program_args: list[str], stdout: Path, stderr: Path,
    keep_alive: bool = True, run_at_load: bool = True, calendar_hour: int | None = None,
) -> str:
    """生成 LaunchAgent plist 内容。WorkingDirectory 固定项目根（.env/data 相对路径的前提）。"""
    args = "".join(f"<string>{a}</string>" for a in program_args)
    cal = ""
    if calendar_hour is not None:
        cal = ("<key>StartCalendarInterval</key><dict><key>Hour</key>"
               f"<integer>{calendar_hour}</integer><key>Minute</key><integer>3</integer></dict>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0"><dict>'
        f"<key>Label</key><string>{label}</string>"
        f"<key>ProgramArguments</key><array>{args}</array>"
        f"<key>WorkingDirectory</key><string>{PROJECT_DIR}</string>"
        f"<key>RunAtLoad</key><{str(run_at_load).lower()}/>"
        f"<key>KeepAlive</key><{str(keep_alive).lower()}/>"
        "<key>ThrottleInterval</key><integer>10</integer>"
        f"<key>StandardOutPath</key><string>{stdout}</string>"
        f"<key>StandardErrPath</key><string>{stderr}</string>"
        f"{cal}</dict></plist>"
    )


def _bootout(label: str) -> None:
    subprocess.run(["launchctl", "bootout", f"{_gui()}/{label}"], capture_output=True)


def cmd_install() -> None:
    """生成并加载两个 LaunchAgent。幂等：已存在则先卸载再装。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    jobs = (
        (SERVER_LABEL, plist_xml(
            SERVER_LABEL, [str(VENV_PYTHON), "-m", "app.main"],
            LOG_DIR / "server.out.log", LOG_DIR / "server.err.log")),
        (MAINTAIN_LABEL, plist_xml(
            MAINTAIN_LABEL, [str(VENV_PYTHON), str(PROJECT_DIR / "scripts" / "maintain.py"), "run"],
            LOG_DIR / "maintain.out.log", LOG_DIR / "maintain.err.log",
            keep_alive=False, run_at_load=False, calendar_hour=3)),
    )
    for label, xml in jobs:
        _bootout(label)
        plist = _LAUNCH_AGENTS / f"{label}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(xml)
        subprocess.run(["launchctl", "bootstrap", _gui(), str(plist)], check=True)
        print(f"已加载 {label} -> {plist}")


def cmd_uninstall() -> None:
    for label in (SERVER_LABEL, MAINTAIN_LABEL):
        _bootout(label)
        plist = _LAUNCH_AGENTS / f"{label}.plist"
        plist.unlink(missing_ok=True)
        print(f"已卸载 {label}")


def cmd_status() -> None:
    for label in (SERVER_LABEL, MAINTAIN_LABEL):
        r = subprocess.run(["launchctl", "print", f"{_gui()}/{label}"], capture_output=True)
        print(f"{label}: {'已加载' if r.returncode == 0 else '未加载'}")


def cmd_rotate() -> None:
    rotated = rotate_logs(LOG_DIR)
    print(f"已轮转: {rotated or '无超限日志'}")


def cmd_backup() -> None:
    target = backup(db_path=DB_PATH, backup_dir=BACKUP_DIR, extra=(RUNTIME_CFG,))
    print(f"已备份到 {target}")


def cmd_run() -> None:
    cmd_rotate()
    cmd_backup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="personal-library 运维工具")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="安装并加载 launchd 服务")
    sub.add_parser("uninstall", help="卸载 launchd 服务")
    sub.add_parser("status", help="查看服务状态")
    sub.add_parser("rotate", help="手动轮转日志")
    sub.add_parser("backup", help="手动备份数据")
    sub.add_parser("run", help="轮转 + 备份（维护定时器调用）")
    args = parser.parse_args(argv)
    handlers = {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "rotate": cmd_rotate,
        "backup": cmd_backup,
        "run": cmd_run,
    }
    try:
        handlers[args.cmd]()
        return 0
    except subprocess.CalledProcessError as e:
        print(f"launchctl 操作失败（退出码 {e.returncode}），可重试或手动检查: launchctl print {_gui()}/{SERVER_LABEL}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
