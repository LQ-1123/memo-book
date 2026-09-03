"""递归目录扫描测试：目录剪枝（默认排除）+ 逐层 .gitignore 语义。"""
from __future__ import annotations

from pathlib import Path

from app.ingest.pipeline import _is_noise, iter_project_files


def _make_project(root: Path) -> None:
    (root / "src" / "deep").mkdir(parents=True)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "dist").mkdir(parents=True)
    (root / "sub" / "build").mkdir(parents=True)
    (root / "sub" / "build2").mkdir(parents=True)

    (root / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
    (root / "notes.md").write_text("# 项目笔记", encoding="utf-8")
    (root / "debug.log").write_text("noise", encoding="utf-8")  # *.log 忽略
    (root / "src" / "main.py").write_text("print('main')", encoding="utf-8")
    (root / "src" / "deep" / "util.js").write_text("export {};", encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")  # 默认排除
    (root / "dist" / "bundle.js").write_text("x", encoding="utf-8")  # 默认排除
    (root / "package-lock.json").write_text("{}", encoding="utf-8")  # 锁文件排除
    (root / "sub" / ".gitignore").write_text("secret.py\n", encoding="utf-8")
    (root / "sub" / "secret.py").write_text("x", encoding="utf-8")  # 子 .gitignore 忽略
    (root / "sub" / "other.py").write_text("x", encoding="utf-8")
    (root / "sub" / "build" / "x.py").write_text("x", encoding="utf-8")  # 根规则 build/ 命中任意层
    (root / "sub" / "build2" / "keep.py").write_text("x", encoding="utf-8")  # build2 不受 build/ 影响


def test_iter_project_files_recursive_with_gitignore(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    _make_project(root)

    got = {p.relative_to(root).as_posix() for p in iter_project_files(root)}
    assert got == {
        "notes.md",
        "src/main.py",
        "src/deep/util.js",
        "sub/other.py",
        "sub/build2/keep.py",
    }


def test_iter_project_files_empty_dir(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    assert list(iter_project_files(root)) == []


def test_is_noise_covers_build_dirs_and_lockfiles():
    assert _is_noise(Path("/x/node_modules/a.js"))
    assert _is_noise(Path("/x/dist/bundle.js"))
    assert _is_noise(Path("/x/sub/target/debug.rs"))
    assert _is_noise(Path("/x/package-lock.json"))
    assert _is_noise(Path("/x/.git/config"))
    assert not _is_noise(Path("/x/src/main.py"))
    assert not _is_noise(Path("/x/build2/keep.py"))  # build2 ≠ build
