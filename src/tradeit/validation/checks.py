"""The vocabulary of an empirical check, and the one thing it may not measure.

A check answers a question about data or about the code's behaviour on data. It
returns a :class:`CheckResult` and never raises for a finding — a failing check
is a result, not an exception, because a runner that stopped at the first
failure would report one problem per run and hide the other nineteen.

**Five outcomes, and the two that are usually collapsed.** ``FAIL`` means the
check ran and the answer was wrong. ``BLOCKED`` means it could not run because
the data it needs is not present. Reporting BLOCKED as PASS is the failure mode
this whole gate exists to prevent: a check that silently skipped looks exactly
like a check that passed, and a summary saying "20/20 passed" over eighteen
skips is worse than no summary. They are separate statuses, counted separately,
and the runner's conclusion states the blocked count first.

``WARN`` is for a finding that is real but not disqualifying — a data quirk
worth knowing about. ``SKIPPED`` is for a check that does not apply to this
snapshot at all, which is different from one that would apply if the data were
there.

**What no check here may compute.** This gate is explicitly not a backtest.
:data:`FORBIDDEN_MEASURES` names the quantities that must wait for a later
phase — future returns, win rate, expectancy, CAGR, Sharpe, profit — and
:func:`assert_no_performance_claims` walks a result's identifiers, its evidence
keys and every line of prose it will print, looking for them. The guard is
crude on purpose: it cannot stop someone determined, but it will stop the
gradual drift where "just a quick hit rate" becomes the number everybody
quotes.

Crude is not the same as careless. Two of those words are ordinary technical
English — a *graph* has edges, and the universe contains a ticker spelled
``EDGE`` — so the guard resolves the sense before it accuses, and refuses only
the reading that would be a performance claim. It has cried wolf twice now:
once on ``knowledge_time_ordering``, once on a lifecycle transition tally, and
each time the fix was to make the guard sharper rather than quieter.
"""

from __future__ import annotations

import datetime as dt
import re
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tradeit.data.packages.spec import DatasetKind
from tradeit.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - breaks the checks <-> context cycle
    from tradeit.validation.context import ValidationContext

#: Quantities this gate may not compute, in any check, under any name.
#:
#: Not a style preference. Every one of these requires deciding what happens
#: *after* a pattern or breakout, and the moment a number like that exists
#: somebody will tune a threshold against it — which is precisely the
#: selection-bias failure the phase order was designed to prevent. They belong
#: to Phase 9 and later, run once, against data nobody has been tuning on.
FORBIDDEN_MEASURES: tuple[str, ...] = (
    "future_return",
    "forward_return",
    "win_rate",
    "hit_rate",
    "expectancy",
    "cagr",
    "sharpe",
    "sortino",
    "profit",
    "pnl",
    "equity_curve",
    "drawdown",
    "alpha",
    "edge",
)


class CheckStatus(StrEnum):
    """What happened when a check was asked to run."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    #: Does not apply to this snapshot — e.g. an intraday check on a daily-only
    #: package. Different from BLOCKED: no data would make this run.
    SKIPPED = "skipped"
    #: Could not run: the data it needs is absent. **Never counted as a pass.**
    BLOCKED = "blocked"
    #: The check itself raised. A bug in the harness, reported as loudly as a
    #: bug in the platform, because a harness that swallows its own errors
    #: reports a clean run over broken machinery.
    ERROR = "error"

    @property
    def is_conclusive(self) -> bool:
        """Whether this outcome is evidence about the platform."""
        return self in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN)

    @property
    def is_bad(self) -> bool:
        return self in (CheckStatus.FAIL, CheckStatus.ERROR)


class Phase(StrEnum):
    """Which layer a check interrogates. Used to group the report."""

    DATA = "data"
    PHASE_3 = "phase3"
    PHASE_4 = "phase4"
    PHASE_5 = "phase5"
    PHASE_6_CONTRACT = "phase6_contract"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One question, one answer, and the numbers behind it."""

    check_id: str
    title: str
    phase: Phase
    status: CheckStatus
    summary: str
    #: The measurements. Kept as a mapping rather than prose so a later run can
    #: be diffed against this one rather than re-read.
    evidence: Mapping[str, object] = field(default_factory=dict)
    #: For BLOCKED: what would have to be present.
    needs: tuple[str, ...] = ()
    #: Sample rows, file/line references, offending identities — bounded.
    examples: tuple[str, ...] = ()
    #: Lines printed in full, never sampled. For the case where the finding
    #: *is* the enumeration: a survivorship roster abbreviated to five rows
    #: hides exactly the names the reader opened the report to look for.
    #: Use ``examples`` for "here are five of 4,000"; use this only when the
    #: complete list is small and each row is load-bearing.
    detail: tuple[str, ...] = ()
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.summary:
            raise ConfigError(f"{self.check_id} returned no summary; a result nobody can read")
        if self.status is CheckStatus.BLOCKED and not self.needs:
            raise ConfigError(
                f"{self.check_id} is BLOCKED but does not say what it needs. A blocked "
                "check whose requirement is unstated cannot be unblocked."
            )

    def render(self) -> str:
        mark = {
            CheckStatus.PASS: "PASS ",
            CheckStatus.FAIL: "FAIL ",
            CheckStatus.WARN: "WARN ",
            CheckStatus.SKIPPED: "SKIP ",
            CheckStatus.BLOCKED: "BLOCK",
            CheckStatus.ERROR: "ERROR",
        }[self.status]
        lines = [f"  [{mark}] {self.check_id}: {self.summary}"]
        for key, value in sorted(self.evidence.items()):
            lines.append(f"           {key}: {_summarise(value)}")
        for example in self.examples[:5]:
            lines.append(f"           e.g. {example}")
        lines.extend(f"         {line}" for line in self.detail)
        if self.needs:
            lines.append(f"           needs: {', '.join(self.needs)}")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "phase": str(self.phase),
            "status": str(self.status),
            "summary": self.summary,
            "evidence": dict(self.evidence),
            "needs": list(self.needs),
            "examples": list(self.examples),
            "detail": list(self.detail),
            "duration_seconds": round(self.duration_seconds, 4),
        }


#: Longest evidence value printed literally in a terminal report.
_EVIDENCE_INLINE_LIMIT = 100


def _summarise(value: object) -> str:
    """One evidence value, at a length a person will actually read.

    A check may carry structured evidence — a per-instrument roster, a census —
    so that a later run can be diffed against this one rather than re-read.
    Printing a hundred-entry list inline buries the scalars either side of it,
    which is how a reader ends up skipping the whole block. The full value is
    always in ``to_payload``; only the terminal line is abbreviated, and the
    line says so rather than trailing off.
    """
    if isinstance(value, list | tuple):
        rendered = str(list(value))
        if len(rendered) > _EVIDENCE_INLINE_LIMIT:
            return f"{len(value)} entries (see the report payload)"
        return rendered
    if isinstance(value, Mapping):
        rendered = str(dict(value))
        if len(rendered) > _EVIDENCE_INLINE_LIMIT:
            return f"{len(value)} keys (see the report payload)"
        return rendered
    return str(value)


@runtime_checkable
class ValidationCheck(Protocol):
    """One check. Implementations are cheap to construct and hold no state."""

    check_id: str
    title: str
    phase: Phase
    #: Datasets that must be in the snapshot for this check to mean anything.
    requires: tuple[DatasetKind, ...]

    def run(self, context: ValidationContext) -> CheckResult:
        """Answer the question. Must not raise for a finding.

        Typed against the concrete context rather than ``object``: a protocol
        whose parameter is ``object`` is not actually satisfied by an
        implementation that needs a ``ValidationContext``, and mypy will say so
        the first time somebody annotates a list of checks.
        """


def blocked(
    check: ValidationCheck, missing: Sequence[DatasetKind], detail: str = ""
) -> CheckResult:
    """The result for a check whose inputs are not present."""
    names = tuple(str(m) for m in missing)
    return CheckResult(
        check_id=check.check_id,
        title=check.title,
        phase=check.phase,
        status=CheckStatus.BLOCKED,
        summary=(
            f"not run: the snapshot has no {', '.join(names)} data"
            + (f". {detail}" if detail else "")
        ),
        needs=names,
    )


def errored(check: ValidationCheck, error: BaseException) -> CheckResult:
    """The result for a check that raised. Carries the traceback's last frame."""
    frames = traceback.format_exception(type(error), error, error.__traceback__)
    return CheckResult(
        check_id=check.check_id,
        title=check.title,
        phase=check.phase,
        status=CheckStatus.ERROR,
        summary=f"the check itself raised {type(error).__name__}: {error}",
        examples=(frames[-2].strip() if len(frames) > 1 else str(error),),
    )


#: Word-boundary patterns for :data:`FORBIDDEN_MEASURES`.
#:
#: Boundaries rather than substrings, because the first version of this guard
#: matched ``edge`` inside ``knowledge_time_ordering`` and refused the
#: point-in-time check — the single most important check in the harness. A
#: guard that cries wolf gets deleted, so it has to be right. The trailing
#: ``\w*`` still catches ``profits`` and ``win_rates``.
#:
#: A leading ``_`` is a word character, so ``transition_edge`` and
#: ``state_transition_edge`` do not match ``\bedge`` — a compound field name is
#: the sanctioned way to say "edge" about a graph. That is not an accident of
#: the regex; it is the rule, and :class:`TestTheTransitionEdgeCollision` in
#: ``tests/unit/test_gate_validation.py`` pins it.
_FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    term: re.compile(rf"\b{re.escape(term)}\w*", re.IGNORECASE) for term in FORBIDDEN_MEASURES
}

#: Forbidden measures whose ordinary spelling is upper case, and which are
#: therefore never exempted as a ticker: ``CAGR`` and ``PNL`` in capitals are
#: exactly the claim.
_ACRONYM_MEASURES = frozenset({"cagr", "pnl"})

#: Longest a US equity symbol runs. The ticker exemption below is capped at
#: this, which confines it to ``edge`` and ``alpha`` — the two forbidden terms
#: that are also real symbols. ``SHARPE`` and ``DRAWDOWN`` in block capitals are
#: a column heading in somebody's report, not a listing, and stay refused.
_MAX_SYMBOL_LENGTH = 5

#: Senses in which an ambiguous term is *structure*, not performance.
#:
#: ``edge`` is the whole reason this mapping exists. A breakout lifecycle is a
#: directed graph, its transitions are edges, and ``phase5.lifecycle`` said so
#: in prose — which aborted a validation run over a check that computes nothing
#: but a transition tally. The answer is neither to drop ``edge`` from the
#: forbidden list (a trading edge is precisely what must not be computed here)
#: nor to ban the word from graph vocabulary, but to require that the graph
#: sense be *stated*. "edges of the lifecycle" is structure. A bare "edge" is
#: not, and still fails.
_STRUCTURAL_SENSES: dict[str, tuple[re.Pattern[str], ...]] = {
    "edge": (
        re.compile(
            r"\b(?:transition|lifecycle|state[ _-]machine|graph|directed)[ _-]edges?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bedges?\s+(?:of|in)\s+the\s+(?:transition\s+)?"
            r"(?:lifecycle|graph|state[ _-]machine)\b",
            re.IGNORECASE,
        ),
    ),
}


def _symbol_pattern(term: str) -> re.Pattern[str] | None:
    """The ticker-shaped spelling of ``term``, or ``None`` if it has none.

    An English word written in block capitals inside a report is a **ticker**:
    the universe contains ``EDGE`` and ``ALPHA``, ``phase4.concentration``
    interpolates symbol names into its examples, and a gate that aborted
    because one of them was in the universe would be the same cry-wolf failure
    as matching ``edge`` inside ``knowledge_time_ordering`` — on real data.
    """
    if term in _ACRONYM_MEASURES or "_" in term or len(term) > _MAX_SYMBOL_LENGTH:
        return None
    return re.compile(rf"(?<![A-Za-z]){re.escape(term.upper())}(?![a-z])")


_SYMBOL_PATTERNS: dict[str, re.Pattern[str] | None] = {
    term: _symbol_pattern(term) for term in FORBIDDEN_MEASURES
}


def _mentions(
    term: str,
    texts: Sequence[str],
    *,
    allow_symbol: bool,
    allow_structural: bool,
) -> bool:
    """Whether ``term`` appears in ``texts`` in its *performance* sense.

    Occurrences that are provably something else are removed before the term is
    searched for, so an allowance only ever covers the occurrence it explains.
    A text containing both "edges of the lifecycle" and "our edge" still fails
    on the second.
    """
    for text in texts:
        residue = text
        if allow_structural:
            for sense in _STRUCTURAL_SENSES.get(term, ()):
                residue = sense.sub(" ", residue)
        if allow_symbol and (symbol := _SYMBOL_PATTERNS[term]) is not None:
            residue = symbol.sub(" ", residue)
        if _FORBIDDEN_PATTERNS[term].search(residue):
            return True
    return False


def _key_paths(evidence: object) -> list[str]:
    """Every mapping key anywhere inside ``evidence``.

    Nested, because ``phase4.score_distribution`` reports a dict per detector
    and a forbidden name one level down is no less a forbidden name. Values are
    deliberately *not* walked: they are tickers, states and reasons — data, not
    assertions — and scanning them would make the guard fire on the universe's
    contents rather than on the report's claims.
    """
    if isinstance(evidence, Mapping):
        out: list[str] = []
        for key, value in evidence.items():
            out.append(str(key))
            out.extend(_key_paths(value))
        return out
    if isinstance(evidence, Sequence) and not isinstance(evidence, str | bytes):
        return [path for item in evidence for path in _key_paths(item)]
    return []


def assert_no_performance_claims(result: CheckResult) -> None:
    """Refuse a result that reports a quantity this gate may not compute.

    Raises rather than warning. A performance number that reached a report is
    already a number somebody can quote, and the point of the guard is that it
    never gets there.

    Three surfaces, with different rules, because they carry different things:

    ``check_id`` and ``title``
        Authored identifiers. Strict — no ticker lives here. The graph sense of
        an ambiguous word is allowed, since a title may legitimately describe a
        state machine.
    evidence keys, nested
        Field names. A ticker *can* be a key, so the symbol exemption applies;
        the structural sense does not, because a field has room to be named
        exactly (``transition_edge``, ``state_transition``) and prose does not.
    ``summary``, ``examples``, ``detail``
        Prose that reaches the report. All three are scanned: a summary reading
        "12% of breakouts were profitable" is exactly as damaging as an
        evidence key called ``win_rate``, and so is an *example* saying it.
    """
    hits = sorted(
        {
            term
            for term in FORBIDDEN_MEASURES
            if _mentions(
                term,
                (result.check_id, result.title),
                allow_symbol=False,
                allow_structural=True,
            )
            or _mentions(
                term,
                _key_paths(result.evidence),
                allow_symbol=True,
                allow_structural=False,
            )
            or _mentions(
                term,
                (result.summary, *result.examples, *result.detail),
                allow_symbol=True,
                allow_structural=True,
            )
        }
    )
    if hits:
        raise ConfigError(
            f"{result.check_id} reports {hits}, which this gate must not compute. "
            "Future returns, win rates, expectancy and every performance "
            "statistic wait for a later phase, run once, against data nobody "
            "has been tuning on. If the term is innocent here, rename the field "
            "— a graph edge is a `transition_edge`, and prose may say `edges of "
            "the lifecycle` where a bare `edge` is refused."
        )


@dataclass(frozen=True, slots=True)
class CheckClock:
    """The as-of instant every check reads through.

    A check that read 'now' would produce a different answer tomorrow over the
    same snapshot, which makes a validation result impossible to compare with
    itself.
    """

    as_of: dt.datetime

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ConfigError("validation as_of must be timezone-aware")


__all__ = [
    "FORBIDDEN_MEASURES",
    "CheckClock",
    "CheckResult",
    "CheckStatus",
    "Phase",
    "ValidationCheck",
    "assert_no_performance_claims",
    "blocked",
    "errored",
]
