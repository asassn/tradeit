"""Tests for the request rate ceiling.

Written after an overrun, not before one. The first SIC fetch set a worker
count from a measured throughput and had no cap; latency improved, the rate
climbed to 11.8/s against a published limit of 10, and sec.gov answered with
889 HTTP 429s and then throttled the address. A worker count is a guess about
latency, and it stops being true the moment latency changes.
"""

from __future__ import annotations

import importlib.util
import pathlib
import threading
import time

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "research01_fetch_sic",
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "research01_fetch_sic.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
RateGate = _MODULE.RateGate


class TestTheCeilingHolds:
    def test_a_burst_is_paced_to_the_stated_rate(self) -> None:
        gate = RateGate(per_second=20.0)
        started = time.monotonic()
        for _ in range(10):
            gate.wait()
        elapsed = time.monotonic() - started
        # Ten slots at 20/s is nine intervals of 50ms once the first is free.
        assert elapsed >= 0.40
        assert elapsed < 1.0

    def test_it_holds_across_threads_not_just_within_one(self) -> None:
        """The failure that happened: four workers, no shared ceiling."""
        gate = RateGate(per_second=20.0)
        started = time.monotonic()

        def burst() -> None:
            for _ in range(5):
                gate.wait()

        threads = [threading.Thread(target=burst) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.monotonic() - started
        # Twenty slots at 20/s, regardless of how many threads asked.
        assert elapsed >= 0.90

    def test_a_slow_caller_is_never_delayed_by_the_gate(self) -> None:
        """The ceiling caps the rate; it must not add latency below it."""
        gate = RateGate(per_second=1000.0)
        started = time.monotonic()
        for _ in range(3):
            gate.wait()
            time.sleep(0.01)
        assert time.monotonic() - started < 0.2

    def test_a_non_positive_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            RateGate(per_second=0)


class TestThrottlePagesAreNotEvidence:
    def test_an_error_page_is_not_read_as_a_filing_without_a_sic(self) -> None:
        """A missing tag in an HTML error page says nothing about the filing.

        The first run could not tell the two apart, and its "no SIC in header"
        tally climbed from 1% to 18% as throttling began -- which is how the
        overrun was noticed at all.
        """
        markers = _MODULE._HEADER_MARKERS
        throttle = "<html><h1>Your Request Originates from an Undeclared Automated Tool</h1>"
        assert not any(marker in throttle for marker in markers)

    def test_a_real_header_is_recognised(self) -> None:
        markers = _MODULE._HEADER_MARKERS
        header = "<SEC-HEADER>0001193125-09-153165.hdr.sgml\n<ASSIGNED-SIC>3571\n"
        assert any(marker in header for marker in markers)

    def test_the_older_text_header_is_recognised_too(self) -> None:
        markers = _MODULE._HEADER_MARKERS
        older = "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\t6211\n"
        assert any(marker in older for marker in markers)
