"""Embeddings abstraction. Mirrors LLMClient — pluggable so tests inject
a deterministic fake while production uses gemini-embedding-001.

Output dimensionality is fixed at 3072 to match core.few_shot_examples.embedding
(halfvec(3072) with HNSW cosine index).
"""

from __future__ import annotations

from typing import Any, Protocol

from packages.config import settings

EMBEDDING_DIM = 3072


class Embeddings(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class GeminiEmbeddings:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore[import-not-found]

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def embed(self, text: str) -> list[float]:
        client = self._ensure_client()
        result = await client.aio.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        # result.embeddings is a list; we sent one string so take [0].
        return list(result.embeddings[0].values)


class FakeEmbeddings:
    """Deterministic test embeddings: returns a fixed-length vector seeded
    by a hash of the input text. Same text → same vector."""

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        import hashlib
        import random

        seed = int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        # uniform in [-1, 1]; tests just need determinism + distinct vectors
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
