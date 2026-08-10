"""What an offline empirical data package contains, and what each column means.

The problem this solves: real market data cannot reach this execution
environment (egress policy denies the provider hosts), so the empirical
validation the Phase 3, 4 and 5 gates all defer must be driven by files the
project owner supplies. Files supplied by a person are files with idiosyncratic
column names, mixed date formats, and a vendor's opinions baked into them.

So the format is defined once, here, in terms of **what each field means rather
than what any vendor calls it**. A package declares a column mapping in its
manifest; the importer never guesses. Guessing is how a "close" column that was
actually adjusted close ends up producing patterns that never existed.

Three rules the whole specification turns on:

**No dataset is mandatory except the manifest.** A package with only daily bars
is a valid package; it simply enables fewer validations. :data:`DATASET_SPECS`
records which Phase 3/4/5 checks each dataset unlocks, and the import report
says what the supplied package can and cannot support. A specification that
demanded everything would be a specification nobody could satisfy.

**Adjusted prices are declared, never inferred.** ``adjustment_policy`` on the
manifest is required and has no default. A vendor export of adjusted closes
loaded as raw prices produces a price history that never happened — today's
adjusted series for a stock that split last week differs from the series anyone
could have seen before the split (ADR-0005). The importer refuses a package that
does not say which it contains.

**Timestamps mean what the manifest says they mean.** A ``filing_timestamp``
column that actually holds a period end is the single most damaging thing a
fundamental package can contain, so the specification separates
``period_end`` from ``filing_timestamp`` and the importer refuses a fundamental
dataset that supplies only the former (see
:data:`~tradeit.data.packages.spec.FUNDAMENTAL_DATASETS`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

#: Bumped when the package format changes in a way that would make an older
#: importer misread a newer package. Stored in every manifest.
PACKAGE_FORMAT_VERSION = 1


class DatasetKind(StrEnum):
    """The datasets a package may contain. None is mandatory."""

    INSTRUMENTS = "instruments"
    SYMBOL_MAPPINGS = "symbol_mappings"
    EXCHANGES = "exchanges"
    DAILY_BARS = "daily_bars"
    INTRADAY_BARS = "intraday_bars"
    SPLITS = "splits"
    DIVIDENDS = "dividends"
    CORPORATE_ACTIONS = "corporate_actions"
    DELISTINGS = "delistings"
    SECTORS = "sectors"
    UNIVERSE_MEMBERSHIP = "universe_membership"
    EARNINGS = "earnings"
    FUNDAMENTALS = "fundamentals"
    FILINGS = "filings"


#: Datasets whose point-in-time correctness depends on a separate filing
#: timestamp. Listed rather than inferred so the rule is reviewable in one
#: place: a quarter ending 31 March does not become available on 31 March.
FUNDAMENTAL_DATASETS: frozenset[DatasetKind] = frozenset(
    {DatasetKind.FUNDAMENTALS, DatasetKind.EARNINGS}
)


class AdjustmentPolicyDeclaration(StrEnum):
    """What the price columns in a package actually contain.

    Required on the manifest with no default. The three are not
    interchangeable, and a package that guessed would be a package whose
    patterns are drawn on a series nobody could have seen.
    """

    #: Exchange prints. What this platform stores (ADR-0005).
    RAW_UNADJUSTED = "raw_unadjusted"
    #: Adjusted for splits only.
    SPLIT_ADJUSTED = "split_adjusted"
    #: Adjusted for splits and dividends.
    TOTAL_RETURN_ADJUSTED = "total_return_adjusted"
    #: The exporter does not know. Accepted, quarantined loudly, and it blocks
    #: every corporate-action validation.
    UNKNOWN = "unknown"

    @property
    def is_usable_for_patterns(self) -> bool:
        """Whether pattern and breakout validation may run on these prices.

        Split-adjusted history is usable for *structure* — the shapes are the
        same — but not for the corporate-action checks, which exist precisely to
        find the artefacts adjustment removes. Unknown blocks everything,
        because a series that will not say what it is cannot be reasoned about.
        """
        return self is not AdjustmentPolicyDeclaration.UNKNOWN


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One field the importer needs, described by meaning rather than name."""

    name: str
    kind: str  # "date" | "datetime" | "decimal" | "int" | "string" | "bool"
    required: bool
    description: str
    #: Column names commonly used by vendors. **Hints for the person writing the
    #: manifest, never applied automatically.** Auto-matching is how a column
    #: called "close" that holds adjusted closes gets silently accepted.
    common_aliases: tuple[str, ...] = ()

    def __str__(self) -> str:
        flag = "required" if self.required else "optional"
        return f"{self.name} ({self.kind}, {flag}): {self.description}"


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """One dataset's contract, and what supplying it unlocks."""

    kind: DatasetKind
    summary: str
    columns: tuple[ColumnSpec, ...]
    #: Named validations this dataset makes possible. Reported by the importer,
    #: so a package's owner learns what a missing file costs rather than
    #: discovering it when a validation silently skips.
    enables: tuple[str, ...] = ()
    #: Datasets that must also be present for this one to be interpretable.
    requires: tuple[DatasetKind, ...] = ()
    #: Why this dataset matters, in a sentence a non-specialist can act on.
    why: str = ""

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.required)

    @property
    def optional_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if not c.required)

    def column(self, name: str) -> ColumnSpec | None:
        for item in self.columns:
            if item.name == name:
                return item
        return None


def _c(
    name: str,
    kind: str,
    required: bool,
    description: str,
    *aliases: str,
) -> ColumnSpec:
    return ColumnSpec(name, kind, required, description, tuple(aliases))


DATASET_SPECS: Mapping[DatasetKind, DatasetSpec] = {
    DatasetKind.INSTRUMENTS: DatasetSpec(
        kind=DatasetKind.INSTRUMENTS,
        summary="One row per security, including securities that no longer trade.",
        why=(
            "Without the delisted and acquired names, every historical result is "
            "computed over survivors only — which is the single most flattering "
            "mistake a backtest can make."
        ),
        columns=(
            _c("instrument_id", "int", True, "stable surrogate key; never a ticker"),
            _c("name", "string", True, "company or fund name", "security_name", "longname"),
            _c("primary_exchange", "string", True, "MIC or exchange code", "exchange", "mic"),
            _c("asset_class", "string", True, "common_stock / etf / adr / reit / ..."),
            _c("country", "string", False, "ISO-3166 alpha-2; defaults to US"),
            _c("currency", "string", False, "ISO-4217; defaults to USD"),
            _c("first_trade_date", "date", False, "first session the security traded"),
            _c("listing_status", "string", False, "active / delisted / acquired / ..."),
            _c("delisted_date", "date", False, "last session; required when not active"),
            _c("figi", "string", False, "OpenFIGI identifier, if the vendor supplies one"),
            _c("cik", "string", False, "SEC CIK, needed to join filings"),
        ),
        enables=("instrument reference resolution", "survivorship-safe universe reads"),
    ),
    DatasetKind.SYMBOL_MAPPINGS: DatasetSpec(
        kind=DatasetKind.SYMBOL_MAPPINGS,
        summary="Ticker-to-instrument bindings over half-open date intervals.",
        why=(
            "Tickers are recycled. A screen keyed on the string will splice two "
            "unrelated price histories together and never say so."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "the instrument this ticker referred to"),
            _c("ticker", "string", True, "the symbol as printed", "symbol"),
            _c("valid_from", "date", True, "first session inclusive", "start_date"),
            _c("valid_to", "date", False, "first session it no longer applied, exclusive"),
        ),
        enables=("symbol-change handling", "ticker-reuse detection"),
    ),
    DatasetKind.EXCHANGES: DatasetSpec(
        kind=DatasetKind.EXCHANGES,
        summary="Exchange codes, names and timezones.",
        why="Session boundaries and half-days depend on which exchange a security trades on.",
        columns=(
            _c("code", "string", True, "MIC or vendor exchange code"),
            _c("name", "string", False, "human-readable name"),
            _c("timezone", "string", False, "IANA timezone, e.g. America/New_York"),
        ),
        enables=("calendar selection",),
    ),
    DatasetKind.DAILY_BARS: DatasetSpec(
        kind=DatasetKind.DAILY_BARS,
        summary="Daily OHLCV. The one dataset almost every validation needs.",
        why=(
            "Everything downstream is built on these. The adjustment policy on "
            "the manifest is not optional metadata: adjusted closes loaded as "
            "raw prices describe a history nobody could have seen."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key, not a ticker"),
            _c("session_date", "date", True, "exchange-local trading date", "date", "day"),
            _c("open", "decimal", True, "session open", "o", "open_price"),
            _c("high", "decimal", True, "session high", "h"),
            _c("low", "decimal", True, "session low", "l"),
            _c("close", "decimal", True, "session close", "c", "close_price"),
            _c("volume", "decimal", True, "shares traded", "v", "vol"),
            _c("vwap", "decimal", False, "volume-weighted average price, if supplied"),
            _c("trade_count", "int", False, "number of trades, if supplied"),
            _c(
                "knowledge_time",
                "datetime",
                False,
                "when the bar became available; defaults to close + vendor lag",
            ),
        ),
        enables=(
            "OHLCV ingestion checks",
            "trading-calendar checks",
            "missing-bar handling",
            "indicator calculations",
            "multi-timeframe construction",
            "volatility regime",
            "pattern detection",
            "breakout detection",
            "point-in-time replay",
        ),
    ),
    DatasetKind.INTRADAY_BARS: DatasetSpec(
        kind=DatasetKind.INTRADAY_BARS,
        summary="Intraday OHLCV at a stated bar width.",
        why=(
            "The only dataset that can validate the intraday volume projection, "
            "which is the place a partial session is most easily mistaken for a "
            "complete one."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("timestamp", "datetime", True, "bar start, timezone-aware", "datetime", "ts"),
            _c("timeframe", "string", True, "1m / 5m / 15m / 30m / 1h / 4h"),
            _c("open", "decimal", True, "bar open"),
            _c("high", "decimal", True, "bar high"),
            _c("low", "decimal", True, "bar low"),
            _c("close", "decimal", True, "bar close"),
            _c("volume", "decimal", True, "shares traded in the bar"),
        ),
        enables=(
            "intraday volume-curve construction",
            "intraday relative-volume validation",
            "intraday timeframe aggregation",
        ),
    ),
    DatasetKind.SPLITS: DatasetSpec(
        kind=DatasetKind.SPLITS,
        summary="Share splits and reverse splits with their ex-dates.",
        why=(
            "A 4-for-1 split looks exactly like a 75% crash to any indicator that "
            "has not been told about it."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("ex_date", "date", True, "first session trading at the new count"),
            _c("ratio", "decimal", True, "share-count multiplier: 2-for-1 is 2, 1-for-10 is 0.1"),
            # Kept alongside the derived ratio rather than replaced by it. A
            # ratio of 0.1 could be 1-for-10 or 2-for-20; the pair says which,
            # and a vendor's own numbers are what an argument about direction
            # gets settled against.
            _c("numerator", "int", False, "new shares, as the vendor stated it: 4 in a 4-for-1"),
            _c("denominator", "int", False, "old shares: 1 in a 4-for-1, 10 in a 1-for-10"),
            _c("split_type", "string", False, "the vendor's own label, e.g. stock_split"),
            _c(
                "source_provider",
                "string",
                False,
                "which vendor supplied this record, when it is not the vendor that "
                "supplied the prices",
            ),
            _c(
                "announcement_time",
                "datetime",
                False,
                "when the split was announced; normally before the ex-date",
            ),
        ),
        enables=(
            "corporate-action artefact detection",
            "split-adjusted price reconstruction",
            "ATR and momentum artefact checks",
        ),
    ),
    DatasetKind.DIVIDENDS: DatasetSpec(
        kind=DatasetKind.DIVIDENDS,
        summary="Cash distributions with their ex-dates.",
        why="A large special dividend produces a gap that is not a market move.",
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("ex_date", "date", True, "first session trading without the dividend"),
            _c("cash_amount", "decimal", True, "per pre-action share, in the listing currency"),
            _c("announcement_time", "datetime", False, "declaration timestamp"),
        ),
        enables=("dividend gap attribution", "total-return reconstruction"),
    ),
    DatasetKind.CORPORATE_ACTIONS: DatasetSpec(
        kind=DatasetKind.CORPORATE_ACTIONS,
        summary="Spin-offs, rights issues, symbol changes and anything else.",
        why="The events that are neither a split nor a dividend still move the print.",
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("action_type", "string", True, "spinoff / rights_issue / symbol_change / ..."),
            _c("ex_date", "date", True, "first session reflecting the action"),
            _c("ratio", "decimal", False, "share-count multiplier where applicable"),
            _c("cash_amount", "decimal", False, "per-share cash where applicable"),
            _c("new_ticker", "string", False, "required for a symbol change"),
            _c("announcement_time", "datetime", False, "declaration timestamp"),
        ),
        enables=("full corporate-action replay",),
    ),
    DatasetKind.DELISTINGS: DatasetSpec(
        kind=DatasetKind.DELISTINGS,
        summary="When and why a security stopped trading.",
        why=(
            "A series that simply stops is indistinguishable from a data gap. "
            "Without this, the two are conflated and every quality check on "
            "missing bars becomes noise."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("delisted_date", "date", True, "last session the security traded"),
            _c("reason", "string", False, "delisted / acquired / merged / bankrupt / suspended"),
        ),
        enables=("delisting-gap discrimination", "survivorship-safe universe reads"),
    ),
    DatasetKind.SECTORS: DatasetSpec(
        kind=DatasetKind.SECTORS,
        summary="Sector and industry classification over date intervals.",
        why=(
            "Classifications change. Using today's mapping for all history "
            "manufactures a sector membership nobody could have known."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("scheme", "string", True, "GICS / ICB / vendor scheme name"),
            _c("sector", "string", True, "sector label"),
            _c("industry", "string", False, "industry label"),
            _c("valid_from", "date", True, "first session this classification applied"),
            _c("valid_to", "date", False, "first session it no longer applied, exclusive"),
        ),
        enables=("sector strength", "sector participation", "sector rotation context"),
    ),
    DatasetKind.UNIVERSE_MEMBERSHIP: DatasetSpec(
        kind=DatasetKind.UNIVERSE_MEMBERSHIP,
        summary="Which instruments were in a named universe over which intervals.",
        why=(
            "The survivorship control. A screen run as of 2015 must see the "
            "companies that were listed in 2015, including the ones that later "
            "went to zero."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("universe", "string", True, "universe name, e.g. sp500"),
            _c("instrument_id", "int", True, "surrogate key"),
            _c("valid_from", "date", True, "first session of membership"),
            _c("valid_to", "date", False, "first session after membership, exclusive"),
            _c("exit_reason", "string", False, "why it left"),
        ),
        enables=(
            "cross-sectional ranking",
            "market breadth",
            "market regime breadth terms",
            "survivorship-safe screening",
        ),
    ),
    DatasetKind.EARNINGS: DatasetSpec(
        kind=DatasetKind.EARNINGS,
        summary="Earnings dates, with the timestamp at which each became known.",
        why=(
            "A scheduled date announced in March cannot be visible to a screen "
            "run in January. The announcement timestamp is the gate, not the "
            "scheduled date."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("scheduled_date", "date", True, "the session the report falls on"),
            _c("fiscal_period", "string", True, "Q1 / Q2 / Q3 / Q4 / FY"),
            _c("fiscal_year", "int", True, "fiscal year"),
            _c(
                "announced_time",
                "datetime",
                False,
                "when this date became public; NOT the scheduled date. Absent, "
                "the importer assumes the event was knowable only on the day it "
                "occurred, which is deliberately conservative: a proximity "
                "filter then sees fewer upcoming events than a live system "
                "would, never more.",
                "announcement_time",
                "announced_at",
            ),
            _c("period_end", "date", False, "last day of the fiscal period, if supplied"),
            _c("session_hint", "string", False, "bmo / amc / during"),
            _c("is_confirmed", "bool", False, "company-confirmed rather than vendor estimate"),
            _c("eps_actual", "decimal", False, "reported EPS, once reported"),
            _c("eps_estimate", "decimal", False, "consensus estimate at announcement"),
        ),
        enables=("earnings-gap context", "earnings proximity flags"),
    ),
    DatasetKind.FUNDAMENTALS: DatasetSpec(
        kind=DatasetKind.FUNDAMENTALS,
        summary="Reported financial facts, one metric per row, as filed.",
        why=(
            "The dataset where a single shortcut invalidates everything built on "
            "it: a quarter ending 31 March does not become available on 31 March."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("metric", "string", True, "line-item name, e.g. revenue, net_income"),
            _c("fiscal_period", "string", True, "Q1 / Q2 / Q3 / Q4 / FY / TTM"),
            _c("fiscal_year", "int", True, "fiscal year"),
            _c("period_end", "date", True, "last day of the fiscal period"),
            _c("value", "decimal", False, "the reported value; may be null"),
            _c("unit", "string", False, "USD / shares / ratio; defaults to USD"),
            _c(
                "filing_timestamp",
                "datetime",
                False,
                "when this fact became public. NOT period_end, and never equal "
                "to it. Optional in the column contract but not optional in "
                "effect: with no filing timestamp anywhere in the package, the "
                "importer's default is to quarantine the row rather than guess "
                "when it was knowable. An operator may instead accept a "
                "filing-deadline estimate, which marks every affected row "
                "ESTIMATED and counts them in the report — see "
                "KnowledgeTimePolicy.require_reported_fundamentals.",
                "filed_at",
                "filing_date",
                "accepted_at",
            ),
            _c("is_restatement", "bool", False, "true when this supersedes an earlier filing"),
            _c("restates_period_end", "date", False, "the period this restates, if any"),
            _c("accession", "string", False, "filing identifier, e.g. SEC accession number"),
        ),
        enables=(
            "point-in-time fundamental reads",
            "Phase 6 fundamental scoring",
            "restatement handling",
        ),
    ),
    DatasetKind.FILINGS: DatasetSpec(
        kind=DatasetKind.FILINGS,
        summary="Filing metadata, when statements and timestamps come from separate sources.",
        why=(
            "If the fundamentals export has no filing timestamp, this dataset is "
            "how one is attached. Joined on accession or on "
            "(instrument, period_end, form_type)."
        ),
        requires=(DatasetKind.INSTRUMENTS,),
        columns=(
            _c("instrument_id", "int", True, "surrogate key"),
            _c("accession", "string", False, "filing identifier; the preferred join key"),
            _c("form_type", "string", True, "10-K / 10-Q / 8-K / 20-F / ..."),
            _c("period_end", "date", False, "fiscal period the filing covers"),
            _c("filed_at", "datetime", True, "public availability timestamp"),
            _c("accepted_at", "datetime", False, "acceptance timestamp where distinct"),
        ),
        enables=("filing-timestamp linkage", "point-in-time fundamental reads"),
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationCapability:
    """What a given set of datasets makes possible, and what it does not."""

    available: tuple[str, ...]
    blocked: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, object]:
        return {
            "available": list(self.available),
            "blocked": [{"check": name, "needs": need} for name, need in self.blocked],
        }

    def render(self) -> str:
        """Both halves, for the import report.

        The blocked half is printed even when it is long, because a check that
        silently skipped is indistinguishable from a check that passed, and the
        whole purpose of this object is to make that distinction visible at
        import time rather than at conclusion time.
        """
        lines = [f"  available ({len(self.available)}):"]
        lines += [f"    + {name}" for name in self.available] or ["    (none)"]
        lines.append(f"  blocked ({len(self.blocked)}):")
        lines += [f"    - {name} — needs {need}" for name, need in self.blocked] or ["    (none)"]
        return "\n".join(lines)


def capabilities_for(present: Sequence[DatasetKind]) -> ValidationCapability:
    """What the supplied datasets unlock, and what each missing one costs.

    The point of returning both halves is that a package owner should learn what
    a missing file costs at import time, not by noticing that a validation
    reported nothing. A check that silently skips looks identical to a check
    that passed.
    """
    have = set(present)
    available: list[str] = []
    blocked: list[tuple[str, str]] = []
    for kind, spec in DATASET_SPECS.items():
        if kind in have and all(dep in have for dep in spec.requires):
            available.extend(spec.enables)
            continue
        if kind in have:
            missing = ", ".join(sorted(str(d) for d in spec.requires if d not in have))
            blocked.extend((check, f"{kind} needs {missing}") for check in spec.enables)
        else:
            blocked.extend((check, f"the {kind} dataset") for check in spec.enables)
    return ValidationCapability(
        available=tuple(dict.fromkeys(available)),
        blocked=tuple(dict.fromkeys(blocked)),
    )


def describe_dataset(kind: DatasetKind) -> str:
    """A human-readable column contract, for the DATA_REQUIRED document."""
    spec = DATASET_SPECS[kind]
    lines = [f"### {kind}", "", spec.summary, ""]
    if spec.why:
        lines += [f"*Why it matters.* {spec.why}", ""]
    if spec.requires:
        lines += [f"Requires: {', '.join(str(d) for d in spec.requires)}", ""]
    lines += [
        "| Column | Type | Required | Meaning | Common vendor names |",
        "| --- | --- | --- | --- | --- |",
    ]
    for column in spec.columns:
        aliases = ", ".join(f"`{a}`" for a in column.common_aliases) or "—"
        lines.append(
            f"| `{column.name}` | {column.kind} | "
            f"{'yes' if column.required else 'no'} | {column.description} | {aliases} |"
        )
    if spec.enables:
        lines += ["", "Enables: " + ", ".join(spec.enables)]
    return "\n".join(lines)


__all__ = [
    "DATASET_SPECS",
    "FUNDAMENTAL_DATASETS",
    "PACKAGE_FORMAT_VERSION",
    "AdjustmentPolicyDeclaration",
    "ColumnSpec",
    "DatasetKind",
    "DatasetSpec",
    "ValidationCapability",
    "capabilities_for",
    "describe_dataset",
]
