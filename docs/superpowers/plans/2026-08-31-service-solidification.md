# 服务固化自启 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 personal-library 服务在 macOS 上常驻：登录自启、崩溃自动拉起、日志轮转、SQLite 数据每日备份——机器重启后无需任何手动操作。

**Architecture:** 单个 Python 运维工具 `scripts/maintain.py` 承载全部能力（日志轮转 / 备份 / launchd 安装卸载），通过两个用户级 LaunchAgent（macOS launchd）实现：`com.personal-library.server`（RunAtLoad + KeepAlive 守护服务进程）与 `com.personal-library.maintain`（每日 03:00 定时执行轮转+备份）。不写 shell 安装脚本、不用 sudo，plist 由 Python 生成。Qdrant 数据不直接备份——按设计 SQLite 是唯一事实源，向量可随时 `POST /ingest/reconcile?force=true` 全量重建。

**Tech Stack:** macOS launchd（LaunchAgent）、Python 3 标准库（sqlite3 backup API / gzip / subprocess 调 launchctl）、pytest。

**Spec:** docs/DESIGN.md（"SQLite 是事实源"章节是备份策略的依据）、README.md

## Global Constraints

- 服务端口固定 **8790**；访问口令等凭据不得以字面量写进任何仓库文件（Mimosa 闸门会拦截）
- launchd Label：服务 `com.personal-library.server`、维护 `com.personal-library.maintain`
- 仅用**用户级 LaunchAgent**（`gui/$(id -u)`），禁止 sudo / LaunchDaemon
- 所有 `ProgramArguments` 用绝对路径：解释器固定为 `.venv/bin/python`；plist 的 `WorkingDirectory` 必须是项目根（`.env` 与 `data/` 按相对路径解析的硬前提）
- 备份保留 7 份、日志单文件 5MB 触发轮转、保留 3 份压缩
- 本工作区有 Mimosa PreToolUse 钩子：新建/修改代码一律走 Write/Edit 工具
- 测试命令统一 `.venv/bin/python -m pytest tests/ -q`

---

### Task 1: maintain.py 骨架 + 日志轮转（TDD）

**Files:**
- Create: `scripts/maintain.py`
- Test: `tests/test_maintain.py`

**Interfaces:**
- Produces: `rotate_logs(log_dir: Path, max_bytes: int = 5_242_880, keep: int = 3) -> list[str]`（返回被轮转的日志文件名列表）；`PROJECT_DIR / VENV_PYTHON / LOG_DIR / BACKUP_DIR / DB_PATH / SERVER_LABEL / MAINTAIN_LABEL` 等常量（后续任务直接引用）

- [ ] **Step 1: 写失败测试**

```python
"""maintain.py 运维工具单元测试（离线，纯文件系统）。"""
import gzip
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "maintain", Path(__file__).resolve().parent.parent / "scripts" / "maintain.py"
)
maintain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintain)


def test_rotate_moves_oversize_log_to_gz(tmp_path):
    log = tmp_path / "server.err.log"
    log.write_bytes(b"x" * 200)
    rotated = maintain.rotate_logs(tmp_path, max_bytes=100, keep=3)
    assert rotated == ["server.err.log"]
    assert not log.exists()
    target = tmp_path / "server.err.log.1.gz"
    assert target.exists()
    assert gzip.decompress(target.read_bytes()) == b"x" * 200


def test_rotate_shifts_and_prunes_old_generations(tmp_path):
    log = tmp_path / "server.err.log"
    for gen in range(1, 3):  # 预置 .1.gz 与 .2.gz（keep=2 上限）
        (tmp_path / f"server.err.log.{gen}.gz").write_bytes(b"old")
    log.write_bytes(b"y" * 300)
    maintain.rotate_logs(tmp_path, max_bytes=100, keep=2)
    assert (tmp_path / "server.err.log.2.gz").read_bytes() == b"old"  # 原 .1.gz 顺移
    assert (tmp_path / "server.err.log.1.gz").exists()  # 刚轮转的
    assert not (tmp_path / "server.err.log.3.gz").exists()  # 超限被剪


def test_rotate_skips_small_logs(tmp_path):
    log = tmp_path / "server.out.log"
    log.write_bytes(b"tiny")
    assert maintain.rotate_logs(tmp_path, max_bytes=100) == []
    assert log.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_maintain.py -q`
Expected: FAIL（`scripts/maintain.py` 不存在，spec_from_file_location 报错）

- [ ] **Step 3: 最小实现**

```python
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
```

（同文件底部先放 `if __name__ == "__main__":` 占位 argparse 骨架，Task 3 补全子命令。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_maintain.py -q`
Expected: PASS（3 个用例）

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 38 个用例全部 PASS

---

### Task 2: SQLite 备份 + 保留策略（TDD）

**Files:**
- Modify: `scripts/maintain.py`（追加 `backup` 函数）
- Test: `tests/test_maintain.py`（追加用例）

**Interfaces:**
- Produces: `backup(db_path: Path, backup_dir: Path, extra: tuple[Path, ...] = (), keep: int = 7, stamp: str | None = None) -> Path`（返回备份目录 `backup-<stamp>/`；stamp 参数供测试注入时间戳）

- [ ] **Step 1: 追加失败测试**

```python
def test_backup_copies_live_db_via_sqlite_api(tmp_path):
    import sqlite3

    db = tmp_path / "library.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.commit()
    conn.close()
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text("{}")

    target = maintain.backup(
        db_path=db, backup_dir=tmp_path / "backups",
        extra=(cfg,), keep=2, stamp="20260831-120000",
    )
    copy = sqlite3.connect(target / "library.db")
    assert copy.execute("SELECT x FROM t").fetchall() == [("hello",)]
    assert (target / "runtime_config.json").exists()


def test_backup_retention_keeps_latest_n(tmp_path):
    import sqlite3

    db = tmp_path / "library.db"
    sqlite3.connect(db).execute("CREATE TABLE t(x)")
    for stamp in ("20260831-110000", "20260831-120000", "20260831-130000"):
        maintain.backup(db_path=db, backup_dir=tmp_path / "backups", keep=2, stamp=stamp)
    names = sorted(p.name for p in (tmp_path / "backups").glob("backup-*"))
    assert names == ["backup-20260831-120000", "backup-20260831-130000"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_maintain.py -q`
Expected: FAIL（`backup` 未定义）

- [ ] **Step 3: 实现 backup（追加到 maintain.py）**

```python
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
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `.venv/bin/python -m pytest tests/test_maintain.py -q` 然后 `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（5 个用例）/ 全量 40 个 PASS

---

### Task 3: plist 生成 + install / uninstall / status 子命令

**Files:**
- Modify: `scripts/maintain.py`（追加 plist 模板与子命令）
- Test: `tests/test_maintain.py`（追加模板断言用例）

**Interfaces:**
- Produces: `plist_xml(label, program_args, stdout, stderr, keep_alive=True, run_at_load=True, calendar_hour=None) -> str`；`cmd_install() / cmd_uninstall() / cmd_status() -> None`；`_bootout(label) -> None`

- [ ] **Step 1: 追加失败测试**

```python
def test_plist_xml_contains_required_keys():
    xml = maintain.plist_xml(
        maintain.SERVER_LABEL,
        [str(maintain.VENV_PYTHON), "-m", "app.main"],
        maintain.LOG_DIR / "server.out.log",
        maintain.LOG_DIR / "server.err.log",
    )
    assert f"<string>{maintain.SERVER_LABEL}</string>" in xml
    assert str(maintain.PROJECT_DIR) in xml          # WorkingDirectory 必须=项目根
    assert str(maintain.VENV_PYTHON) in xml
    assert "<true/>" in xml                          # RunAtLoad / KeepAlive
    assert "StartCalendarInterval" not in xml

def test_plist_xml_maintain_timer():
    xml = maintain.plist_xml(
        maintain.MAINTAIN_LABEL, ["py", "run"],
        maintain.LOG_DIR / "m.out.log", maintain.LOG_DIR / "m.err.log",
        keep_alive=False, run_at_load=False, calendar_hour=3,
    )
    assert "<false/>" in xml and "<integer>3</integer>" in xml
```

- [ ] **Step 2: 跑测试确认失败** — Run: `.venv/bin/python -m pytest tests/test_maintain.py -q`，Expected: FAIL

- [ ] **Step 3: 实现模板与子命令（追加到 maintain.py）**

```python
def plist_xml(
    label: str, program_args: list[str], stdout: Path, stderr: Path,
    keep_alive: bool = True, run_at_load: bool = True, calendar_hour: int | None = None,
) -> str:
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
```

并在 `main()` argparse 里接线五个子命令（install/uninstall/status/rotate/backup，`run` = rotate + backup 顺序执行，`backup` 子命令里若 DB 不存在打印提示并以非零码退出）。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（42 个左右）

---

### Task 4: 真实安装 + 守护验证（系统级手工步骤）

**Files:** 无新文件；产出为已安装的系统服务与 `data/backups/` 首份备份

- [ ] **Step 1: 停掉现有 nohup 实例（避免端口冲突）**

Run: `kill $(lsof -nP -iTCP:8790 -sTCP:LISTEN -t) 2>/dev/null; sleep 1`
Expected: 8790 端口空闲（`lsof -nP -iTCP:8790 -sTCP:LISTEN` 无输出）

- [ ] **Step 2: 安装并确认服务被 launchd 拉起**

Run: `.venv/bin/python scripts/maintain.py install && sleep 5 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8790/api/v1/health`
Expected: 输出两个"已加载"，curl 返回 `200`

- [ ] **Step 3: 崩溃自动拉起验证**

Run: `kill -9 $(lsof -nP -iTCP:8790 -sTCP:LISTEN -t) && sleep 15 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8790/api/v1/health`
Expected: 杀死后 15 秒内 launchd 重新拉起，curl 返回 `200`（ThrottleInterval=10）

- [ ] **Step 4: 维护定时器手动触发一次**

Run: `launchctl kickstart gui/$(id -u)/com.personal-library.maintain && sleep 3 && ls data/backups/ && ls data/logs/`
Expected: 出现 `backup-<时间戳>/library.db`；日志目录包含 server/maintain 的 out/err 四个文件

- [ ] **Step 5: 状态查看与文档核对**

Run: `.venv/bin/python scripts/maintain.py status`
Expected: 两个 label 均显示"已加载"

- [ ] **Step 6: 提醒用户做一次"重启电脑"终验**（重启后不开任何终端，直接访问 http://127.0.0.1:8790/ 应可用）

---

### Task 5: README 运维章节 + 收尾

**Files:**
- Modify: `README.md`（追加"常驻运行与备份"章节）

- [ ] **Step 1: README 追加**

```markdown
## 常驻运行与备份

```bash
.venv/bin/python scripts/maintain.py install    # 登录自启 + 崩溃拉起 + 每日 03:00 维护
.venv/bin/python scripts/maintain.py status     # 查看服务状态
.venv/bin/python scripts/maintain.py backup     # 手动备份（data/backups/，保留 7 份）
.venv/bin/python scripts/maintain.py uninstall  # 卸载自启服务
```

- 日志在 `data/logs/`（单文件 5MB 自动压缩轮转，保留 3 份）
- 备份的是 SQLite（唯一事实源）与运行时配置；Qdrant 向量不备份，恢复后用
  `POST /api/v1/ingest/reconcile?force=true` 全量重建
- 手动前台运行（调试用）：`.venv/bin/python -m app.main`
```

- [ ] **Step 2: 全量回归 + 更新项目记忆**

Run: `.venv/bin/python -m pytest tests/ -q`，Expected: 全部 PASS

---

## Self-Review 结论

- **覆盖核对**：开机自启（Task 3/4 RunAtLoad）、崩溃拉起（KeepAlive + Task 4 Step 3）、日志轮转（Task 1 + 定时器）、数据备份（Task 2 + 定时器）——四个目标各有对应任务与验证步骤 ✅
- **占位符扫描**：无 TBD/TODO；所有代码步骤给出完整实现 ✅
- **类型一致性**：`rotate_logs/backup/plist_xml` 的签名在测试与实现两处一致；Task 3 的 `main()` 接线引用 Task 1/2 定义的同名函数 ✅
