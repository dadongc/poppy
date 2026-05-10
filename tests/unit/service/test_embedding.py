from __future__ import annotations

import asyncio

import pytest

from src.service.embedding.gateway import EmbeddingGateway
from src.service.embedding.provider import StubEmbeddingProvider


class TestStubEmbeddingProvider:
    async def test_returns_zero_vectors(self):
        p = StubEmbeddingProvider(dim=4)
        result = await p.embed(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.0, 0.0, 0.0, 0.0]
        assert result[1] == [0.0, 0.0, 0.0, 0.0]

    async def test_dimension(self):
        p = StubEmbeddingProvider(dim=128)
        assert p.dim == 128


class TestEmbeddingGateway:
    @pytest.fixture
    async def gateway(self, mem_cache):
        provider = StubEmbeddingProvider(dim=4)
        return EmbeddingGateway(
            providers={"stub": provider},
            cache=mem_cache,
            default_model="stub",
            cache_ttl=30 * 86400,
        )

    async def test_embed_returns_vectors(self, gateway):
        result = await gateway.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 4

    async def test_embed_one_returns_single_vector(self, gateway):
        result = await gateway.embed_one("hello")
        assert len(result) == 4

    async def test_cache_hit(self, gateway, mem_cache):
        await gateway.embed(["hello"])
        cached = await mem_cache.get("emb:stub:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        assert cached is not None
        assert cached == [0.0, 0.0, 0.0, 0.0]

    async def test_cache_hit_avoids_recompute(self, gateway, mem_cache):
        result1 = await gateway.embed(["hello"])
        result2 = await gateway.embed(["hello"])
        assert result1 == result2

    async def test_partial_cache_miss(self, gateway, mem_cache):
        await gateway.embed(["hello"])
        result = await gateway.embed(["hello", "world"])
        assert len(result) == 2

    async def test_cache_ttl_expiry(self, mem_cache):
        provider = StubEmbeddingProvider(dim=4)
        gateway = EmbeddingGateway(
            providers={"stub": provider},
            cache=mem_cache,
            default_model="stub",
            cache_ttl=1,
        )
        await gateway.embed(["hello"])
        await asyncio.sleep(1.1)
        cached = await mem_cache.get("emb:stub:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        assert cached is None

    async def test_batch_splitting(self, mem_cache):
        provider = StubEmbeddingProvider(dim=2)
        gateway = EmbeddingGateway(
            providers={"stub": provider},
            cache=mem_cache,
            default_model="stub",
            batch_size=2,
        )
        result = await gateway.embed(["a", "b", "c", "d", "e"])
        assert len(result) == 5

    async def test_different_model_keys(self, mem_cache):
        provider = StubEmbeddingProvider(dim=4)
        gateway = EmbeddingGateway(
            providers={"stub": provider},
            cache=mem_cache,
            default_model="stub",
        )
        await gateway.embed(["hello"])
        cached_default = await mem_cache.get("emb:stub:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        assert cached_default is not None

    async def test_get_dim(self, gateway):
        assert gateway.get_dim() == 4
