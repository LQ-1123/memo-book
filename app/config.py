"""集中配置：全部来自环境变量 / .env 文件。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- 服务 ---
    app_host: str = "0.0.0.0"  # 局域网可达；只本机用可改 127.0.0.1
    app_port: int = 8787
    api_keys: str = ""  # 逗号分隔，多个设备各持一个；为空则受保护接口全部拒绝

    # --- 数据 ---
    data_dir: Path = Path("./data")  # SQLite、URL 剪藏等自管数据
    watch_dirs: str = ""  # 逗号分隔的监听目录

    # --- Qdrant ---
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "library"
    # 桌面版用：true 时不连服务端，向量存 data/qdrant/（qdrant-client 内嵌本地模式，无需 Docker）
    qdrant_embedded: bool = False

    # --- 嵌入（OpenAI 兼容，默认智谱 embedding-3）---
    embed_base_url: str = ZHIPU_BASE_URL
    embed_api_key: str = ""
    embed_model: str = "embedding-3"
    embed_dim: int = 2048
    embed_batch_size: int = 16

    # --- LLM（OpenAI 兼容，默认智谱 GLM）---
    llm_base_url: str = ZHIPU_BASE_URL
    llm_api_key: str = ""
    llm_model: str = "glm-4.6"  # 备选：glm-4-flash（免费）、glm-4-plus 等

    # --- 图像理解（多模态 VLM，复用 llm_* 的 base_url/key；留空 = 关闭）---
    vision_model: str = "glm-4v-flash"  # GLM-4V-Flash 免费

    # --- Rerank（可选，SiliconFlow 托管 bge-reranker）---
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # --- B站视频入库（可选）---
    bilibili_sessdata: str = ""  # 登录态 cookie，用于获取 B站 AI 字幕


    # --- 知乎抓取（内容页有 JS 挑战，需带真实 cookie）---

    # --- 语音转写 ASR（无字幕视频兜底；留空回落 llm_* 智谱 key）---
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = "glm-asr-2512"

    # --- OCR / 分块 / 检索参数 ---
    ocr_enabled: bool = True
    ingest_allow_private_urls: bool = False  # SSRF 防护：默认拒绝抓取内网地址；个人内网工具可显式放行
    chunk_target_chars: int = 1100
    chunk_overlap_chars: int = 150
    search_candidates: int = 50  # 每路召回数
    search_topk: int = 10
    ask_topk: int = 6

    # --- 监听对账 ---
    reconcile_interval_sec: int = 300

    # ---- 派生属性 ----

    @property
    def watch_dir_list(self) -> list[Path]:
        return [
            Path(p.strip()).expanduser().resolve()
            for p in self.watch_dirs.split(",")
            if p.strip()
        ]

    @property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"  # URL 抓取正文落盘处（勿放入监听目录）

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
