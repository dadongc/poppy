"""Service 层 smoke test — 需要能连 PG/Redis/OSS 的 prod 配置才能跑通。

用法：python -m src.service.demo [config/prod.yaml]
"""
from __future__ import annotations

import asyncio
import sys

from src.common.config import load_config
from src.common.types import Message
from src.infra.factory import build_infra
from src.service.container import build_services


async def demo(config_path: str = "config/prod.yaml") -> None:
    print(f"Loading config: {config_path}")
    cfg = load_config(config_path)

    print("Building infra...")
    infra = await build_infra(cfg.infra.model_dump(), run_migrations_flag=True)
    print("  relational:\tOK")
    print("  vector:\tOK")
    print("  keyword:\tOK")
    print("  blob:\t\tOK")
    print("  cache:\tOK")
    print("  eventbus:\tOK")

    print("Building services...")
    services = await build_services(infra, cfg=cfg)
    assert services.session is not None
    assert services.memory is not None
    assert services.artifact is not None
    assert services.kb is not None
    assert services.retriever is not None
    assert services.embedding is not None
    print("  session:\tOK")
    print("  memory:\tOK")
    print("  artifact:\tOK")
    print("  kb:\t\tOK")
    print("  retriever:\tOK")
    print("  embedding:\tOK")

    # --- Session ---
    print("\n--- Session ---")
    sess = await services.session.create("u1", title="Demo Session")
    print(f"  created: {sess.session_id}")
    msg = await services.session.append_message(
        sess.session_id, "u1", Message(role="user", content="Hello Poppy!")
    )
    print(f"  appended msg seq={msg.seq}")
    window = await services.session.get_window_for_context(sess.session_id, "u1")
    print(f"  window: {len(window.messages)} msgs, summary={window.summary[:50] if window.summary else '(empty)'}")

    # --- Artifact ---
    print("\n--- Artifact ---")
    art = await services.artifact.save(
        user_id="u1",
        content="# Test Document\n\nThis is a test document.",
        mime_type="text/markdown",
        title="Test Doc",
    )
    print(f"  saved: {art.artifact_id}, hash={art.content_hash[:16]}")
    fetched = await services.artifact.get_text(art.artifact_id, "u1")
    print(f"  fetched: {len(fetched)} chars")
    ref = await services.artifact.render_reference(art)
    print(f"  reference: {ref[:80]}...")

    # --- KB ---
    print("\n--- KB ---")
    doc = await services.kb.add_document(
        user_id="u1", artifact_id=art.artifact_id, title="From Demo"
    )
    print(f"  doc: {doc.doc_id}, state={doc.state}, chunks={doc.chunk_count}")

    # --- Memory ---
    print("\n--- Memory ---")
    mem = await services.memory.remember(
        user_id="u1", kind="fact", content="Poppy 是一个 AI 助手项目"
    )
    print(f"  remembered: {mem.memory_id}")
    recalled = await services.memory.recall("u1", "AI 助手")
    print(f"  recalled: {len(recalled)} memories")
    for r in recalled[:3]:
        print(f"    - [{r.kind}] {r.content[:60]}")

    # --- Retriever ---
    print("\n--- Retriever ---")
    from src.common.types import RetrievalQuery

    q = RetrievalQuery(text="test document", user_id="u1", channels=["kb", "memory"], top_k=5)
    hits = await services.retriever.search(q)
    print(f"  hits: {len(hits)}")
    for h in hits[:3]:
        print(f"    - [{h.channel}] score={h.score:.3f} text={h.text[:50]}")

    print("\n=== ALL OK ===")

    await infra.relational.close()
    await infra.eventbus.shutdown()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "config/prod.yaml"
    asyncio.run(demo(path))
