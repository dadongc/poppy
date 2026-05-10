from __future__ import annotations

import asyncio
import hashlib

from src.infra.protocols import Cache  # noqa: TCH004
from src.service.embedding.provider import EmbeddingProvider  # noqa: TCH004


class EmbeddingGateway:
    def __init__(
        self,
        *,
        providers: dict[str, EmbeddingProvider],
        cache: Cache,
        default_model: str,
        batch_size: int = 32,
        cache_ttl: int = 30 * 86400,
    ) -> None:
        self._providers = providers
        self._cache = cache
        self._default = default_model
        self._batch_size = batch_size
        self._cache_ttl = cache_ttl

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or self._default
        provider = self._providers[model]

        keys = [self._cache_key(model, t) for t in texts]
        cached = await asyncio.gather(*(self._cache.get(k) for k in keys))
        results: list[list[float] | None] = list(cached)

        missing_idx = [i for i, v in enumerate(results) if v is None]
        if not missing_idx:
            return results  # type: ignore[return-value]

        missing_texts = [texts[i] for i in missing_idx]
        for batch_start in range(0, len(missing_texts), self._batch_size):
            batch = missing_texts[batch_start : batch_start + self._batch_size]
            vectors = await provider.embed(batch)
            for j, vec in enumerate(vectors):
                idx = missing_idx[batch_start + j]
                results[idx] = vec
                await self._cache.set(keys[idx], vec, ttl=self._cache_ttl)
        return results  # type: ignore[return-value]

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        return (await self.embed([text], model))[0]

    def get_dim(self, model: str | None = None) -> int:
        return self._providers[model or self._default].dim

    @property
    def default_model(self) -> str:
        return self._default

    @staticmethod
    def _cache_key(model: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()
        return f"emb:{model}:{h}"
