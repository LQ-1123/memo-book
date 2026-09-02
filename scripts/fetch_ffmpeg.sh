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

fetch() { # fetch <url> <out> [额外 curl 参数，如 --max-time 240]
  echo "下载 $1"
  local url=$1 out=$2
  shift 2
  # --speed-*：连续 30 秒速度低于 1KB/s 视为卡死，自动断开（换下一源/判失败），避免无限挂起
  curl -fL --retry 2 --connect-timeout 15 --speed-time 30 --speed-limit 1024 "$@" -A "Mozilla/5.0" -o "$out" "$url"
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
  # ffprobe 可选（仅部分后处理用；失败不阻塞）。
  # 优先 npmmirror 的 @ffprobe-installer npm 包（二进制打包在 tarball 里，国内快），
  # 兜底 evermeet.cx（海外源，慢但稳定；zip 内单文件）
  if [[ ! -x "$dir/ffprobe" ]]; then
    case $gharch in
      arm64) pkg="@ffprobe-installer/darwin-arm64"; ver=5.0.1 ;;  # arm64 最新只到 5.0.1（ffprobe 4.4）
      *)     pkg="@ffprobe-installer/darwin-x64";   ver=5.1.0 ;;  # x64 到 5.1.0（ffprobe 2023）
    esac
    ok=0
    echo "下载 ffprobe（npmmirror 国内源，约 8~25MB）"
    if fetch "https://registry.npmmirror.com/$pkg/-/$pkg-$ver.tgz" "$dir/ffprobe.tgz" --max-time 240 2>/dev/null; then
      member=$(tar -tzf "$dir/ffprobe.tgz" 2>/dev/null | grep -m1 '/ffprobe$' || true)
      if [[ -n $member ]] && tar -xzf "$dir/ffprobe.tgz" -C "$dir" "$member" 2>/dev/null && [[ -f "$dir/$member" ]]; then
        mv "$dir/$member" "$dir/ffprobe"
        rm -rf "$dir/package"
        chmod +x "$dir/ffprobe"
        ok=1
      fi
      rm -f "$dir/ffprobe.tgz"
    fi
    if [[ $ok != 1 ]]; then
      echo "  npmmirror 未取到，回落 evermeet（海外源，最多等 4 分钟）"
      if fetch "https://evermeet.cx/ffmpeg/get/ffprobe/zip" "$dir/ffprobe.zip" --max-time 240 2>/dev/null; then
        unzip -o -j "$dir/ffprobe.zip" ffprobe -d "$dir" && rm "$dir/ffprobe.zip" && chmod +x "$dir/ffprobe"
      else
        echo "提示：未获取 ffprobe（可选，不影响核心功能）"
        rm -f "$dir/ffprobe.zip" "$dir/ffprobe.tgz"
      fi
    fi
  fi
  exit 0
fi

echo "非 macOS 平台请使用 scripts/build_app_win.ps1 构建 Windows 版"
