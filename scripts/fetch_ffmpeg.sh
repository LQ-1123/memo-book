#!/usr/bin/env bash
# 下载静态 ffmpeg 到 resources/ffmpeg/<platform>/（构建桌面版用）。
# 幂等：已有可执行文件则跳过。任一源失败会明确提示，也可手动放置二进制。
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p resources/ffmpeg

fetch() { # fetch <url> <out>
  echo "下载 $1"
  curl -fL --retry 2 --connect-timeout 20 -A "Mozilla/5.0" -o "$2" "$1"
}

# ---- macOS（当前架构）----
if [[ "$(uname)" == "Darwin" ]]; then
  arch=$(uname -m)  # arm64 或 x86_64
  dir=resources/ffmpeg/darwin-$([[ $arch == arm64 ]] && echo arm64 || echo x86_64)
  mkdir -p "$dir"
  if [[ -x "$dir/ffmpeg" ]]; then
    echo "已存在 $dir/ffmpeg，跳过"
  else
    ok=0
    # 源1：eugeneware/ffmpeg-static（GitHub release，静态单文件）
    if fetch "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffmpeg-darwin-$arch" "$dir/ffmpeg"; then ok=1; fi
    # 源2：evermeet.cx（x86_64 用 zip；arm64 路径不同）
    if [[ $ok -eq 0 ]]; then
      if [[ $arch == arm64 ]]; then
        fetch "https://evermeet.cx/ffmpeg/getrelease/arm64_zip" "$dir/ffmpeg.zip" && \
          unzip -o -j "$dir/ffmpeg.zip" ffmpeg -d "$dir" && rm "$dir/ffmpeg.zip" && ok=1
      else
        fetch "https://evermeet.cx/ffmpeg/getrelease/zip" "$dir/ffmpeg.zip" && \
          unzip -o -j "$dir/ffmpeg.zip" ffmpeg -d "$dir" && rm "$dir/ffmpeg.zip" && ok=1
      fi
    fi
    [[ $ok -eq 1 ]] || { echo "下载失败：可手动下载静态 ffmpeg 放到 $dir/ffmpeg（chmod +x）后重跑"; exit 1; }
    chmod +x "$dir/ffmpeg"
  fi
  # ffprobe 可选（yt-dlp 部分后处理会用；失败不阻塞）
  if [[ ! -x "$dir/ffprobe" ]]; then
    if fetch "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffprobe-darwin-$arch" "$dir/ffprobe" 2>/dev/null; then
      chmod +x "$dir/ffprobe"
    else
      echo "提示：未获取 ffprobe（可选，不影响核心功能）"
    fi
  fi
  exit 0
fi

# ---- Windows（交叉准备：在 macOS/Linux 上为 Windows 构建预下载也可跳过）----
if [[ "${1:-}" == "--windows" ]]; then
  dir=resources/ffmpeg/windows
  mkdir -p "$dir"
  if [[ -x "$dir/ffmpeg.exe" ]]; then echo "已存在 $dir/ffmpeg.exe，跳过"; exit 0; fi
  fetch "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" "$dir/ff.zip"
  unzip -o -j "$dir/ff.zip" "*/bin/ffmpeg.exe" "*/bin/ffprobe.exe" -d "$dir"
  rm "$dir/ff.zip"
  exit 0
fi

echo "非 macOS 平台请使用 scripts/build_app_win.ps1 构建 Windows 版"
