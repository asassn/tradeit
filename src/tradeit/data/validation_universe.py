"""The Phase 3 validation universe.

A deliberately hostile roster: 85 instruments picked because each one breaks
something. Splits and reverse splits, bankruptcies, ticker reuse, direct
listings, ADRs on foreign holiday calendars, a five-figure share price, a
sub-dollar one, and the 2021 squeeze names whose real bars look exactly like
corrupt data.

The point is not to be a portfolio. It is to make every diagnostic in the
pipeline fire on something real, so that a diagnostic which *never* fires can
be recognised as broken rather than assumed to be vigilant.

The roster also carries its own control group -- long, boring, high-quality
histories where nothing unusual happened. A quality check that flags JNJ is a
quality check with a false-positive problem, and without controls in the same
run there is no way to tell.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from tradeit.errors import ConfigError

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "validation_universe.toml"


class UniverseCategory(StrEnum):
    BENCHMARK = "benchmark"
    SECTOR_PROXY = "sector_proxy"
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    DIVIDEND = "dividend"
    DELISTED = "delisted"
    SYMBOL_CHANGE = "symbol_change"
    EXTREME_MOVE = "extreme_move"
    IPO = "ipo"
    LIQUIDITY = "liquidity"
    ADR = "adr"
    REIT = "reit"
    DUAL_CLASS = "dual_class"
    #: Fiscal year that does not end in December. Added for the empirical gate:
    #: the whole universe until then stressed *price* handling, and nothing in
    #: it would catch a fundamental pipeline that assumes Q1 ends 31 March.
    FISCAL_CALENDAR = "fiscal_calendar"
    #: A company that restated previously reported figures. The case that
    #: separates a real point-in-time store from one that merely keeps history:
    #: as of a date before the restatement, the *original* number is what a
    #: strategy could act on, and the corrected one must be invisible.
    RESTATEMENT = "restatement"
    CONTROL = "control"

    @property
    def is_stress_case(self) -> bool:
        """Whether an instrument in this category is expected to trip something."""
        return self not in (
            UniverseCategory.BENCHMARK,
            UniverseCategory.SECTOR_PROXY,
            UniverseCategory.CONTROL,
        )


@dataclass(frozen=True, slots=True)
class ValidationInstrument:
    ticker: str
    category: UniverseCategory
    #: What this instrument tests. ``"none"`` for the control group.
    difficulty: str
    note: str = ""
    first_trade_date: dt.date | None = None
    last_trade_date: dt.date | None = None
    #: Other symbols the *same security* has traded under — typically the
    #: bankruptcy rename, where NYSE-listed ``LEH`` becomes over-the-counter
    #: ``LEHMQ`` on the day of the Chapter 11 filing.
    #:
    #: These are historical ticker facts, **not** verified vendor symbols. No
    #: vendor is claimed to serve any of them. They exist so that a provider
    #: answering "unknown symbol" for ``LEH`` is reported as *identity
    #: resolution not attempted* rather than as *the vendor has no delisted
    #: coverage* — two findings with different owners and different fixes.
    #: Whether a given vendor actually resolves one is settled by asking it.
    alias_candidates: tuple[str, ...] = ()

    @property
    def is_delisted(self) -> bool:
        return self.last_trade_date is not None

    @property
    def requires_delisted_coverage(self) -> bool:
        """Whether a survivorship-unsafe provider will simply not have this.

        Used to compute how much of the universe is unreachable on a given
        vendor, which is a more useful number than a yes/no on survivorship.
        """
        return self.is_delisted


@dataclass(frozen=True, slots=True)
class ValidationUniverse:
    name: str
    description: str
    instruments: tuple[ValidationInstrument, ...]

    def __len__(self) -> int:
        return len(self.instruments)

    @property
    def tickers(self) -> list[str]:
        return [i.ticker for i in self.instruments]

    def of_category(self, category: UniverseCategory) -> list[ValidationInstrument]:
        return [i for i in self.instruments if i.category is category]

    @property
    def stress_cases(self) -> list[ValidationInstrument]:
        return [i for i in self.instruments if i.category.is_stress_case]

    @property
    def controls(self) -> list[ValidationInstrument]:
        """The names where a firing diagnostic is evidence of a false positive."""
        return self.of_category(UniverseCategory.CONTROL)

    @property
    def delisted(self) -> list[ValidationInstrument]:
        return [i for i in self.instruments if i.is_delisted]

    def get(self, ticker: str) -> ValidationInstrument:
        for instrument in self.instruments:
            if instrument.ticker == ticker:
                return instrument
        raise ConfigError(f"{ticker!r} is not in the {self.name} universe")

    def symbol_map(self, *, start_id: int = 1) -> dict[int, str]:
        """Stable ``instrument_id`` → ticker map for the whole roster."""
        return {start_id + i: t for i, t in enumerate(sorted(self.tickers))}

    def composition(self) -> dict[str, int]:
        return dict(sorted(Counter(str(i.category) for i in self.instruments).items()))

    def coverage_gap(self, *, supplies_delisted: bool) -> list[str]:
        """Which tickers a provider cannot supply, and therefore which tests die.

        Returns names rather than a count because the identity of what is
        missing matters: losing LEH and BSC removes the entire survivorship
        test, while losing one thin-liquidity name removes very little.
        """
        if supplies_delisted:
            return []
        return sorted(i.ticker for i in self.instruments if i.requires_delisted_coverage)

    def difficulty_index(self) -> dict[str, list[str]]:
        """Which instruments exercise which failure mode."""
        out: dict[str, list[str]] = {}
        for instrument in self.instruments:
            if instrument.difficulty == "none":
                continue
            out.setdefault(instrument.difficulty, []).append(instrument.ticker)
        return {k: sorted(v) for k, v in sorted(out.items())}


def load_universe(path: Path | str | None = None) -> ValidationUniverse:
    raw = tomllib.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    entries: Iterable[dict[str, object]] = raw.get("instruments", [])

    instruments: list[ValidationInstrument] = []
    seen: set[str] = set()
    for entry in entries:
        ticker = str(entry["ticker"])
        if ticker in seen:
            raise ConfigError(f"{ticker!r} appears twice in the validation universe")
        seen.add(ticker)
        try:
            category = UniverseCategory(str(entry["category"]))
        except ValueError as exc:
            raise ConfigError(f"{ticker}: unknown category {entry['category']!r}") from exc
        instruments.append(
            ValidationInstrument(
                ticker=ticker,
                category=category,
                difficulty=str(entry.get("difficulty", "none")),
                note=str(entry.get("note", "")),
                first_trade_date=_as_date(entry.get("first_trade_date")),
                last_trade_date=_as_date(entry.get("last_trade_date")),
                alias_candidates=_as_tickers(entry.get("alias_candidates")),
            )
        )

    if not instruments:
        raise ConfigError("the validation universe is empty")

    return ValidationUniverse(
        name=str(raw.get("name", "validation")),
        description=str(raw.get("description", "")),
        instruments=tuple(instruments),
    )


@lru_cache(maxsize=4)
def default_universe() -> ValidationUniverse:
    return load_universe()


def _as_tickers(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    raise ConfigError(f"alias_candidates must be a list of strings, got {value!r}")


def _as_date(value: object) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
