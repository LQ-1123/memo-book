#!/usr/bin/env bash
# macOS 桌面版一键构建：装依赖 → 图标 → ffmpeg → PyInstaller → 签名 → zip。
# 产物：dist/personal-library.app 与 dist/personal-library-macos.zip
#
# 可选：正式签名（有 Apple Developer ID 时）
#   APPLE_IDENTITY="Developer ID Application: Your Name (TEAMID)" bash scripts/build_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x $PY ]] || { echo "未找到 .venv，先执行: python3 -m venv .venv && .venv/bin/pip install -e ."; exit 1; }

echo "==> [1/5] 构建依赖（pyinstaller / pywebview，已装则跳过）"
$PY -c "import PyInstaller" 2>/dev/null || $PY -m pip install pyinstaller
$PY -c "import webview" 2>/dev/null || $PY -m pip install pywebview

echo "==> [2/5] 生成应用图标"
$PY scripts/gen_icons.py

echo "==> [3/5] 下载静态 ffmpeg（已存在则跳过）"
bash scripts/fetch_ffmpeg.sh

echo "==> [4/5] PyInstaller 打包（几分钟）"
rm -rf build dist
$PY -m PyInstaller --noconfirm personal-library.spec

echo "==> [5/5] 签名与打包 zip"
APP=dist/personal-library.app
if [[ -n "${APPLE_IDENTITY:-}" ]]; then
  codesign --deep --force --options runtime --sign "$APPLE_IDENTITY" "$APP"
  echo "已用 $APPLE_IDENTITY 签名；分发前建议公证: xcrun notarytool submit ..."
else
  codesign --deep --force --sign - "$APP"
  echo "已 ad-hoc 签名：接收方首次打开需「右键 → 打开」"
fi
(
  cd dist
  zip -qry personal-library-macos.zip personal-library.app
)
du -sh "$APP" dist/personal-library-macos.zip
echo "完成: $APP"
