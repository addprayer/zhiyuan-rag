"""Embedding providers.

Two modes ship with the project:

* ``local``  — a deterministic hashed n-gram vector (768 dims). It requires no
  model download and keeps the first-run experience offline, while still giving
  useful lexical-semantic matching for Chinese.
* ``openai`` — any OpenAI-compatible ``/embeddings`` endpoint, e.g. OpenAI
  ``text-embedding-3-small`` or SiliconFlow ``BAAI/bge-m3``. Recommended for
  production-quality semantic retrieval.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any

import httpx
import numpy as np

from .text_utils import tokenize


class EmbeddingError(RuntimeError):
    pass


def _endpoint(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise EmbeddingError("Embedding API 地址为空")
    if not base.endswith("/v1") and "/v1" not in base:
        base += "/v1"
    return f"{base}/{path.lstrip('/')}"


class LocalHashEmbedder:
    """Signed feature hashing over Chinese unigrams/bigrams and Latin words."""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self.signature = f"local-hash-{dim}-v1"

    def _embed_one(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        counts = Counter(tokenize(text))
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            index = value % self.dim
            sign = 1.0 if (value >> 63) & 1 else -1.0
            length_boost = 1.35 if len(token) > 1 else 0.65
            weight = (1.0 + math.log(count)) * length_boost
            vector[index] += sign * weight
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed_one(text)


class OpenAIEmbedder:
    def __init__(self, base_url: str, api_key: str, model: str):
        if not api_key:
            raise EmbeddingError("未配置 Embedding API Key")
        if not model:
            raise EmbeddingError("未配置 Embedding 模型")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.dim = 0

    @property
    def signature(self) -> str:
        return f"openai:{self.model}@{self.base_url.rstrip('/')}"

    def _request(self, inputs: list[str]) -> list[np.ndarray]:
        url = _endpoint(self.base_url, "embeddings")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"model": self.model, "input": inputs}
        try:
            with httpx.Client(timeout=httpx.Timeout(90, connect=15)) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embedding 请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise EmbeddingError(
                f"Embedding API {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors = [
                np.asarray(item["embedding"], dtype=np.float32) for item in ordered
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding API 返回格式无法解析") from exc
        if len(vectors) != len(inputs):
            raise EmbeddingError("Embedding API 返回数量与输入不一致")
        dim = len(vectors[0])
        if dim == 0 or any(len(v) != dim for v in vectors):
            raise EmbeddingError("Embedding API 返回的向量维度不一致")
        self.dim = dim
        return vectors

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), 32):
            vectors.extend(self._request(texts[start : start + 32]))
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        return self._request([text])[0]


def build_embedder(settings: dict[str, Any]) -> LocalHashEmbedder | OpenAIEmbedder:
    provider = str(settings.get("embedding_provider", "local")).lower()
    if provider == "local":
        return LocalHashEmbedder()
    if provider == "openai":
        return OpenAIEmbedder(
            base_url=str(settings.get("embedding_base_url", "")),
            api_key=str(settings.get("embedding_api_key", "")),
            model=str(settings.get("embedding_model", "")),
        )
    raise EmbeddingError(f"未知的 Embedding Provider：{provider}")


def signature_for(settings: dict[str, Any]) -> str:
    provider = str(settings.get("embedding_provider", "local")).lower()
    if provider == "local":
        return "local-hash-768-v1"
    if provider == "openai":
        return (
            f"openai:{settings.get('embedding_model', '')}"
            f"@{str(settings.get('embedding_base_url', '')).rstrip('/')}"
        )
    return f"unknown:{provider}"


def embedding_label(settings: dict[str, Any]) -> str:
    if str(settings.get("embedding_provider", "local")).lower() == "local":
        return "本地哈希向量 · 768 维"
    return f"{settings.get('embedding_model', 'Embedding')} · API"
