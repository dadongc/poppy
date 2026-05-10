from __future__ import annotations

import asyncio

from src.common.types import Event


class TestEventBusPublish:
    async def test_publish_subscribe_basic(self, event_bus):
        received: list[Event] = []

        async def consumer():
            async with event_bus.subscribe(filter={"run_id": "r1"}) as sub:
                async for ev in sub:
                    received.append(ev)
                    if len(received) >= 2:
                        break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        await event_bus.publish(
            Event(event_id="e1", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        )
        await event_bus.publish(
            Event(event_id="e2", type="t", run_id="r2", session_id="s1", user_id="u1", ts=1.0)
        )
        await event_bus.publish(
            Event(event_id="e3", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        )

        await asyncio.wait_for(task, timeout=1.0)
        assert [e.event_id for e in received] == ["e1", "e3"]

    async def test_seq_monotonic(self, event_bus):
        e1 = Event(event_id="e1", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        e2 = Event(event_id="e2", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        e3 = Event(event_id="e3", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        await event_bus.publish(e1)
        await event_bus.publish(e2)
        await event_bus.publish(e3)
        assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)

    async def test_filter_by_type(self, event_bus):
        received: list[Event] = []

        async def consumer():
            async with event_bus.subscribe(filter={"type": "type_a"}) as sub:
                async for ev in sub:
                    received.append(ev)
                    if len(received) >= 1:
                        break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        await event_bus.publish(
            Event(event_id="e1", type="type_b", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        )
        await event_bus.publish(
            Event(event_id="e2", type="type_a", run_id="r2", session_id="s1", user_id="u1", ts=1.0)
        )

        await asyncio.wait_for(task, timeout=1.0)
        assert len(received) == 1
        assert received[0].event_id == "e2"

    async def test_auto_fill_ids(self, event_bus):
        e = Event(event_id="", type="t", run_id="r1", session_id="s1", user_id="u1", ts=0.0)
        await event_bus.publish(e)
        assert e.event_id != ""
        assert e.ts > 0.0

    async def test_multiple_subscribers(self, event_bus):
        recv_a: list[Event] = []
        recv_b: list[Event] = []

        async def consumer_a():
            async with event_bus.subscribe(filter={"run_id": "r1"}) as sub:
                async for ev in sub:
                    recv_a.append(ev)
                    if len(recv_a) >= 1:
                        break

        async def consumer_b():
            async with event_bus.subscribe(filter={"run_id": "r1"}) as sub:
                async for ev in sub:
                    recv_b.append(ev)
                    if len(recv_b) >= 1:
                        break

        ta = asyncio.create_task(consumer_a())
        tb = asyncio.create_task(consumer_b())
        await asyncio.sleep(0.05)

        await event_bus.publish(
            Event(event_id="e1", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        )

        await asyncio.wait_for(asyncio.gather(ta, tb), timeout=1.0)
        assert len(recv_a) == 1
        assert len(recv_b) == 1

    async def test_replay(self, event_bus):
        await event_bus.publish(
            Event(event_id="e1", type="t", run_id="r1", session_id="s1", user_id="u1", ts=1.0)
        )
        await event_bus.publish(
            Event(event_id="e2", type="t", run_id="r1", session_id="s1", user_id="u1", ts=2.0)
        )

        # Give persistence a moment
        await asyncio.sleep(0.1)

        events = []
        async for ev in event_bus.replay("r1", since_seq=0):
            events.append(ev)
        assert len(events) >= 2

        events2 = []
        async for ev in event_bus.replay("r1", since_seq=1):
            events2.append(ev)
        assert len(events2) >= 1
