from __future__ import annotations


class TestFilesystemBackend:
    async def test_put_and_get(self, fs_backend):
        uri = await fs_backend.put("test.txt", b"hello world")
        assert uri.startswith("fs://")

        data = await fs_backend.get("test.txt")
        assert data == b"hello world"

    async def test_exists(self, fs_backend):
        assert not await fs_backend.exists("missing.txt")

        await fs_backend.put("exists.txt", b"data")
        assert await fs_backend.exists("exists.txt")

    async def test_delete(self, fs_backend):
        await fs_backend.put("delete_me.txt", b"data")
        assert await fs_backend.exists("delete_me.txt")

        await fs_backend.delete("delete_me.txt")
        assert not await fs_backend.exists("delete_me.txt")

    async def test_put_stream_and_get_stream(self, fs_backend):
        async def chunks():
            yield b"hello "
            yield b"world"

        uri = await fs_backend.put_stream("stream.txt", chunks())
        assert uri.startswith("fs://")

        data = b""
        async for chunk in fs_backend.get_stream("stream.txt"):
            data += chunk
        assert data == b"hello world"

    async def test_signed_url(self, fs_backend):
        await fs_backend.put("sign.txt", b"data")
        url = await fs_backend.signed_url("sign.txt")
        assert url.startswith("fs://")

    async def test_key_escape(self, fs_backend):
        import pytest

        with pytest.raises(ValueError):
            await fs_backend.put("../escape.txt", b"bad")
