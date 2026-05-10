from __future__ import annotations

import os

from src.common.config import AppConfig
from src.common.types import Services
from src.infra.factory import Infra
from src.service.artifact import ArtifactStore, ArtifactSummarizer
from src.service.embedding.bge_provider import BgeEmbeddingProvider
from src.service.embedding.gateway import EmbeddingGateway
from src.service.embedding.openai_provider import OpenAIEmbeddingProvider
from src.service.embedding.provider import EmbeddingProvider, StubEmbeddingProvider
from src.service.kb.chunker import Chunker
from src.service.kb.service import KBService
from src.service.llm_protocol import LLMService, StubLLM
from src.service.memory.extractor import MemoryExtractor
from src.service.memory.service import MemoryService
from src.service.retriever import Retriever
from src.service.session import SessionService


def _build_embedding_providers(cfg: AppConfig) -> dict[str, EmbeddingProvider]:
    emb = cfg.embedding
    provider_name = emb.get("provider", "deepseek")
    model = emb.get("model", "text-embedding-3-small")
    dim = emb.get("dim", 1536)

    if provider_name == "openai":
        return {
            model: OpenAIEmbeddingProvider(
                api_key=os.environ.get("OPENAI_API_KEY", ""), model=model
            )
        }
    if provider_name == "bge":
        return {model: BgeEmbeddingProvider(model_name=model)}
    # dev/fallback: stub
    return {model: StubEmbeddingProvider(dim=dim)}


async def build_services(
    infra: Infra,
    llm: LLMService | None = None,
    cfg: AppConfig | None = None,
) -> Services:
    _llm = llm or StubLLM()

    # EmbeddingGateway
    providers = _build_embedding_providers(cfg) if cfg else {}
    default_model = (
        cfg.embedding.get("model", "stub") if cfg else "stub"
    )
    embedding = EmbeddingGateway(
        providers=providers,
        cache=infra.cache,
        default_model=default_model or "stub",
    )

    # ArtifactStore
    summarizer = ArtifactSummarizer(_llm)
    artifact = ArtifactStore(
        store=infra.relational,
        blob=infra.blob,
        event_bus=infra.eventbus,
        summarizer=summarizer,
    )

    # KBService
    chunker = Chunker()
    kb = KBService(
        store=infra.relational,
        artifact=artifact,
        jobs=infra.jobs,
        event_bus=infra.eventbus,
        embedding=embedding,
        vector=infra.vector,
        keyword=infra.keyword,
        chunker=chunker,
    )

    # MemoryService
    extractor = MemoryExtractor(_llm)
    memory = MemoryService(
        store=infra.relational,
        vector=infra.vector,
        keyword=infra.keyword,
        embedding=embedding,
        jobs=infra.jobs,
        event_bus=infra.eventbus,
        llm=_llm,
        extractor=extractor,
    )

    # SessionService
    session = SessionService(
        store=infra.relational,
        cache=infra.cache,
        event_bus=infra.eventbus,
        jobs=infra.jobs,
        llm=_llm,
    )

    # Retriever
    retriever = Retriever(
        memory=memory,
        kb_vector=infra.vector,
        kb_keyword=infra.keyword,
        embedding=embedding,
        store=infra.relational,
    )

    # Init service tables (CREATE TABLE IF NOT EXISTS)
    await session.init()
    await artifact.init()
    await memory.init()
    await kb.init()

    return Services(
        session=session,
        memory=memory,
        artifact=artifact,
        kb=kb,
        retriever=retriever,
        embedding=embedding,
    )
