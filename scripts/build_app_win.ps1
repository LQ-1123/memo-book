# personal-library Windows 桌面版一键构建（在 Windows 机器上执行一次）
# 前置：已安装 Python 3.11+（勾选 "Add to PATH"）
# 用法：在仓库根目录打开 PowerShell →  .\scripts\build_app_win.ps1
# 产物：dist\personal-library-win64.zip（解压后双击 personal-library\personal-library.exe）

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# ---- [1/5] venv 与依赖 ----
Write-Host "==> [1/5] 准备 venv 与依赖"
$Mirror = $env:PIP_INDEX_URL
if (-not $Mirror) { $Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple" }  # 国内镜像提速
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv   # 用 PATH 里的 python：CI 上=setup-python 指定版本。
    # 勿用 `py -3`——它选系统注册的最高版本（如 3.14），重依赖无 wheel 会静默装失败
}
$PY = ".venv\Scripts\python.exe"
& $PY --version
& $PY -m pip install -U pip -q -i $Mirror
& $PY -m pip install -e . -q -i $Mirror
if ($LASTEXITCODE -ne 0) { throw "pip install -e . 失败（exit $LASTEXITCODE）——依赖装不全会打出残缺包" }
& $PY -m pip install pyinstaller -q -i $Mirror

# ---- [2/5] 图标 ----
Write-Host "==> [2/5] 生成应用图标"
& $PY scripts\gen_icons.py

# ---- [3/5] ffmpeg ----
Write-Host "==> [3/5] 下载 ffmpeg（npmmirror 国内镜像优先，失败回落 BtbN；已存在则跳过）"
New-Item -ItemType Directory -Force -Path "resources\ffmpeg\windows" | Out-Null
# 上次异常残留的同名目录会让后续下载/打包行为诡异，先清掉
if (Test-Path "resources\ffmpeg\windows\ffmpeg.exe" -PathType Container) {
    Remove-Item -Recurse -Force "resources\ffmpeg\windows\ffmpeg.exe"
}
if (-not (Test-Path "resources\ffmpeg\windows\ffmpeg.exe")) {
    $ok = $false
    # 源1：npmmirror（阿里二进制镜像，单文件直下，体积小速度快）；目录布局不确定，两种都试
    foreach ($u in @(
        "https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.0/win32-x64/ffmpeg.exe",
        "https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.0/ffmpeg-win32-x64"
    )) {
        try {
            Invoke-WebRequest -Uri $u -OutFile "resources\ffmpeg\windows\ffmpeg.exe" -TimeoutSec 60
            $ok = $true
            break
        } catch { Write-Host "  源不可用: $u" }
    }
    # 源2：BtbN 官方 zip（约 90MB，慢）
    if (-not $ok) {
        $zip = "resources\ffmpeg\windows\ff.zip"
        Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath "resources\ffmpeg\windows\_tmp" -Force
        $bin = Get-ChildItem "resources\ffmpeg\windows\_tmp" -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        Copy-Item $bin.FullName "resources\ffmpeg\windows\ffmpeg.exe"
        $probe = Get-ChildItem "resources\ffmpeg\windows\_tmp" -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
        if ($probe) { Copy-Item $probe.FullName "resources\ffmpeg\windows\ffprobe.exe" }
        Remove-Item -Recurse -Force "resources\ffmpeg\windows\_tmp", $zip
        $ok = $true
    }
}
else { Write-Host "已存在，跳过" }

# ffprobe（可选件，仅部分后处理用）：npmmirror 的 @ffprobe-installer npm 包内二进制（国内快），失败不阻塞
if (-not (Test-Path "resources\ffmpeg\windows\ffprobe.exe")) {
    try {
        Write-Host "下载 ffprobe（npmmirror 国内源，约 29MB）"
        Invoke-WebRequest -Uri "https://registry.npmmirror.com/@ffprobe-installer/win32-x64/-/win32-x64-5.1.0.tgz" -OutFile "resources\ffmpeg\windows\ffprobe.tgz" -TimeoutSec 120
        tar -xzf "resources\ffmpeg\windows\ffprobe.tgz" -C "resources\ffmpeg\windows"
        $probe = Get-ChildItem "resources\ffmpeg\windows\package" -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
        if ($probe) { Copy-Item $probe.FullName "resources\ffmpeg\windows\ffprobe.exe" }
    } catch { Write-Host "  ffprobe 获取失败（可选，不影响核心功能）：$_" }
    finally {
        if (Test-Path "resources\ffmpeg\windows\package") { Remove-Item -Recurse -Force "resources\ffmpeg\windows\package" }
        if (Test-Path "resources\ffmpeg\windows\ffprobe.tgz") { Remove-Item -Force "resources\ffmpeg\windows\ffprobe.tgz" }
    }
}

# ---- [4/5] PyInstaller ----
Write-Host "==> [4/5] PyInstaller 打包（几分钟）"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
& $PY -m PyInstaller --noconfirm personal-library.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# ---- [4.5/5] 产物自检：PyInstaller "成功"不代表依赖进包（曾因 Python 3.14 无 wheel 打出空壳）----
Write-Host "==> [4.5/5] 产物自检"
$internal = "dist\personal-library\_internal"
foreach ($need in @(
    "$internal\app\static\index.html",
    "$internal\ffmpeg\windows\ffmpeg.exe",
    "$internal\onnxruntime",
    "$internal\cv2",
    "$internal\pandas",
    "$internal\magika",
    "$internal\PIL\_imaging.pyd"
)) {
    if (-not (Test-Path $need)) { throw "产物缺 $need —— 重依赖未收集（查 Python 版本与依赖安装日志）" }
}
$pydll = Get-ChildItem "$internal" -Filter "python3*.dll" | Select-Object -First 1
Write-Host "自检通过；运行时: $($pydll.Name)"

# ---- [5/5] 打包 zip ----
Write-Host "==> [5/5] 压缩 zip"
Compress-Archive -Path "dist\personal-library" -DestinationPath "dist\personal-library-win64.zip" -Force
Get-ChildItem dist | Format-Table Name, @{L = "Size"; E = { "{0:N0} MB" -f ($_.Length / 1MB) }}
Write-Host "完成: dist\personal-library-win64.zip"
Write-Host "提示：目标机器需要 WebView2 运行时（Win10/11 一般自带）；首次启动数据目录在 %APPDATA%\personal-library"
