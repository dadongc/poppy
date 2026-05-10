from __future__ import annotations

import asyncio


class BgeEmbeddingProvider:
    def __init__(self, *, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self._model_name = model_name
        self._model: object | None = None
        self.dim = 512

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        from FlagEmbedding import FlagModel  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        self._model = await loop.run_in_executor(
            None, lambda: FlagModel(self._model_name, use_fp16=False)
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await self._ensure_model()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: self._model.encode(texts).tolist())  # type: ignore[union-attr]
        return result
