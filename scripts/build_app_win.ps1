# personal-library Windows 桌面版一键构建（在 Windows 机器上执行一次）
# 前置：已安装 Python 3.11+（勾选 "Add to PATH"）
# 用法：在仓库根目录打开 PowerShell →  .\scripts\build_app_win.ps1
# 产物：dist\personal-library-win64.zip（解压后双击 personal-library\personal-library.exe）

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# ---- [1/5] venv 与依赖 ----
Write-Host "==> [1/5] 准备 venv 与依赖"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
$PY = ".venv\Scripts\python.exe"
& $PY -m pip install -U pip -q
& $PY -m pip install -e . -q
& $PY -m pip install pyinstaller -q

# ---- [2/5] 图标 ----
Write-Host "==> [2/5] 生成应用图标"
& $PY scripts\gen_icons.py

# ---- [3/5] ffmpeg ----
Write-Host "==> [3/5] 下载 ffmpeg（BtbN 静态构建，已存在则跳过）"
New-Item -ItemType Directory -Force -Path "resources\ffmpeg" | Out-Null
if (-not (Test-Path "resources\ffmpeg\windows\ffmpeg.exe")) {
    New-Item -ItemType Directory -Force -Path "resources\ffmpeg\windows" | Out-Null
    $zip = "resources\ffmpeg\windows\ff.zip"
    Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath "resources\ffmpeg\windows\_tmp" -Force
    $bin = Get-ChildItem "resources\ffmpeg\windows\_tmp" -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    Copy-Item $bin.FullName "resources\ffmpeg\windows\ffmpeg.exe"
    $probe = Get-ChildItem "resources\ffmpeg\windows\_tmp" -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if ($probe) { Copy-Item $probe.FullName "resources\ffmpeg\windows\ffprobe.exe" }
    Remove-Item -Recurse -Force "resources\ffmpeg\windows\_tmp", $zip
}
else { Write-Host "已存在，跳过" }

# ---- [4/5] PyInstaller ----
Write-Host "==> [4/5] PyInstaller 打包（几分钟）"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
& $PY -m PyInstaller --noconfirm personal-library.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# ---- [5/5] 打包 zip ----
Write-Host "==> [5/5] 压缩 zip"
Compress-Archive -Path "dist\personal-library" -DestinationPath "dist\personal-library-win64.zip" -Force
Get-ChildItem dist | Format-Table Name, @{L = "Size"; E = { "{0:N0} MB" -f ($_.Length / 1MB) }}
Write-Host "完成: dist\personal-library-win64.zip"
Write-Host "提示：目标机器需要 WebView2 运行时（Win10/11 一般自带）；首次启动数据目录在 %APPDATA%\personal-library"
