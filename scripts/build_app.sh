#!/usr/bin/env bash
# macOS 桌面版一键构建：装依赖 → 图标 → ffmpeg → PyInstaller → 签名 → DMG。
# 产物：dist/personal-library.app 与 dist/personal-library-macos.dmg
#
# 可选：正式签名（有 Apple Developer ID 时）
#   APPLE_IDENTITY="Developer ID Application: Your Name (TEAMID)" bash scripts/build_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x $PY ]] || { echo "未找到 .venv，先执行: python3 -m venv .venv && .venv/bin/pip install -e ."; exit 1; }

echo "==> [1/5] 构建依赖（pyinstaller / pywebview，已装则跳过）"
# 国内默认走清华镜像提速；已有 PIP_INDEX_URL 配置则尊重原值
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
$PY -c "import PyInstaller" 2>/dev/null || $PY -m pip install -i "$PIP_INDEX" pyinstaller
$PY -c "import webview" 2>/dev/null || $PY -m pip install -i "$PIP_INDEX" pywebview

echo "==> [2/5] 生成应用图标"
$PY scripts/gen_icons.py

echo "==> [3/5] 下载静态 ffmpeg（已存在则跳过）"
bash scripts/fetch_ffmpeg.sh

echo "==> [4/5] PyInstaller 打包（几分钟）"
# 清场：上次构建的应用若还在运行会锁住 dist 里的 dylib，先结束它再删
pkill -f "dist/personal-library" 2>/dev/null || true
rm -rf build dist 2>/dev/null || true
if [[ -d dist ]]; then
  chflags -R nouchg dist 2>/dev/null || true  # 个别文件可能带「已锁定」标志
  rm -rf build dist || true
fi
if [[ -d dist ]]; then
  echo "无法删除旧产物 dist/：请先退出正在运行的 personal-library 应用（看下 Dock/程序坞），"
  echo "然后手动执行 rm -rf dist 再重跑本脚本。"
  exit 1
fi
$PY -m PyInstaller --noconfirm personal-library.spec

echo "==> [5/5] 签名与打包 DMG"
APP=dist/personal-library.app
if [[ -n "${APPLE_IDENTITY:-}" ]]; then
  codesign --deep --force --options runtime --sign "$APPLE_IDENTITY" "$APP"
  echo "已用 $APPLE_IDENTITY 签名；分发前建议公证: xcrun notarytool submit ..."
else
  codesign --deep --force --sign - "$APP"
  echo "已 ad-hoc 签名：接收方首次打开需「右键 → 打开」"
fi
# DMG（标准分发格式，带「拖入 Applications」安装布局；ULFO=lzfse 压缩，比 UDZO 小 ~15%）
STAGE=dist/dmg-stage
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "personal-library" -srcfolder "$STAGE" -ov -format ULFO \
  dist/personal-library-macos.dmg
rm -rf "$STAGE"
du -sh "$APP" dist/personal-library-macos.dmg
echo "完成: $APP  分发: dist/personal-library-macos.dmg"
