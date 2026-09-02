#!/usr/bin/env bash
# 下载静态 ffmpeg 到 resources/ffmpeg/<platform>/（构建桌面版用）。
# 幂等：已有可执行文件则跳过。国内网络默认走 npmmirror 镜像，失败自动回落官方源。
#
# 环境变量：
#   FFMPEG_MIRROR=github     强制用 GitHub 官方源（默认 npmmirror 优先）
#   FFMPEG_MIRROR=npmmirror  强制 npmmirror（阿里二进制镜像）
#
# 全部失败时：手动下载静态 ffmpeg 放到对应目录（chmod +x）后重跑即可。
set -euo pipefail
cd "$(dirname "$0")/.."

MIRROR="${FFMPEG_MIRROR:-npmmirror}"
NPMMIRROR_BASE="https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.0"
GH_BASE="https://github.com/eugeneware/ffmpeg-static/releases/latest/download"

mkdir -p resources/ffmpeg

fetch() { # fetch <url> <out>
  echo "下载 $1"
  curl -fL --retry 2 --connect-timeout 15 -A "Mozilla/5.0" -o "$2" "$1"
}

fetch_first() { # fetch_first <out> <url...>：依次尝试，全部失败返回 1
  local out=$1; shift
  local u
  for u in "$@"; do
    if fetch "$u" "$out"; then return 0; fi
    echo "  源不可用，换下一个: $u"
    rm -f "$out"
  done
  return 1
}

# 按 MIRROR 排出 ffmpeg 的候选源顺序；$1=arch（arm64/x86_64）。
# npmmirror 目录布局不确定，目录式/平铺式两种路径都尝试（404 秒切下一个）
_ffmpeg_urls() {
  local arch=$1 gharch=$2
  if [[ $MIRROR == github ]]; then
    echo "$GH_BASE/ffmpeg-darwin-$gharch"
  else
    echo "$NPMMIRROR_BASE/$arch/ffmpeg"
    echo "$NPMMIRROR_BASE/ffmpeg-darwin-$gharch"
    echo "$GH_BASE/ffmpeg-darwin-$gharch"
  fi
}

# ---- macOS（当前架构）----
if [[ "$(uname)" == "Darwin" ]]; then
  case "$(uname -m)" in
    arm64) arch=arm64;  gharch=arm64 ;;
    *)     arch=x64;    gharch=x64  ;;
  esac
  dir=resources/ffmpeg/darwin-$gharch
  mkdir -p "$dir"
  if [[ -x "$dir/ffmpeg" ]]; then
    echo "已存在 $dir/ffmpeg，跳过"
  else
    urls=$(_ffmpeg_urls "$arch" "$gharch")
    # shellcheck disable=SC2086
    if fetch_first "$dir/ffmpeg" $urls; then
      chmod +x "$dir/ffmpeg"
    elif [[ $MIRROR != github ]]; then
      # evermeet.cx 兜底（海外源，慢但稳定；zip 内单文件）
      if [[ $arch == arm64 ]]; then
        fetch "https://evermeet.cx/ffmpeg/getrelease/arm64_zip" "$dir/ffmpeg.zip" && \
          unzip -o -j "$dir/ffmpeg.zip" ffmpeg -d "$dir" && rm "$dir/ffmpeg.zip" && chmod +x "$dir/ffmpeg"
      else
        fetch "https://evermeet.cx/ffmpeg/getrelease/zip" "$dir/ffmpeg.zip" && \
          unzip -o -j "$dir/ffmpeg.zip" ffmpeg -d "$dir" && rm "$dir/ffmpeg.zip" && chmod +x "$dir/ffmpeg"
      fi
    else
      echo "全部源失败：可手动下载静态 ffmpeg 放到 $dir/ffmpeg（chmod +x）后重跑"; exit 1
    fi
  fi
  # ffprobe 可选（仅部分后处理用；失败不阻塞）
  if [[ ! -x "$dir/ffprobe" ]]; then
    if fetch "https://evermeet.cx/ffmpeg/get/ffprobe/zip" "$dir/ffprobe.zip" 2>/dev/null; then
      unzip -o -j "$dir/ffprobe.zip" ffprobe -d "$dir" && rm "$dir/ffprobe.zip" && chmod +x "$dir/ffprobe"
    else
      echo "提示：未获取 ffprobe（可选，不影响核心功能）"
      rm -f "$dir/ffprobe.zip"
    fi
  fi
  exit 0
fi

echo "非 macOS 平台请使用 scripts/build_app_win.ps1 构建 Windows 版"
