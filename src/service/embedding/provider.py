from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class StubEmbeddingProvider:
    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]
