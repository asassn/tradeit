"""Asking sec.gov for things politely, from one place.

:class:`RateGate` began life inside ``scripts/research01_fetch_sic.py``, written
after that script's first full run overran: four workers sized from a measured
6.25 requests a second, latency improved, throughput climbed to 11.8/s against
a published limit of 10, and sec.gov answered with 889 HTTP 429s and then
throttled the address outright. It moved here when a second fetcher needed it,
because two copies of a rate limit are two chances for one of them to be wrong.

**A worker count is not a rate limit.** It is a guess about latency, and it
stops being true the moment latency changes. The gate enforces an aggregate
ceiling across every thread regardless of how fast any one request returns.
"""

from __future__ import annotations

import threading
import time

__all__ = ["DEFAULT_RATE", "HEADER_MARKERS", "RateGate", "header_url"]

#: Half the published ten-per-second limit. The cost of being slow is minutes;
#: the cost of being rude is an address that stops being served.
DEFAULT_RATE = 5.0


#: Strings a genuine SGML header contains. sec.gov serves its throttle page with
#: HTTP **200**, so a status check alone reads "Your Request Originates from an
#: Undeclared Automated Tool" as a successful fetch of a header with nothing in
#: it -- which is how the first SIC overrun was nearly misread as "no SIC".
HEADER_MARKERS = ("<SEC-HEADER>", "<ASSIGNED-SIC>", "STANDARD INDUSTRIAL CLASSIFICATION")


class RateGate:
    """An aggregate request ceiling, shared across worker threads.

    Enforced rather than estimated. Workers pace themselves against a shared
    next-slot time, so the rate holds whatever the latency does.
    """

    def __init__(self, per_second: float) -> None:
        if per_second <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


def header_url(cik: str | int, accession: str) -> str:
    """The ``.hdr.sgml`` beside a submission: about 900 bytes, not megabytes.

    Measured at 906 bytes against filings of 2.4 MB and 10.3 MB. An HTTP
    ``Range`` request was tried first and sec.gov ignored it.
    """
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}.hdr.sgml"
    )
