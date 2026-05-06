from __future__ import annotations

from openai import AsyncOpenAI


class OpenAIEmbeddingProvider:
    def __init__(self, *, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self.dim = 1536 if "small" in model else 3072

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]
