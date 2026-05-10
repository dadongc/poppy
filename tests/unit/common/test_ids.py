from __future__ import annotations

import time

from src.common.ids import (
    ARTIFACT_ID,
    EVENT_ID,
    JOB_ID,
    KB_CHUNK_ID,
    KB_DOC_ID,
    MEMORY_ID,
    MSG_ID,
    RUN_ID,
    SESSION_ID,
    new_id,
)


class TestNewID:
    def test_no_prefix(self):
        uid = new_id()
        assert isinstance(uid, str)
        assert len(uid) == 26  # ULID length

    def test_with_prefix(self):
        uid = new_id("test")
        assert uid.startswith("test_")

    def test_uniqueness(self):
        ids = {new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_time_ordered(self):
        ids = [new_id() for _ in range(50)]
        time.sleep(0.002)  # ensure different timestamp
        ids += [new_id() for _ in range(50)]
        # ULID is lexicographically time-ordered
        sorted_ids = sorted(ids)
        assert sorted_ids == ids


class TestPrefixHelpers:
    def test_run_id(self):
        assert RUN_ID().startswith("run_")

    def test_session_id(self):
        assert SESSION_ID().startswith("ses_")

    def test_msg_id(self):
        assert MSG_ID().startswith("msg_")

    def test_event_id(self):
        assert EVENT_ID().startswith("evt_")

    def test_artifact_id(self):
        assert ARTIFACT_ID().startswith("atf_")

    def test_memory_id(self):
        assert MEMORY_ID().startswith("mem_")

    def test_kb_doc_id(self):
        assert KB_DOC_ID().startswith("doc_")

    def test_kb_chunk_id(self):
        assert KB_CHUNK_ID().startswith("ck_")

    def test_job_id(self):
        assert JOB_ID().startswith("job_")
