"""ASR 客户端：智谱 GLM-ASR（OpenAI Whisper 兼容 transcriptions 接口）。

base_url/api_key 留空自动回落 llm_*（同一智谱 key 即可用）；模型默认 glm-asr-2512。
接口限制：单次音频 ≤30 秒 / ≤25MB / 仅 wav|mp3 —— 长音频用 split_wav 纯标准库切块。
"""
from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path

import httpx

from .runtime_config import RuntimeConfig

log = logging.getLogger(__name__)

CHUNK_SECONDS = 28          # 留 2s 余量，防边界超 30s 限制
_PROMPT_TAIL_CHARS = 200    # 链式上下文：携带上一块尾部文本（接口文档建议 <8000）


class AsrClient:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        self._client: httpx.Client | None = None
        self._sig: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> tuple[str, str]:
        """asr_* 显式配置优先，空值回落 llm_*。"""
        base = str(self._cfg.get("asr_base_url") or self._cfg.get("llm_base_url") or "")
        key = str(self._cfg.get("asr_api_key") or self._cfg.get("llm_api_key") or "")
        return base.rstrip("/"), key

    @property
    def available(self) -> bool:
        return bool(self._resolve()[1])

    @property
    def model(self) -> str:
        return str(self._cfg.get("asr_model") or "glm-asr-2512")

    def _ensure(self) -> httpx.Client:
        base, key = self._resolve()
        with self._lock:
            if (base, key) != self._sig:
                self._client = httpx.Client(
                    base_url=base, timeout=180,
                    headers={"Authorization": f"Bearer {key}"},
                ) if key else None
                self._sig = (base, key)
                if self._client:
                    log.info("ASR 客户端已连接 %s（model=%s）", base, self.model)
        if not self._client:
            raise RuntimeError("语音转写 key 未配置（asr_api_key 或 llm_api_key）")
        return self._client

    def transcribe_file(self, path: Path, prompt: str = "") -> str:
        """转写单个音频文件（≤30s），返回文本。prompt 携带上文以提升连贯性。"""
        client = self._ensure()
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "audio/wav" if path.suffix == ".wav" else "audio/mpeg")}
            data = {"model": self.model}
            if prompt:
                data["prompt"] = prompt[-_PROMPT_TAIL_CHARS:]
            resp = client.post("/api/paas/v4/audio/transcriptions", files=files, data=data)
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()


def split_wav(src: Path, out_dir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[Path]:
    """纯标准库按时间切块 wav（帧级切片，逐块写规范头）；返回按时间排序的块列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    with wave.open(str(src), "rb") as w:
        params = w.getparams()
        frames_per_chunk = params.framerate * chunk_seconds
        idx = 0
        while True:
            frames = w.readframes(frames_per_chunk)
            if not frames:
                break
            out = out_dir / f"chunk-{idx:04d}.wav"
            with wave.open(str(out), "wb") as ow:
                ow.setnchannels(params.nchannels)
                ow.setsampwidth(params.sampwidth)
                ow.setframerate(params.framerate)
                ow.writeframes(frames)
            chunks.append(out)
            idx += 1
    return chunks


def transcribe_audio(asr: AsrClient, wav_path: Path, tmpdir: Path,
                     progress=None, chunk_seconds: int = CHUNK_SECONDS) -> str:
    """整段音频 → 分块转写 → `[mm:ss] 文本` 行式转写稿（时间戳=块起点）。"""
    chunks = split_wav(wav_path, tmpdir / "chunks", chunk_seconds=chunk_seconds)
    if not chunks:
        raise RuntimeError("音频切分为空")
    prompt = ""
    lines: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress(f"语音转写中 ({i}/{len(chunks)})")
        text = asr.transcribe_file(chunk, prompt=prompt)
        if text:
            start = (i - 1) * chunk_seconds
            mm, ss = divmod(start, 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {text}")
            prompt = text
    return "\n".join(lines)
