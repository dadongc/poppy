"""Phase 3 验收入口：python -m src.runtime.cli --agent default --message "你好"

在没有 Gateway 的情况下端到端验证 Agent 编排层。
"""
from __future__ import annotations

import argparse
import asyncio

from src.common.types import EventType
from src.runtime.runtime import Runtime


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Poppy Agent CLI")
    parser.add_argument("--agent", default="default", help="Agent name")
    parser.add_argument("--message", "-m", required=True, help="User message")
    parser.add_argument("--config", default="config/dev.yaml", help="Config path")
    parser.add_argument("--user", default="cli-user", help="User ID")
    args = parser.parse_args()

    print(f"[Poppy] Initializing with config: {args.config}")
    rt = await Runtime.initialize(args.config)
    print(f"[Poppy] Agent: {args.agent}")
    print(f"[Poppy] User:  {args.message}")
    print("-" * 40)

    run_id = await rt.start_run(
        agent_name=args.agent,
        user_id=args.user,
        user_message=args.message,
    )

    # Subscribe to events for streaming output
    sub = rt.event_bus.subscribe(
        filter={"run_id": run_id}
    )
    try:
        async with sub:
            async for ev in sub:
                if ev.type == EventType.LLM_TEXT_DELTA:
                    print(ev.payload.get("text", ""), end="", flush=True)
                elif ev.type == EventType.LLM_TOOL_CALL_START:
                    name = ev.payload.get("name", "?")
                    print(f"\n[Tool: {name}]", flush=True)
                    print("  ", end="", flush=True)
                elif ev.type == EventType.TOOL_COMPLETED:
                    tool_name = ev.payload.get("tool_name", "?")
                    duration = ev.payload.get("duration_ms", 0)
                    print(f"\n[Tool done: {tool_name} ({duration}ms)]", flush=True)
                elif ev.type in (
                    EventType.RUN_COMPLETED,
                    EventType.RUN_FAILED,
                    EventType.RUN_CANCELLED,
                ):
                    print()
                    print("-" * 40)
                    print(f"[Poppy] Run {ev.type}: {run_id}")
                    break
    finally:
        await sub.aclose()

    await rt.shutdown()
    print("[Poppy] Done.")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
