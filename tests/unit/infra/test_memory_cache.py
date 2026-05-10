from __future__ import annotations

import asyncio


class TestMemoryCache:
    async def test_set_and_get(self, mem_cache):
        await mem_cache.set("k1", "v1")
        assert await mem_cache.get("k1") == "v1"

    async def test_get_missing(self, mem_cache):
        assert await mem_cache.get("nope") is None

    async def test_delete(self, mem_cache):
        await mem_cache.set("k1", "v1")
        await mem_cache.delete("k1")
        assert await mem_cache.get("k1") is None

    async def test_ttl_expiry(self, mem_cache):
        await mem_cache.set("k1", "v1", ttl=1)
        assert await mem_cache.get("k1") == "v1"
        await asyncio.sleep(1.1)
        assert await mem_cache.get("k1") is None

    async def test_incr(self, mem_cache):
        val = await mem_cache.incr("counter")
        assert val == 1
        val = await mem_cache.incr("counter", amount=3)
        assert val == 4

    async def test_incr_new_key(self, mem_cache):
        val = await mem_cache.incr("new_counter", amount=5)
        assert val == 5

    async def test_lru_eviction(self, mem_cache):
        mem_cache._max_size = 3
        for i in range(5):
            await mem_cache.set(f"k{i}", f"v{i}")
        # The first two should be evicted
        assert await mem_cache.get("k0") is None
        assert await mem_cache.get("k1") is None
        assert await mem_cache.get("k4") == "v4"
