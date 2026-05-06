from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import oss2


class OssBackend:
    """Alibaba Cloud OSS blob storage backend.

    Uses a thread pool to wrap the synchronous OSS2 SDK.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        access_key_secret: str,
        prefix: str = "",
    ) -> None:
        self._auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(self._auth, endpoint, bucket)
        self._bucket_name = bucket
        self._prefix = prefix.rstrip("/")
        self._executor = ThreadPoolExecutor(max_workers=8)

    async def init(self) -> None:
        pass

    async def _run(self, fn, *args, **kw):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(*args, **kw))

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}/{key}".lstrip("/")

    async def put(self, key: str, data: bytes, mime_type: str = "application/octet-stream") -> str:
        full = self._full_key(key)
        await self._run(self._bucket.put_object, full, data, headers={"Content-Type": mime_type})
        return f"oss://{self._bucket_name}/{full}"

    async def put_stream(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        mime_type: str = "application/octet-stream",
    ) -> str:
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return await self.put(key, b"".join(chunks), mime_type)

    async def get(self, key: str) -> bytes:
        full = self._full_key(key)
        result = await self._run(self._bucket.get_object, full)
        return await self._run(result.read)

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        data = await self.get(key)
        yield data

    async def delete(self, key: str) -> None:
        full = self._full_key(key)
        await self._run(self._bucket.delete_object, full)

    async def exists(self, key: str) -> bool:
        full = self._full_key(key)
        try:
            await self._run(self._bucket.head_object, full)
            return True
        except oss2.exceptions.NoSuchKey:
            return False
        except Exception:
            return False

    async def signed_url(self, key: str, expires_in: int = 3600) -> str:
        full = self._full_key(key)
        return await self._run(self._bucket.sign_url, "GET", full, expires_in)
