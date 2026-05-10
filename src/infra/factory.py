from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.infra.blob.filesystem import FilesystemBackend
from src.infra.blob.oss import OssBackend
from src.infra.cache.memory_cache import MemoryCache
from src.infra.cache.redis_cache import RedisCache
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.jobs.pg_jobs import PgJobQueue
from src.infra.keyword.fts5 import Fts5Index
from src.infra.keyword.pg_tsvector import PgTsvectorIndex
from src.infra.protocols import (
    Cache,
    EventBus,
    JobQueue,
    KeywordIndex,
    RelationalStore,
    StorageBackend,
    VectorIndex,
)
from src.infra.relational.migrator import run_migrations
from src.infra.relational.postgres import PostgresStore
from src.infra.relational.sqlite import SqliteStore
from src.infra.vector.pgvector import PgVectorIndex
from src.infra.vector.sqlite_vec import SqliteVecIndex


@dataclass(slots=True, kw_only=True)
class Infra:
    relational: RelationalStore
    vector: VectorIndex
    keyword: KeywordIndex
    blob: StorageBackend
    cache: Cache
    eventbus: EventBus
    jobs: JobQueue | None = None


async def build_infra(config: dict, *, run_migrations_flag: bool = True) -> Infra:
    """Assemble infra components from config dict."""

    # Relational
    rel_cfg = config["relational"]
    rel: Any
    if rel_cfg["backend"] == "sqlite":
        rel = SqliteStore(path=rel_cfg["path"])
    elif rel_cfg["backend"] == "postgres":
        if "dsn" in rel_cfg and rel_cfg["dsn"]:
            rel = PostgresStore(
                dsn=rel_cfg["dsn"],
                pool_min=rel_cfg.get("pool_min", 2),
                pool_max=rel_cfg.get("pool_max", 10),
            )
        else:
            rel = PostgresStore(
                host=rel_cfg.get("host", ""),
                port=rel_cfg.get("port", 5432),
                database=rel_cfg.get("database", ""),
                user=rel_cfg.get("user", ""),
                password=rel_cfg.get("password", ""),
                pool_min=rel_cfg.get("pool_min", 2),
                pool_max=rel_cfg.get("pool_max", 10),
            )
    else:
        raise ValueError(f"Unsupported relational backend: {rel_cfg['backend']}")
    await rel.init()

    if run_migrations_flag and rel_cfg["backend"] == "postgres":
        migrations_dir = Path("migrations")
        await run_migrations(rel, migrations_dir)

    # Vector
    vec_cfg = config["vector"]
    vec: Any
    if vec_cfg["backend"] == "sqlite-vec":
        vec = SqliteVecIndex(rel, dim=vec_cfg.get("dim", 1536))
    elif vec_cfg["backend"] == "pgvector":
        vec = PgVectorIndex(
            rel, dim=vec_cfg.get("dim", 1536), metric=vec_cfg.get("metric", "cosine")
        )
    else:
        raise ValueError(f"Unsupported vector backend: {vec_cfg['backend']}")
    await vec.init()

    # Keyword
    kw_cfg = config["keyword"]
    kw: Any
    if kw_cfg["backend"] == "fts5":
        kw = Fts5Index(rel)
    elif kw_cfg["backend"] == "pg_tsvector":
        kw = PgTsvectorIndex(rel, ts_config=kw_cfg.get("config", "zhcfg"))
    else:
        raise ValueError(f"Unsupported keyword backend: {kw_cfg['backend']}")
    await kw.init()

    # Blob
    blob_cfg = config["blob"]
    blob: Any
    if blob_cfg["backend"] == "filesystem":
        blob = FilesystemBackend(root=blob_cfg["root"])
    elif blob_cfg["backend"] == "oss":
        blob = OssBackend(
            endpoint=blob_cfg["endpoint"],
            bucket=blob_cfg["bucket"],
            access_key_id=blob_cfg["access_key_id"],
            access_key_secret=blob_cfg["access_key_secret"],
            prefix=blob_cfg.get("prefix", ""),
        )
    else:
        raise ValueError(f"Unsupported blob backend: {blob_cfg['backend']}")
    await blob.init()

    # Cache
    cache_cfg = config["cache"]
    cache: Any
    if cache_cfg["backend"] == "memory":
        cache = MemoryCache(max_size=cache_cfg.get("max_size", 1000))
    elif cache_cfg["backend"] == "redis":
        if "url" in cache_cfg and cache_cfg["url"]:
            cache = RedisCache(url=cache_cfg["url"])
        else:
            cache = RedisCache(
                host=cache_cfg.get("host", ""),
                port=cache_cfg.get("port", 6379),
                password=cache_cfg.get("password", ""),
                db=cache_cfg.get("db", 0),
            )
    else:
        raise ValueError(f"Unsupported cache backend: {cache_cfg['backend']}")
    await cache.init()

    # EventBus
    eb_cfg = config.get("eventbus", {})
    bus: Any = InProcessEventBus(store=rel, persist=eb_cfg.get("persist", False))
    await bus.init()

    # JobQueue (optional)
    jobs: Any = None
    if config.get("jobs"):
        jobs_cfg = config["jobs"]
        if jobs_cfg["backend"] == "pg_jobs":
            jobs = PgJobQueue(rel)
            await jobs.init()
        else:
            raise ValueError(f"Unsupported jobs backend: {jobs_cfg['backend']}")

    return Infra(
        relational=rel,
        vector=vec,
        keyword=kw,
        blob=blob,
        cache=cache,
        eventbus=bus,
        jobs=jobs,
    )
