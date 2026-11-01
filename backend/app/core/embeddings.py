"""Embedding 服务（M13 · 记忆语义检索底座）。

双后端可插拔：
- API：OpenAI 兼容 ``/embeddings``（复用 .env 的 LLM 网关配置），可用时优先
- 哈希兜底：确定性 token 哈希向量（零成本零依赖，中英二元组），离线可用

接口：``embed(text) -> list[float]``（256 维统一维度）、``cosine(a, b)``。
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

DIM = 256
_STOP = {"的", "了", "是", "我", "你", "在", "和", "与", "请", "帮", "有", "个",
         "the", "a", "an", "is", "to", "of", "and", "for", "in", "on"}


def _tokens(text: str) -> list[str]:
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", text.lower())
    zh = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    for seg in zh.split():
        for i in range(max(len(seg) - 1, 1)):
            toks.append(seg[i:i + 2])
    return [t for t in toks if t not in _STOP]


def _hash_vector(text: str) -> list[float]:
    """确定性哈希词袋向量（零外部依赖，语义近似词面——优于纯词面交集）。"""
    vec = [0.0] * DIM
    for t in _tokens(text):
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        vec[h % DIM] += 1.0
        vec[(h >> 32) % DIM] += 0.5  # 二次散列降碰撞
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def embed_api(text: str) -> list[float] | None:
    """OpenAI 兼容 embeddings API（配置了 Key 才启用）。"""
    if not settings.llm_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                settings.llm_base_url.rstrip("/") + "/embeddings",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={"model": "text-embedding-v3", "input": text[:2000]},
            )
            r.raise_for_status()
            vec = r.json()["data"][0]["embedding"]
            return [float(v) for v in vec]
    except Exception as e:
        logger.warning("embeddings.api_failed", error=str(e))
        return None


async def embed(text: str) -> list[float]:
    """统一入口：API 可用→真实语义向量；否则哈希兜底。返回前先截断维度。"""
    vec = await embed_api(text)
    if vec is None:
        return _hash_vector(text)
    if len(vec) > DIM:  # API 维度高于统一维度时降采样
        step = len(vec) / DIM
        vec = [vec[int(i * step)] for i in range(DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
