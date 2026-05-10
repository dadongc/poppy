from __future__ import annotations

from ulid import ULID


def new_id(prefix: str = "") -> str:
    uid = str(ULID())
    return f"{prefix}_{uid}" if prefix else uid


def run_id() -> str:
    return new_id("run")


def session_id() -> str:
    return new_id("ses")


def msg_id() -> str:
    return new_id("msg")


def event_id() -> str:
    return new_id("evt")


def artifact_id() -> str:
    return new_id("atf")


def memory_id() -> str:
    return new_id("mem")


def kb_doc_id() -> str:
    return new_id("doc")


def kb_chunk_id() -> str:
    return new_id("ck")


def job_id() -> str:
    return new_id("job")


RUN_ID = run_id
SESSION_ID = session_id
MSG_ID = msg_id
EVENT_ID = event_id
ARTIFACT_ID = artifact_id
MEMORY_ID = memory_id
KB_DOC_ID = kb_doc_id
KB_CHUNK_ID = kb_chunk_id
JOB_ID = job_id
