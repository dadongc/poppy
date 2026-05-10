from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
import aiofiles.os


class FilesystemBackend:
    """Local filesystem storage backend for development."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root.resolve())):
            raise ValueError(f"key escapes root: {key}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def put(self, key: str, data: bytes, mime_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return f"fs://{path.absolute()}"

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], mime_type: str = "application/octet-stream"
    ) -> str:
        path = self._path(key)
        async with aiofiles.open(path, "wb") as f:
            async for chunk in stream:
                await f.write(chunk)
        return f"fs://{path.absolute()}"

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        async with aiofiles.open(path, "rb") as f:
            while chunk := await f.read(65536):
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await aiofiles.os.remove(path)

    async def exists(self, key: str) -> bool:
        path = self._root / key
        return path.exists()

    async def signed_url(self, key: str, expires_in: int = 3600) -> str:
        return f"fs://{(self._root / key).absolute()}"
