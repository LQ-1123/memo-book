"""模型客户端构造回归测试：防止 base_url 与 api_key 在解包时互换。

历史 bug：sig=(base_url, key) 但 `key, url = sig` 解包反转，导致
OpenAI 客户端把 key 当 base_url，报 UnsupportedProtocol。
"""
import secrets

from app.config import Settings
from app.core.embeddings import EmbeddingClient
from app.core.llm import LLMClient
from app.core.runtime_config import RuntimeConfig

URL = "https://example.com/v4"
KEY = "k" + secrets.token_hex(10)


def make_cfg(tmp_path, embed_key=KEY, llm_key=KEY) -> RuntimeConfig:
    s = Settings(
        api_keys="x", data_dir=tmp_path, watch_dirs="",
        embed_base_url=URL, embed_api_key=embed_key,
        llm_base_url=URL, llm_api_key=llm_key,
        _env_file=None,  # type: ignore[call-arg]
    )
    return RuntimeConfig(s)


def test_embedding_client_uses_url_as_base_url(tmp_path):
    client = EmbeddingClient(make_cfg(tmp_path))._ensure()
    assert client is not None
    assert "example.com" in str(client.base_url), f"base_url 被换成了 key? {client.base_url}"


def test_llm_client_uses_url_as_base_url(tmp_path):
    client = LLMClient(make_cfg(tmp_path))._ensure()
    assert client is not None
    assert "example.com" in str(client.base_url), f"base_url 被换成了 key? {client.base_url}"


def test_client_rebuilds_when_config_changes(tmp_path):
    cfg = make_cfg(tmp_path)
    ec = EmbeddingClient(cfg)
    c1 = ec._ensure()
    cfg.update(embed_base_url="https://other.example.com/v4")
    c2 = ec._ensure()
    assert "other.example.com" in str(c2.base_url)
    assert c1 is not c2
