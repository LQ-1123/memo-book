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
