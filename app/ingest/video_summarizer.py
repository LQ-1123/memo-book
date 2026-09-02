"""视频 → 字幕/语音转写 → LLM 结构化笔记，落盘 clips/ 走统一文件管线。

支持平台（app/ingest/platforms.py 注册表）：B站（AI 字幕需 SESSDATA）、
取数思路借鉴 video-to-article-skill：yt-dlp（Python 库）拉元数据与媒体，
LLM 把转写稿整理为覆盖细节的中文笔记，附录保留带时间戳的完整文本便于问答定位。

安全约定：对本服务之外 URL 发起请求前逐跳校验 —— 仅 http(s)，
拒绝 localhost/环回/私网/链路本地/保留/组播地址（防 SSRF）；
yt-dlp 只接收校验通过后的最终 URL，库内直调、无 shell。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

from ..core.fftools import ffmpeg_location, ffmpeg_path
from .platforms import detect_platform

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_SUB_LANGS = ["zh-Hans", "zh-CN", "zh", "ai-zh", "en"]
_COOKIE_EXPIRY = "1893456000"  # cookie 文件里的远期过期时间（2030）
_MAX_REDIRECTS = 5
_DIRECT_LIMIT = 36_000  # 字幕直接单次交给 LLM 的字符上限，超出走分段汇总
_SEGMENT_SIZE = 24_000
_ASR_MAX_SECONDS = 900  # 语音转写兜底的时长上限（15 分钟，控成本）


class VideoIngestError(RuntimeError):
    """对用户可读的视频入库失败原因。"""


# ---------- URL 解析与校验 ----------


def is_bilibili_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in _BILIBILI_HOSTS


def extract_bvid(url: str) -> str | None:
    """从 bilibili 链接提取 BV 号（路径或 bvid= 参数）。"""
    parsed = urllib.parse.urlsplit(url)
    qs_bvid = (urllib.parse.parse_qs(parsed.query).get("bvid") or [None])[0]
    if qs_bvid and _BV_RE.fullmatch(qs_bvid):
        return qs_bvid
    m = _BV_RE.search(parsed.path)
    return m.group(0) if m else None


def _check_addr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise VideoIngestError(f"拒绝非公网地址: {addr}")


def _check_public_host(host: str, resolve_dns: bool = True) -> None:
    if not host:
        raise VideoIngestError("URL 缺少主机名")
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None:
        _check_addr(addr)
        return
    if not resolve_dns:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise VideoIngestError(f"域名解析失败: {host}") from e
    if not infos:
        raise VideoIngestError(f"域名解析为空: {host}")
    for info in infos:
        _check_addr(ipaddress.ip_address(info[4][0]))


def validate_url(url: str, resolve_dns: bool = True) -> str:
    """校验 URL：仅 http/https，主机必须解析到公网地址。返回原 URL。"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise VideoIngestError("仅支持 http(s) 链接")
    _check_public_host(parsed.hostname or "", resolve_dns=resolve_dns)
    return url


def resolve_url(url: str, client: httpx.Client | None = None) -> str:
    """跟随跳转取最终 URL（b23.tv → www.bilibili.com/...），逐跳 SSRF 校验。"""
    current = url
    own = client is None
    http = client or httpx.Client(
        headers={"User-Agent": _UA}, timeout=15, follow_redirects=False
    )
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            validate_url(current)
            try:
                resp = http.get(current)
            except httpx.HTTPError as e:
                raise VideoIngestError(f"链接访问失败: {e}") from e
            if resp.is_redirect:
                loc = resp.headers.get("location", "")
                if not loc:
                    raise VideoIngestError("重定向缺少 Location")
                current = urllib.parse.urljoin(current, loc)
                continue
            return current
        raise VideoIngestError("重定向次数过多")
    finally:
        if own:
            http.close()


# ---------- yt-dlp（库内直调，无 shell）----------


def _ydl_opts(cookie: Path | None, extra: dict | None = None, browser: tuple | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 2,
    }
    # 桌面版捆绑了静态 ffmpeg，显式告知 yt-dlp 位置（开发/服务器环境下返回 None，走 PATH）
    ffloc = ffmpeg_location()
    if ffloc:
        opts["ffmpeg_location"] = ffloc
    if cookie:
        opts["cookiefile"] = str(cookie)
    if browser:
        # 直接读本机浏览器的 cookie 库（yt-dlp 内部处理钥匙串/加密），省手动导出
        opts["cookiesfrombrowser"] = browser
    if extra:
        opts.update(extra)
    return opts


def _friendly_error(text: str, cookie_hint: str = "") -> str:
    low = (text or "").lower()
    if "充电专属" in text:
        return "该视频为充电专属内容，无法获取"
    if "login" in low or "登录" in text or "sign in" in low or "cookies" in low or "fresh cookies" in low:
        return f"该视频需要登录态才能访问：{cookie_hint or '请到「设置」填写 bilibili_sessdata'}"
    if "unavailable" in low or "已删除" in text or "不存在" in text or "deleted" in low:
        return "视频不存在或已下架"
    if "unable to extract" in low or "no video formats" in low:
        return "未能解析该视频（平台接口可能已变更）：可尝试升级 yt-dlp 后重试"
    last = text.strip().splitlines()[-1] if text.strip() else "yt-dlp 未知错误"
    return f"yt-dlp 失败: {last[:300]}"


def fetch_video_info(url: str, cookie: Path | None, browser: tuple | None = None) -> dict:
    import yt_dlp

    def _once() -> dict:
        with yt_dlp.YoutubeDL(_ydl_opts(cookie, browser=browser)) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = _with_retry(_once)
    except yt_dlp.utils.DownloadError as e:
        raise VideoIngestError(_friendly_error(str(e))) from e
    if not info:
        raise VideoIngestError("未能获取视频信息")
    return info


def _pick_subtitle(files: list[Path]) -> Path:
    """字幕选优：简中 CC > 简中 AI > 中文 > 英文 > 其他。"""
    prio = ("zh-hans", "zh-cn", "ai-zh", "zh", "en")

    def score(p: Path) -> int:
        lang = p.stem.lower().split(".", 1)[-1]  # "sub.zh-Hans" → "zh-hans"
        for i, key in enumerate(prio):
            if key in lang:
                return i
        return len(prio)

    return min(files, key=lambda p: (score(p), p.stat().st_mtime))


def fetch_subtitle(url: str, cookie: Path | None, tmpdir: Path, browser: tuple | None = None) -> Path | None:
    """下载字幕到 tmpdir，返回最优字幕文件；没有可用字幕返回 None。"""
    import yt_dlp

    opts = _ydl_opts(
        cookie,
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": _SUB_LANGS,
            "subtitlesformat": "srt/vtt/best",
            "outtmpl": str(tmpdir / "sub.%(ext)s"),
        },
        browser=browser,
    )
    def _once() -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

    try:
        _with_retry(_once)
    except yt_dlp.utils.DownloadError as e:
        # 信息阶段已成功，此处失败多为字幕接口不可用，按无字幕处理
        log.warning("字幕下载失败（按无字幕处理）: %s", e)
    files = sorted(tmpdir.glob("sub.*.srt")) + sorted(tmpdir.glob("sub.*.vtt"))
    if not files:
        return None
    return _pick_subtitle(files)


def fetch_audio_as_wav(url: str, cookie: Path | None, tmpdir: Path, browser: tuple | None = None) -> Path:
    """下载音频并经 yt-dlp 内置 ffmpeg 后处理转单 wav（ASR 前置；块切分由 asr.split_wav 纯标准库完成）。"""
    import yt_dlp

    if not ffmpeg_path():
        raise VideoIngestError("语音转写需要 ffmpeg：请先安装（macOS: brew install ffmpeg）")
    opts = _ydl_opts(
        cookie,
        {
            "format": "bestaudio/best",
            "outtmpl": str(tmpdir / "audio.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        },
        browser=browser,
    )

    def _once() -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

    try:
        _with_retry(_once)
    except yt_dlp.utils.DownloadError as e:
        raise VideoIngestError(_friendly_error(str(e))) from e
    files = list(tmpdir.glob("audio.wav")) + list(tmpdir.glob("audio.*.wav"))
    if not files:
        raise VideoIngestError("未能下载音频")
    return max(files, key=lambda p: p.stat().st_mtime)


def write_cookie_file(dirpath: Path, sessdata: str) -> Path:
    """生成 yt-dlp 用的 Netscape 格式 cookie 文件。

    即使无 SESSDATA 也写入随机 buvid3 —— B站对完全无 cookie 的请求
    常返回 412 风控页；buvid3 为运行时随机生成，非账号凭证。
    """
    buvid3 = f"{uuid.uuid4().hex.upper()}infoc"
    lines = [
        "# Netscape HTTP Cookie File",
        f".bilibili.com\tTRUE\t/\tTRUE\t{_COOKIE_EXPIRY}\tbuvid3\t{buvid3}",
    ]
    if sessdata:
        lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t{_COOKIE_EXPIRY}\tSESSDATA\t{sessdata}")
    p = dirpath / "cookies.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


_TRANSIENT = ("412", "SSL", "EOF", "timed out", "Temporary failure")


def _with_retry(fn, attempts: int = 3, backoff: float = 2.0):
    """yt-dlp 网络抖动重试：仅对瞬态错误（412 风控/SSL 中断/超时）重试。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            text = str(e)
            if i < attempts - 1 and any(k in text for k in _TRANSIENT):
                time.sleep(backoff * (i + 1))
                continue
            raise
    raise last  # pragma: no cover


# ---------- 字幕清洗 ----------

_TAG_RE = re.compile(r"<[^>]+>")


def _mmss(secs: int) -> str:
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _join_cue(a: str, b: str) -> str:
    if not a:
        return b
    ascii_tail = a[-1].isascii() and a[-1].isalnum()
    ascii_head = b[:1].isascii() and b[:1].isalnum()
    return a + (" " + b if (ascii_tail and ascii_head) else b)


def srt_to_transcript(text: str) -> str:
    """SRT/VTT → 按 ~60s 聚段的纯文本，每段前带 [mm:ss] 时间戳。"""
    cues: list[tuple[int, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        tidx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if tidx is None:
            continue
        m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})[,.]", lines[tidx])
        if not m:
            continue
        body = " ".join(_TAG_RE.sub("", l).strip() for l in lines[tidx + 1:]).strip()
        if not body:
            continue
        secs = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        cues.append((secs, body))

    paras: list[str] = []
    acc = ""
    start: int | None = None
    for secs, body in cues:
        if start is not None and secs - start >= 60:
            paras.append(f"[{_mmss(start)}] {acc}")
            acc, start = "", None
        if start is None:
            start = secs
        acc = _join_cue(acc, body)
    if acc and start is not None:
        paras.append(f"[{_mmss(start)}] {acc}")
    return "\n".join(paras)


# ---------- LLM 笔记 ----------

_SYSTEM = "你是专业的视频内容整理师，把视频字幕整理成便于长期检索的知识笔记。"

_MAIN_PROMPT = """以下是B站视频《{title}》（UP主：{uploader}）的字幕记录。

请整理成一篇中文 Markdown 笔记，要求：
1. 忠于字幕内容，不编造、不添加字幕中没有的信息，覆盖绝大部分信息点。
2. 字幕可能来自语音识别：合理断句、修正明显的同音错别字、去掉口水词与重复。
3. 从二级标题开始输出，结构如下：
## 一句话总结
## 核心要点
- 3~8 条
## 详细笔记
按主题分小节（### 小节标题），保留关键数据、例子、步骤与结论。
## 值得记住
金句或关键数据；若无价值则省略此节。
4. 专有名词、代码、命令保留原文。

字幕记录：
{content}"""

_MAP_PROMPT = """以下是B站视频《{title}》字幕的第 {idx}/{total} 段。

请把这一段整理成中文要点笔记：保留该段的关键信息、数据、例子与结论，
用简洁的条目或短段落，不编造。专有名词保留原文。

字幕片段：
{content}"""

_REDUCE_PROMPT = """以下是B站视频《{title}》（UP主：{uploader}）分段笔记的汇总素材。

请合并整理成一篇完整、连贯的中文 Markdown 笔记，要求：
1. 忠于素材内容，不编造；合并重复、理顺结构。
2. 从二级标题开始输出，结构如下：
## 一句话总结
## 核心要点
- 3~8 条
## 详细笔记
按主题分小节（### 小节标题），保留关键数据、例子、步骤与结论。
## 值得记住
金句或关键数据；若无价值则省略此节。

分段笔记素材：
{content}"""


def _split_transcript(transcript: str, size: int = _SEGMENT_SIZE) -> list[str]:
    paras = transcript.split("\n")
    segs: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        if cur and cur_len + len(p) > size:
            segs.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(p) + 1
    if cur:
        segs.append("\n".join(cur))
    return segs


def _meta(info: dict) -> tuple[str, str]:
    title = str(info.get("title") or "未命名视频").strip()
    uploader = str(info.get("uploader") or info.get("channel") or "").strip()
    return title, uploader or "未知UP主"


def summarize_video(llm, info: dict, transcript: str) -> str:
    """字幕 → 结构化笔记；超长字幕分段汇总（map-reduce）。"""
    title, uploader = _meta(info)
    if len(transcript) <= _DIRECT_LIMIT:
        prompt = _MAIN_PROMPT.format(title=title, uploader=uploader, content=transcript)
        return llm.complete(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]
        )
    segs = _split_transcript(transcript)
    notes: list[str] = []
    for i, seg in enumerate(segs, 1):
        notes.append(
            llm.complete(
                [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": _MAP_PROMPT.format(
                            title=title, idx=i, total=len(segs), content=seg
                        ),
                    },
                ]
            )
        )
    joined = "\n\n".join(f"【第 {i} 段】\n{n}" for i, n in enumerate(notes, 1))
    prompt = _REDUCE_PROMPT.format(title=title, uploader=uploader, content=joined)
    return llm.complete(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]
    )


# ---------- 文章组装 ----------


def _fmt_duration(duration) -> str:
    try:
        secs = int(duration or 0)
    except (TypeError, ValueError):
        return "未知"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_article(info: dict, article_md: str, transcript: str, subtitle_kind: str, url: str,
                  platform_label: str = "B站") -> str:
    title, uploader = _meta(info)
    date = str(info.get("upload_date") or "")
    date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 and date.isdigit() else ""
    by = f"UP主：{uploader}" if platform_label == "B站" else f"作者：{uploader}"
    lines = [
        f"# 【视频】{title}",
        "",
        f"> 来源：{platform_label} · {by}"
        + (f" · 发布于 {date_fmt}" if date_fmt else "")
        + f" · 时长 {_fmt_duration(info.get('duration'))} · 字幕：{subtitle_kind}",
        f"> 原链接：{url}",
        "",
        article_md.strip(),
        "",
        "---",
        "",
        "## 附录：完整字幕（含时间戳）",
        "",
        transcript,
        "",
    ]
    return "\n".join(lines)


# ---------- 主流程 ----------


def run_video_ingest(
    *,
    db,
    pipeline,
    llm,
    cfg,
    settings,
    url: str,
    progress=None,
    asr=None,
) -> str:
    """完整流程：解析 → 平台识别 → 去重 → 元数据/字幕(或语音转写) → LLM 笔记 → 落盘 → 入库。"""
    from ..core.asr import transcribe_audio

    def note(stage: str) -> None:
        if progress:
            progress(stage)
        log.info("视频入库 %s：%s", url, stage)

    if not llm.available:
        raise VideoIngestError("问答模型未配置：请先在「设置」填写 LLM API Key")
    raw = url.strip()
    validate_url(raw)

    note("解析链接")
    final_url = resolve_url(raw)
    platform = detect_platform(final_url)
    if platform is None:
        raise VideoIngestError("仅支持视频链接：B站（bilibili.com / b23.tv）")
    if platform.unsupported_reason:
        raise VideoIngestError(platform.unsupported_reason)

    # B站在拉取前就能从 URL 确定 BV 号：命中已入库直接复用（省一次网络请求）
    early_vid = None
    if platform.key == "bilibili":
        bvid = extract_bvid(final_url)
        if bvid:
            early_vid = bvid
            existing = db.find_by_path(str((settings.clips_dir / f"bilibili-{bvid}.md").resolve()))
            if existing and existing["status"] == "indexed":
                note("该视频已入库，直接复用")
                return existing["id"]

    with tempfile.TemporaryDirectory(prefix="video-ingest-") as td:
        tmpdir = Path(td)
        # 平台 cookie 策略：B站=SESSDATA 文件
        cookie: Path | None = None
        browser: tuple | None = None
        cookie_hint = ""
        if platform.key == "bilibili":
            sessdata = str(cfg.get("bilibili_sessdata") or "").strip()
            cookie = write_cookie_file(tmpdir, sessdata) if sessdata else None

        note("获取视频信息")
        info = fetch_video_info(final_url, cookie, browser)

        # 视频 id：B站取 BV 号
        vid = early_vid or (extract_bvid(final_url) if platform.key == "bilibili" else "")
        if not vid:
            raise VideoIngestError("未能从链接中识别 BV 号")

        clip = (settings.clips_dir / f"{platform.key}-{vid}.md").resolve()
        existing = db.find_by_path(str(clip))
        if existing and existing["status"] == "indexed":
            note("该视频已入库，直接复用")
            return existing["id"]

        note("下载字幕")
        sub = fetch_subtitle(final_url, cookie, tmpdir, browser)
        transcript, kind = "", ""
        if sub is not None:
            kind = "AI字幕" if ".ai-" in sub.name.lower() else "CC字幕"
            transcript = srt_to_transcript(sub.read_text(encoding="utf-8", errors="replace"))

        if not transcript.strip():
            # ASR 兜底：B站无字幕时走语音转写
            duration = int(info.get("duration") or 0)
            asr_ready = asr is not None and asr.available and bool(ffmpeg_path())
            if asr_ready and 0 < duration <= _ASR_MAX_SECONDS:
                note("下载音频")
                wav = fetch_audio_as_wav(final_url, cookie, tmpdir, browser)
                transcript = transcribe_audio(asr, wav, tmpdir, progress=lambda s: note(s))
                kind = "语音转写"
            elif platform.key == "bilibili":
                raise VideoIngestError(
                    "该视频没有可用字幕：UP主未加CC字幕时，B站AI字幕需要登录态 —— "
                    "请到「设置」填写 bilibili_sessdata 后重试；或配置语音转写（需 ffmpeg）"
                )
            elif duration > _ASR_MAX_SECONDS:
                raise VideoIngestError(
                    f"视频时长 {duration // 60} 分钟超过语音转写上限（{_ASR_MAX_SECONDS // 60} 分钟），暂无法入库"
                )
            else:
                raise VideoIngestError("无字幕且语音转写不可用：请安装 ffmpeg 并确认已配置智谱 key")

        if len(transcript.strip()) < 50:
            raise VideoIngestError("转写内容过短，可能下载不完整")

        note("生成笔记（LLM 整理中，长视频约需 1-3 分钟）")
        article = summarize_video(llm, info, transcript)
        content = build_article(info, article, transcript, kind, final_url, platform.label)
        clip.write_text(content, encoding="utf-8")

    note("入库索引")
    return pipeline.ingest_path(clip, source="video", url=final_url)
