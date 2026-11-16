"""Keep the local EDGAR full-index current, without ever installing a worse file.

**Why this exists.** Measured 2026-11-15: the local index held filings only to
2026-08-13. The 2026 QTR3 file had been downloaded mid-quarter on 2026-08-14 and
never replaced; QTR4 did not exist. Three months of filings were missing, and
**nothing reported it** -- so `tradeit.forward.silence` answered "no exit found"
about companies whose deregistration filings were simply not in the archive.
The classifier was not wrong; it was answering from absent data.

`FORWARD_SILENCE_CLASSIFICATION_2026-11-15.md` closed the reporting half: the
index's age now prints every night. This closes the other half.

Two rules, and both exist because the manual refresh nearly broke them
---------------------------------------------------------------------

**1. Never install a file that knows less than the one it replaces.** A refresh
is only ever meant to move forward. A truncated download, a partial response or
an SEC-side problem would otherwise overwrite three months of good index with a
header and a stack trace -- and the nightly job would then report the archive as
current, because the file it just wrote is new.

**2. Never destroy a withdrawn line silently.** Replacing 2026 QTR3 by hand
removed **15 lines SEC no longer serves in that file** -- all ownership, 13G and
13F forms. A withdrawn index line is evidence about EDGAR, and a refresh that
discarded it would be rewriting history to make a problem disappear. The
superseded file is kept and the lost lines are written out beside it.

No shell, by design
-------------------

`ops/forward_daily.py` documents why at length: macOS TCC grants Documents
access **per executable**, `/bin/bash` does not have it under ``launchd``, and
the framework interpreter does. The published fetch recipe is ``curl`` in a
shell loop, which is right for an operator at a terminal and would fail silently
at 22:10. This fetches with :mod:`urllib` from the same interpreter that already
holds the grant.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "IndexSnapshot",
    "RefreshAction",
    "RefreshOutcome",
    "fetch_quarter",
    "full_index_url",
    "inspect_index",
    "quarters_to_refresh",
    "refresh_quarter",
]

_BASE = "https://www.sec.gov/Archives/edgar/full-index"
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

#: How long to wait for SEC. Generous, because a slow night is better than a
#: missed refresh, and the job has nothing else to do while it waits.
TIMEOUT_SECONDS = 120


class RefreshAction(StrEnum):
    """What a refresh did to one quarter."""

    INSTALLED = "installed"
    UNCHANGED = "unchanged"
    #: Fetched, and **rejected** because it knew less than what is held. The
    #: existing file stands.
    REFUSED = "refused"
    #: Could not be fetched at all. Also leaves the existing file alone.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """What one ``form.idx`` holds, measured from its rows.

    ``newest`` is read from the dated lines rather than the file's
    ``Last Data Received`` header. The header is the file's claim about itself,
    and this module exists because such a claim went three months out of date
    with nobody checking.
    """

    dated_lines: int
    newest: dt.date | None

    @property
    def usable(self) -> bool:
        """Whether this is an index at all rather than an error page.

        A response with no dated line is not a thin index; it is not an index.
        """
        return self.dated_lines > 0 and self.newest is not None


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    quarter: str
    action: RefreshAction
    reason: str
    held: IndexSnapshot | None = None
    offered: IndexSnapshot | None = None
    #: Lines the superseded file had that the new one does not. Preserved
    #: alongside, never only counted.
    withdrawn: tuple[str, ...] = ()

    def describe(self) -> str:
        line = f"  {self.quarter}: {self.action} -- {self.reason}"
        if self.withdrawn:
            line += f"\n    {len(self.withdrawn)} line(s) SEC no longer serves, preserved"
        return line


def full_index_url(year: int, quarter: int) -> str:
    return f"{_BASE}/{year}/QTR{quarter}/form.idx"


def inspect_index(text: str) -> IndexSnapshot:
    """Measure a ``form.idx``'s contents without parsing it into rows.

    Deliberately cruder than :func:`tradeit.edgar.index.parse_full_index`: the
    only questions here are *is this an index* and *how far does it reach*, and
    a refresh must not refuse a file because a strict row parser disliked one
    line of it.
    """
    newest: dt.date | None = None
    dated = 0
    for match in _DATE.finditer(text):
        try:
            day = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            # EDGAR headers carry things like `9997-26-00`. Not a date, not a
            # row; skipping it is the whole reason this is a regex and not
            # `date.fromisoformat`.
            continue
        dated += 1
        if newest is None or day > newest:
            newest = day
    return IndexSnapshot(dated_lines=dated, newest=newest)


def quarters_to_refresh(today: dt.date) -> list[tuple[int, int]]:
    """The current quarter and the one before it.

    **Two, not one.** On 2026-10-01 the current quarter holds a single day while
    the previous quarter's file -- downloaded at some point mid-quarter -- is
    permanently incomplete and would never be revisited. That is exactly how
    2026 QTR3 came to stop at 2026-08-13 and stay there. Refreshing the previous
    quarter costs one request and closes the hole for good.
    """
    year, quarter = today.year, (today.month - 1) // 3 + 1
    previous = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    return [previous, (year, quarter)]


def fetch_quarter(year: int, quarter: int, *, user_agent: str) -> str:
    """GET one quarter's ``form.idx``. Raises on anything but success.

    ``user_agent`` is **required and has no default.** SEC fair-access guidance
    requires a descriptive identifying User-Agent, and an unidentified automated
    client is both rude and liable to be blocked. The operator supplies it as
    `EDGAR_USER_AGENT`; it is never logged, because it carries contact details.
    """
    import urllib.request

    if not user_agent.strip():
        raise ValueError("refusing to fetch from sec.gov without an identifying User-Agent")
    # The URL is built here from two integers against a fixed https host; it
    # never comes from a caller, a file or a response.
    request = urllib.request.Request(
        full_index_url(year, quarter),
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        import gzip

        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        import zlib

        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    decoded: str = raw.decode("latin-1")
    return decoded


def refresh_quarter(
    year: int,
    quarter: int,
    *,
    root: Path,
    superseded: Path,
    fetch: Callable[[int, int], str],
    stamp: str,
) -> RefreshOutcome:
    """Fetch one quarter and install it **only if it knows at least as much**.

    ``fetch`` is injected so the decision logic can be tested without touching
    the network -- which matters, because the interesting cases are a truncated
    response and an SEC error page, and neither can be produced on demand from
    the real endpoint.

    The comparison is on ``newest`` **and** ``dated_lines``, and both must hold.
    A file reaching further but holding a tenth as many rows is a partial
    response that happens to include a recent filing, and installing it would
    report the archive as current while deleting most of it.
    """
    label = f"{year} QTR{quarter}"
    target = root / str(year) / f"QTR{quarter}" / "form.idx"
    held_text = target.read_text(encoding="latin-1") if target.exists() else None
    held = inspect_index(held_text) if held_text is not None else None

    try:
        offered_text = fetch(year, quarter)
    except Exception as exc:
        # Any failure at all leaves the existing file alone. A refresh that
        # could not fetch must not be able to damage what is held.
        return RefreshOutcome(label, RefreshAction.FAILED, f"fetch failed: {exc}", held=held)

    offered = inspect_index(offered_text)
    if not offered.usable:
        return RefreshOutcome(
            label,
            RefreshAction.REFUSED,
            "the response holds no dated row; it is not an index",
            held=held,
            offered=offered,
        )
    if held_text is not None and offered_text == held_text:
        return RefreshOutcome(label, RefreshAction.UNCHANGED, "byte-identical", held=held)
    if held is not None and held.usable:
        assert held.newest is not None and offered.newest is not None
        if offered.newest < held.newest or offered.dated_lines < held.dated_lines:
            return RefreshOutcome(
                label,
                RefreshAction.REFUSED,
                f"it knows less than what is held ({offered.dated_lines} rows to "
                f"{offered.newest} against {held.dated_lines} to {held.newest}); "
                "the existing file stands",
                held=held,
                offered=offered,
            )

    withdrawn: tuple[str, ...] = ()
    if held_text is not None:
        fresh_lines = set(offered_text.splitlines())
        withdrawn = tuple(
            line for line in held_text.splitlines() if line.strip() and line not in fresh_lines
        )
        superseded.mkdir(parents=True, exist_ok=True)
        base = f"{year}-QTR{quarter}-form.idx"
        (superseded / f"{base}.superseded-{stamp}").write_text(held_text, encoding="latin-1")
        if withdrawn:
            (superseded / f"{base}.withdrawn-{stamp}.txt").write_text(
                "# Lines the superseded copy held that SEC no longer serves in this\n"
                "# file. A withdrawn index line is evidence about EDGAR; preserved\n"
                "# rather than destroyed by the refresh. Not interpreted here.\n\n"
                + "\n".join(withdrawn)
                + "\n",
                encoding="latin-1",
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(offered_text, encoding="latin-1")
    return RefreshOutcome(
        label,
        RefreshAction.INSTALLED,
        f"{offered.dated_lines} rows, reaching {offered.newest}",
        held=held,
        offered=offered,
        withdrawn=withdrawn,
    )
