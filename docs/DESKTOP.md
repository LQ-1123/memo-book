# 桌面版（免环境客户端）

把本应用打包成双击即用的桌面客户端：**无需 Python、无需 Docker、无需任何环境**。
macOS 产出 `personal-library.app`，Windows 产出 `personal-library.exe`。

## 桌面版与服务器版的差异

| | 服务器版（现状） | 桌面版 |
|---|---|---|
| 运行方式 | launchd 常驻 / 手动启动 | 双击 .app（.exe），开原生窗口 |
| 向量库 | Docker 里的 Qdrant | **内置**（qdrant-client 本地模式，向量存数据目录 `qdrant/`） |
| 数据目录 | 仓库 `data/` | macOS `~/Library/Application Support/personal-library/`；Windows `%APPDATA%\personal-library`（可用环境变量 `PL_DATA_DIR` 覆盖） |
| 配置 | 仓库 `.env` | 数据目录里的 `.env`（可选；一切也可在网页「设置」里热配置） |
| 监听 | `WATCH_DIRS` | 首次运行自动建 `~/Documents/personal-library-docs` 并监听；可在设置页改 |
| 端口 | 固定 8790 | 默认 8790，被占自动顺延；默认监听 `0.0.0.0`（本机免配置，局域网设备靠口令把门；`.env` 写 `APP_HOST=127.0.0.1` 可收回仅本机） |
| API 口令 | `.env` 的 `API_KEYS` | 首次运行自动生成（存数据目录 `api_key.txt`），本机免认证；手机在「设置 → 手机访问」扫码即用，全程无需填写 |
| ffmpeg | 需自装（B站无字幕兜底） | **内置**静态 ffmpeg |

桌面版的服务与 API 和服务器版完全一致（同一套 `app/` 代码），PWA、分享入库等能力照旧——局域网开启后手机可继续用。

## 首次使用

1. 双击应用 → 原生窗口打开，已自动以生成的口令登录
2. 到「设置」填智谱 `EMBED/LLM API Key`（热生效，无 key 时关键词检索照常可用）
3. 把文档放进 `~/Documents/personal-library-docs`（或直接在网页上传 / 粘贴链接）

## 构建（macOS，本机）

```bash
bash scripts/build_app.sh
```

脚本自包含：自动装 `pyinstaller`/`pywebview`、生成图标、下载静态 ffmpeg（可手动放置 `resources/ffmpeg/darwin-<arch>/ffmpeg` 跳过下载）、PyInstaller 打包、ad-hoc 签名、打 DMG。

- 产物：`dist/personal-library.app`、`dist/personal-library-macos.dmg`（标准分发格式，打开后拖入 Applications 即完成安装）
- 分发 dmg 给对方即可（Intel/Apple Silicon 不通用，按目标机构建）
- **未公证提示**：无 Apple Developer ID 时，对方首次打开需「右键 → 打开」，或
  `xattr -cr /Applications/personal-library.app`
- 有 Developer ID 时正式签名+公证：
  `APPLE_IDENTITY="Developer ID Application: ..." bash scripts/build_app.sh`，随后 `xcrun notarytool submit` + `xcrun stapler staple`

## 构建（Windows，需要一台 Windows 电脑）

PyInstaller 不支持跨平台编译。把整个仓库拷到 Windows 机器（不含 `.venv`），PowerShell 执行：

```powershell
.\scripts\build_app_win.ps1
```

产物 `dist\personal-library-win64.zip`。目标机器要求：

- Windows 10/11（WebView2 运行时一般自带；个别老系统缺时从 https://developer.microsoft.com/microsoft-edge/webview2/ 装）
- 数据在 `%APPDATA%\personal-library`，`api_key.txt` 同目录

## 数据迁移 / 备份

数据目录整体拷贝即完成迁移（SQLite + 向量 + 配置全在里面）。与服务器版一样：
SQLite 是唯一事实源，向量丢失后 `POST /api/v1/ingest/reconcile?force=true` 可全量重嵌入重建。

## 环境变量速查（桌面版）

| 变量 | 作用 |
|---|---|
| `PL_DATA_DIR` | 覆盖数据目录位置 |
| `APP_HOST=127.0.0.1` | 收回为仅本机监听（默认 0.0.0.0，配合「设置 → 手机访问」扫码） |
| `APP_PORT` | 固定端口（默认 8790 自动顺延） |
| `API_KEYS` | 自定义口令（否则首启自动生成） |
| `QDRANT_EMBEDDED` | 默认桌面即为 true；服务器形态置 false 并配 `QDRANT_URL` |

## 开发模式直接试桌面壳

```bash
.venv/bin/python run_desktop.py
```

（等价于打包后双击 .app 的行为；不打包、直接起窗口。）
