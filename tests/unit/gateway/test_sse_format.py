from __future__ import annotations

import json

from src.common.types import Event, EventType
from src.gateway.sse import (
    PUBLIC_EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    TERMINAL_STATES,
    _format_sse,
)


class TestSSEFormat:
    def test_format_sse_basic(self):
        ev = Event(
            event_id="evt_1",
            type=EventType.LLM_TEXT_DELTA,
            run_id="run_1",
            seq=5,
            ts=1000.0,
            payload={"text": "hello"},
        )
        result = _format_sse(ev)
        assert result.startswith("id: 5\n")
        assert "event: llm.text_delta\n" in result
        assert "hello" in result

    def test_format_sse_json_encodable(self):
        ev = Event(
            event_id="evt_1",
            type=EventType.RUN_STARTED,
            run_id="run_1",
            seq=1,
            ts=1000.0,
            payload={"agent_name": "default"},
        )
        result = _format_sse(ev)
        # Should be parsable as SSE
        lines = result.strip().split("\n")
        assert len(lines) == 3  # id, event, data
        data_line = lines[2]
        assert data_line.startswith("data: ")
        data = json.loads(data_line[6:])
        assert data["payload"]["agent_name"] == "default"


class TestPublicEventTypes:
    def test_run_events_are_public(self):
        assert EventType.RUN_STARTED in PUBLIC_EVENT_TYPES
        assert EventType.RUN_COMPLETED in PUBLIC_EVENT_TYPES
        assert EventType.RUN_FAILED in PUBLIC_EVENT_TYPES
        assert EventType.RUN_CANCELLED in PUBLIC_EVENT_TYPES
        assert EventType.RUN_TIMEOUT in PUBLIC_EVENT_TYPES

    def test_llm_events_are_public(self):
        assert EventType.LLM_TEXT_DELTA in PUBLIC_EVENT_TYPES
        assert EventType.LLM_TOOL_CALL_START in PUBLIC_EVENT_TYPES
        assert EventType.LLM_TOOL_CALL_END in PUBLIC_EVENT_TYPES
        assert EventType.LLM_USAGE in PUBLIC_EVENT_TYPES

    def test_tool_events_are_public(self):
        assert EventType.TOOL_STARTED in PUBLIC_EVENT_TYPES
        assert EventType.TOOL_COMPLETED in PUBLIC_EVENT_TYPES
        assert EventType.TOOL_FAILED in PUBLIC_EVENT_TYPES

    def test_subagent_events_are_public(self):
        assert EventType.SUBAGENT_STARTED in PUBLIC_EVENT_TYPES
        assert EventType.SUBAGENT_COMPLETED in PUBLIC_EVENT_TYPES

    def test_internal_events_not_public(self):
        # Session, memory, KB events should NOT be in public set
        assert EventType.SESSION_MESSAGE_ADDED not in PUBLIC_EVENT_TYPES
        assert EventType.MEMORY_EXTRACTED not in PUBLIC_EVENT_TYPES
        assert EventType.MEMORY_WRITTEN not in PUBLIC_EVENT_TYPES
        assert EventType.KB_DOC_INGESTING not in PUBLIC_EVENT_TYPES
        assert EventType.KB_DOC_READY not in PUBLIC_EVENT_TYPES


class TestTerminalDetection:
    def test_terminal_states(self):
        assert "completed" in TERMINAL_STATES
        assert "failed" in TERMINAL_STATES
        assert "cancelled" in TERMINAL_STATES
        assert "timeout" in TERMINAL_STATES

    def test_terminal_events(self):
        assert EventType.RUN_COMPLETED in TERMINAL_EVENT_TYPES
        assert EventType.RUN_FAILED in TERMINAL_EVENT_TYPES
        assert EventType.RUN_CANCELLED in TERMINAL_EVENT_TYPES
        assert EventType.RUN_TIMEOUT in TERMINAL_EVENT_TYPES
