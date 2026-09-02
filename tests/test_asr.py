"""ASR 客户端与 wav 分块测试（离线：httpx MockTransport + wave 生成正弦波）。"""
import json
import wave
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.core.asr import AsrClient, split_wav, transcribe_audio
from app.core.runtime_config import RuntimeConfig


class FakeCfg(dict):
    """dict 式 cfg：get() 直接查表，模拟 RuntimeConfig。"""

    def get(self, k, default=None):
        return super().get(k, default)


def _make_wav(path: Path, seconds: float, rate: int = 8000) -> None:
    import math
    import struct

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(int(seconds * rate)):
            v = int(20000 * math.sin(2 * math.pi * 440 * i / rate))
            w.writeframes(struct.pack("<h", v))


def test_asr_falls_back_to_llm_key(tmp_path):
    cfg = FakeCfg(llm_base_url="https://open.bigmodel.cn/api/paas/v4", llm_api_key="k-llm")
    asr = AsrClient(cfg)
    assert asr.available is True
    assert asr.model == "glm-asr-2512"
    base, key = asr._resolve()
    assert key == "k-llm"  # asr_* 未配置 → 回落 llm


def test_asr_explicit_overrides():
    cfg = FakeCfg(llm_base_url="https://llm.example", llm_api_key="k-llm",
                  asr_base_url="https://asr.example", asr_api_key="k-asr", asr_model="m1")
    asr = AsrClient(cfg)
    base, key = asr._resolve()
    assert base == "https://asr.example" and key == "k-asr" and asr.model == "m1"


def test_asr_unavailable_without_any_key():
    assert AsrClient(FakeCfg()).available is False


def test_transcribe_file_multipart(tmp_path):
    audio = tmp_path / "a.wav"
    _make_wav(audio, 0.1)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "你好世界"})

    cfg = FakeCfg(llm_base_url="https://open.bigmodel.cn", llm_api_key="k-test",
                  asr_model="glm-asr-2512")
    asr = AsrClient(cfg)
    asr._client = httpx.Client(transport=httpx.MockTransport(handler),
                               base_url="https://open.bigmodel.cn",
                               headers={"Authorization": "Bearer k-test"})
    asr._sig = ("https://open.bigmodel.cn", "k-test")  # 跳过重建
    text = asr.transcribe_file(audio, prompt="上文")
    assert text == "你好世界"
    assert seen["path"] == "/api/paas/v4/audio/transcriptions"
    assert seen["auth"] == "Bearer k-test"
    assert "multipart/form-data" in seen["content_type"]


def test_split_wav_chunks(tmp_path):
    src = tmp_path / "long.wav"
    _make_wav(src, 3.0, rate=8000)  # 3 秒，28s/块 → 应切出 1 块
    chunks = split_wav(src, tmp_path / "out", chunk_seconds=28)
    assert len(chunks) == 1
    # 用小窗口验证切块数量：1.2s/块 → 3 块
    chunks = split_wav(src, tmp_path / "out2", chunk_seconds=1)
    assert len(chunks) == 3
    # 每块都是可读的合法 wav
    with wave.open(str(chunks[0]), "rb") as w:
        assert w.getframerate() == 8000


def test_transcribe_audio_chains_prompt_and_timestamps(tmp_path):
    src = tmp_path / "long.wav"
    _make_wav(src, 2.5, rate=8000)
    prompts = []

    class FakeAsr:
        available = True

        def transcribe_file(self, path, prompt=""):
            prompts.append(prompt)
            return f"第{len(prompts)}块内容"

    transcript = transcribe_audio(FakeAsr(), src, tmp_path, chunk_seconds=1)
    lines = transcript.splitlines()
    assert lines[0].startswith("[00:00] 第1块内容")
    assert lines[1].startswith("[00:01] 第2块内容")
    assert prompts == ["", "第1块内容", "第2块内容"]  # 链式上下文


def test_runtime_config_has_new_fields(tmp_path):
    s = Settings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    cfg = RuntimeConfig(s)
    cfg._path = tmp_path / "rc.json"  # 测试隔离
    out = cfg.update(asr_base_url="https://x.example", asr_model="glm-asr-pro")
    assert set(out) >= {"asr_base_url", "asr_model"}
    assert cfg.get("asr_base_url") == "https://x.example"
    assert json.loads((tmp_path / "rc.json").read_text())["asr_model"] == "glm-asr-pro"
