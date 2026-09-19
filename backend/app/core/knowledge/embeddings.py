"""
Embedding 提供方抽象（知识库改进 Phase 1 / 借鉴 WeKnora 可插拔模型设计）
==========================================================================

三类实现，按性价比自动选择：

- ``openai``：OpenAI 兼容 /embeddings API（硅基流动 BGE 系列、通义等，
  百万 token 级几元——性价比主力），L2 归一化；
- ``hashing``：确定性字符 n-gram 哈希向量（零依赖/零成本/离线，
  捕捉字面 n-gram 重叠；测试与未配置 API 时的兜底）；
- ``none``：禁用语义通道（混合检索退回 BM25+图谱两路）。

配置（settings）：
- ERP_EMBEDDING_PROVIDER: auto（默认，配置了 key 用 openai，否则 hashing）
  / openai / hashing / none
- ERP_EMBEDDING_API_BASE / ERP_EMBEDDING_API_KEY / ERP_EMBEDDING_MODEL
- ERP_EMBEDDING_DIMS: hashing 向量维度（默认 256）

所有向量 L2 归一化（余弦相似度 = 点积）。
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

import structlog

logger = structlog.get_logger()


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class EmbeddingProvider(ABC):
    """Embedding 提供方接口（批量接口，L2 归一化输出）。"""

    name: str = "base"

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。失败应抛异常（调用方决定降级策略）。"""


class HashingEmbeddingProvider(EmbeddingProvider):
    """
    确定性字符 n-gram 哈希向量（零依赖兜底）。

    特征：unigram + bigram 字符 n-gram，hash 到固定维并按出现次数加权，
    L2 归一化。捕捉字面重叠（型号/编号/专有名词匹配友好），
    语义泛化弱于深度模型——正式环境建议配置 openai 类提供方。
    """

    name = "hashing"

    def __init__(self, dims: int = 256):
        self.dims = dims

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        text = (text or "").lower().strip()
        if not text:
            return vec
        grams = [text[i] for i in range(len(text))]
        grams += [text[i:i + 2] for i in range(len(text) - 1)]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dims] += 1.0
        return _l2_normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容 /embeddings（DeepSeek 无此 API；推荐硅基流动 BGE-m3 等）。"""

    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = 30, batch_size: int = 32):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": batch},
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                out.extend(_l2_normalize(d["embedding"]) for d in data)
        return out


def get_embedding_provider():
    """
    按配置构建提供方（每次调用新实例，轻量对象）。

    auto 模式：配置了 ERP_EMBEDDING_API_KEY 且 provider 为 auto/openai
    → openai；否则 hashing（零成本兜底，显式告警提示语义能力受限）。
    """
    from app.core.config import settings

    provider = (settings.embedding_provider or "auto").lower()
    try:
        if provider == "openai" or (
            provider == "auto" and settings.embedding_api_key
        ):
            if not settings.embedding_api_key:
                raise ValueError("openai 提供方需要 ERP_EMBEDDING_API_KEY")
            return OpenAIEmbeddingProvider(
                base_url=settings.embedding_api_base,
                api_key=settings.embedding_api_key,
                model=settings.embedding_model,
            )
        return HashingEmbeddingProvider(dims=settings.embedding_dims)
    except Exception as e:  # noqa: BLE001
        logger.warning("embedding.provider_fallback_hashing",
                       requested=provider, error=str(e))
        return HashingEmbeddingProvider(dims=settings.embedding_dims)
