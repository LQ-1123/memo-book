# -*- mode: python ; coding: utf-8 -*-
"""personal-library 桌面版 PyInstaller spec（macOS .app 与 Windows onedir 共用）。

构建：.venv/bin/python -m PyInstaller --noconfirm personal-library.spec
前置：pip install pyinstaller pywebview；bash scripts/fetch_ffmpeg.sh
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),        # 前端壳（main.py 以 __file__ 相对路径挂载）
    ("resources/icons", "resources/icons"),
]

# 捆绑的静态 ffmpeg（fetch_ffmpeg.sh 下载；缺失则打出来但 ASR 兜底功能在目标机不可用）
_ff_root = "resources/ffmpeg"
if os.path.isdir(_ff_root):
    for root, _dirs, files in os.walk(_ff_root):
        for f in files:
            src = os.path.join(root, f)
            arc = os.path.join("ffmpeg", os.path.relpath(src, _ff_root))
            datas.append((src, arc))
else:
    print("!! 未找到 resources/ffmpeg —— 本包将不含 ffmpeg（B站无字幕视频入库不可用）")

binaries = []
hiddenimports = []

# 重依赖整包收集：ONNX 模型随包内置、动态导入的子模块多
for pkg in ("rapidocr_onnxruntime", "onnxruntime", "jieba", "trafilatura", "openai", "qdrant_client"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("markitdown")      # docx/pptx/xlsx 惰性加载
hiddenimports += collect_submodules("webview.platforms")  # pywebview 平台后端按平台动态选择
hiddenimports += collect_submodules("watchdog")        # 文件监听按平台动态选 observer
hiddenimports += [
    "yt_dlp",  # 函数内导入，显式声明
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "qdrant_client.local.mode",
]

a = Analysis(
    ["run_desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="personal-library",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 双击启动不弹控制台
    icon="resources/icons/icon.ico" if os.name == "nt" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="personal-library")

if os.name != "nt":
    app = BUNDLE(
        coll,
        name="personal-library.app",
        icon="resources/icons/icon.icns",
        bundle_identifier="com.personal-library.desktop",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHumanReadableCopyright": "© 2026 personal-library",
        },
    )
