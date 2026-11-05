"""Embedding 客户端（V4）：按 EMBEDDING_BACKEND 选择后端。

- api         ：OpenAI 兼容 /embeddings（付费；需 EMBEDDING_API_KEY）
- local_model ：本地开源中文模型（fastembed + BAAI/bge-small-zh-v1.5，免费，默认）
- hash        ：确定性哈希向量（无外部依赖，仅离线演示/测试）

任何后端加载/调用失败都会降级到 hash，并置 degraded=True。
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx

HASH_DIM = 256


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]|\d+(?:\.\d+)?", (text or "").lower())


def _hash_dim(token: str, dim: int) -> int:
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim


class LocalEmbedding:
    """确定性 bag-of-token 哈希向量（无外部依赖；仅离线演示/测试）。"""

    dim = HASH_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in _tokens(text):
                v[_hash_dim(tok, self.dim)] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([round(x / norm, 6) for x in v])
        return out


class EmbeddingClient:
    """OpenAI 兼容 embedding 客户端（/embeddings）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = httpx.post(f"{self.base_url}/embeddings",
                       headers={"Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json"},
                       json={"model": self.model, "input": texts}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [it["embedding"] for it in items]


class LocalModelEmbedding:
    """本地开源中文 embedding（fastembed + BAAI/bge-small-zh-v1.5，免费、无需付费 API）。

    懒加载；首次调用会下载模型（约 100MB）。加载/推理失败抛异常，由 Embedder 降级 hash。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        result: list[list[float]] = []
        for vec in model.embed(list(texts)):
            result.append([float(x) for x in vec])
        return result


class Embedder:
    """统一门面：primary 成功则用之，否则降级 hash。"""

    def __init__(self, primary=None, fallback: LocalEmbedding | None = None):
        self.primary = primary
        self.fallback = fallback or LocalEmbedding()
        self.degraded = primary is None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.primary is not None:
            try:
                return self.primary.embed(texts)
            except Exception:
                self.degraded = True
        return self.fallback.embed(texts)


def make_embedder(settings) -> Embedder:
    """从配置构造 embedder：有 EMBEDDING_API_KEY 用付费 API；否则按 EMBEDDING_BACKEND。"""
    key = getattr(settings, "embedding_api_key", "") or ""
    backend = getattr(settings, "embedding_backend", "local_model") or "local_model"
    model = getattr(settings, "embedding_model", "BAAI/bge-small-zh-v1.5") or "BAAI/bge-small-zh-v1.5"
    if key:
        return Embedder(EmbeddingClient(
            getattr(settings, "embedding_base_url", "https://api.openai.com/v1"),
            key, model, getattr(settings, "embedding_timeout", 30.0)))
    if backend == "local_model":
        return Embedder(LocalModelEmbedding(model))
    return Embedder(None)  # hash 降级
