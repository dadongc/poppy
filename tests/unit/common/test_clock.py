from __future__ import annotations

import time

from src.common.clock import now_ms, now_ts


class TestNowTs:
    def test_returns_float(self):
        assert isinstance(now_ts(), float)

    def test_reasonable_value(self):
        ts = now_ts()
        assert ts > 1_700_000_000  # year 2023+

    def test_monotonic(self):
        a = now_ts()
        time.sleep(0.01)
        b = now_ts()
        assert b >= a


class TestNowMs:
    def test_returns_int(self):
        assert isinstance(now_ms(), int)

    def test_reasonable_value(self):
        ms = now_ms()
        assert ms > 1_700_000_000_000

    def test_close_to_now_ts(self):
        ts = now_ts()
        ms = now_ms()
        assert abs((ms / 1000) - ts) < 1.0
