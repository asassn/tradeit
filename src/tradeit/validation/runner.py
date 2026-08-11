"""Running every check against one named snapshot, and saying what it proved.

The report this produces is the gate's deliverable, so its job is not to look
reassuring. Three rules shape it.

**Blocked is reported before passed.** A summary line reading "20 passed" over
a run where eighteen checks could not execute is a lie told with true numbers.
The conclusion states the blocked count first, and a run with any blocked check
cannot be described as a validation of anything.

**The provenance block is repeated in full.** Snapshot id, manifest digest,
adjustment policy, declared coverage next to observed coverage, universe
digest, code version, configuration digests. It is verbose and it is the
difference between a result somebody can check and a number in a chat message.

**The conclusion says what was not measured.** Every report ends by naming the
quantities this gate deliberately did not compute — returns, win rates,
expectancy — because a reader who sees twenty green checks will otherwise fill
that gap with an assumption, and the assumption will be the flattering one.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tradeit.errors import ConfigError
from tradeit.validation.checks import (
    CheckResult,
    CheckStatus,
    Phase,
    ValidationCheck,
    assert_no_performance_claims,
    blocked,
    errored,
)
from tradeit.validation.context import ValidationContext
from tradeit.validation.data_checks import data_checks
from tradeit.validation.phase_checks import phase_checks
from tradeit.validation.scan_checks import scan_checks

#: What this gate does not compute, quoted verbatim in every conclusion.
NOT_MEASURED: tuple[str, ...] = (
    "pattern or breakout future returns",
    "win rate or hit rate of any signal",
    "breakout expectancy",
    "trading CAGR, Sharpe or drawdown",
    "portfolio profit of any kind",
)


def all_checks() -> list[ValidationCheck]:
    """Every check, data layer first."""
    checks: list[Any] = [*data_checks(), *phase_checks(), *scan_checks()]
    seen = Counter(c.check_id for c in checks)
    duplicates = sorted(k for k, v in seen.items() if v > 1)
    if duplicates:
        raise ConfigError(
            f"duplicate check ids {duplicates}; a report keyed on a repeated id "
            "silently loses one of the results"
        )
    return checks


@dataclass(slots=True)
class ValidationRun:
    """The outcome of one harness execution over one snapshot."""

    context: ValidationContext
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    results: list[CheckResult] = field(default_factory=list)
    #: Set when the snapshot itself disqualifies the run. Checks still execute
    #: — their findings remain useful for debugging the package — but the
    #: conclusion refuses to call the result evidence.
    snapshot_warning: str = ""

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(str(r.status) for r in self.results)
        return {str(status): tally.get(str(status), 0) for status in CheckStatus}

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status.is_bad]

    @property
    def blocked_checks(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.BLOCKED]

    @property
    def conclusive(self) -> int:
        return sum(1 for r in self.results if r.status.is_conclusive)

    @property
    def is_evidence(self) -> tuple[bool, str]:
        """Whether this run may be cited as an empirical result.

        Four disqualifications, in the order a reader should hear them. Note
        that "no failures" is not on its own sufficient: a run where nothing
        could execute has no failures either.
        """
        if self.snapshot_warning:
            return False, self.snapshot_warning
        if not self.results:
            return False, "no checks ran"
        if self.blocked_checks:
            return False, (
                f"{len(self.blocked_checks)} of {len(self.results)} checks could not run "
                "for want of data; a partial run is not a validation"
            )
        if self.failures:
            n = len(self.failures)
            return False, (
                f"{n} check{'' if n == 1 else 's'} failed; the findings are real but "
                "the platform does not yet behave as documented on this data"
            )
        return True, ""

    def by_phase(self) -> dict[Phase, list[CheckResult]]:
        out: dict[Phase, list[CheckResult]] = {}
        for result in self.results:
            out.setdefault(result.phase, []).append(result)
        return out

    # -- rendering -----------------------------------------------------------

    def render(self) -> str:
        provenance = self.context.provenance()
        lines = [
            "EMPIRICAL VALIDATION RUN",
            "=" * 78,
            "",
            "Provenance",
            "-" * 78,
        ]
        for key, value in provenance.items():
            lines.append(f"  {key:<32} {value}")
        lines += ["", "Results", "-" * 78]

        for phase in Phase:
            results = self.by_phase().get(phase)
            if not results:
                continue
            lines.append(f"  {phase}")
            lines.extend(result.render() for result in results)
            lines.append("")

        counts = self.counts
        lines += [
            "Tally",
            "-" * 78,
            f"  blocked  {counts['blocked']:>4}   (could not run — never counted as a pass)",
            f"  failed   {counts['fail']:>4}",
            f"  errored  {counts['error']:>4}   (a bug in the harness itself)",
            f"  warned   {counts['warn']:>4}",
            f"  passed   {counts['pass']:>4}",
            f"  skipped  {counts['skipped']:>4}   (does not apply to this snapshot)",
            "",
        ]

        capabilities = self.context.capabilities
        lines += ["Sample size", "-" * 78]
        if capabilities.is_empty:
            lines += [
                "  This package carries no per-instrument capability record, so the two",
                "  samples below cannot be separated. Eligibility is UNKNOWN rather than",
                "  established; re-run enrichment to produce one.",
                "",
            ]
        else:
            lines += [
                f"  real-price-eligible instruments      {len(capabilities.price_eligible):>4}"
                "   (scale-invariant analytics)",
                f"  raw-reconstruction-verified          {len(capabilities.raw_verified):>4}"
                "   (absolute-price analytics)",
                "",
                "  These are two different samples and are never combined. A result over",
                "  the first says nothing about analytics that need the second, and a",
                "  single N would let them be read as the same claim.",
                "",
            ]
            reasons = capabilities.reasons()
            if reasons:
                lines.append("  Why instruments lack a verified raw price series:")
                lines += [f"    {count:>4}  {reason}" for reason, count in reasons.items()]
                lines += [
                    "",
                    "  An instrument in that list keeps its price history. It is excluded",
                    "  from absolute-price work and from nothing else.",
                    "",
                ]

        usable, reason = self.is_evidence
        lines += ["Conclusion", "-" * 78]
        if usable:
            lines.append(
                "  Every check ran and none failed. This snapshot supports the structural "
                "claims each check states, and nothing beyond them."
            )
        else:
            lines.append(f"  NOT A VALIDATION: {reason}")
        lines += [
            "",
            "  Not measured by this run, by design:",
            *[f"    - {item}" for item in NOT_MEASURED],
            "",
            "  Those wait for a later phase, run once, against data nobody has been",
            "  tuning against. A reader who fills the gap with an assumption will fill",
            "  it with the flattering one.",
        ]
        return "\n".join(lines)

    def to_payload(self) -> dict[str, object]:
        usable, reason = self.is_evidence
        return {
            "provenance": self.context.provenance(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "counts": self.counts,
            "sample_sizes": {
                "real_price_eligible": len(self.context.capabilities.price_eligible),
                "raw_reconstruction_verified": len(self.context.capabilities.raw_verified),
                "capability_recorded": not self.context.capabilities.is_empty,
            },
            "is_evidence": usable,
            "conclusion": reason or "all checks ran and passed",
            "not_measured": list(NOT_MEASURED),
            "results": [r.to_payload() for r in self.results],
        }


def run_validation(
    context: ValidationContext,
    checks: list[ValidationCheck] | None = None,
) -> ValidationRun:
    """Execute every check against one snapshot.

    Never raises for a finding. The only exception that escapes is a
    :class:`~tradeit.errors.ConfigError` from
    :func:`~tradeit.validation.checks.assert_no_performance_claims`, which is
    raised on purpose: a result reporting a forbidden quantity is a bug in the
    harness, and letting it through would be worse than crashing.
    """
    usable, warning = context.is_usable()
    run = ValidationRun(
        context=context,
        started_at=dt.datetime.now(dt.UTC),
        snapshot_warning="" if usable else warning,
    )

    for check in checks if checks is not None else all_checks():
        missing = context.missing(check.requires)
        if missing:
            run.results.append(blocked(check, missing))
            continue
        started = time.perf_counter()
        try:
            result = check.run(context)
        except Exception as error:  # a check must never abort the run
            result = errored(check, error)
        else:
            result = CheckResult(
                check_id=result.check_id,
                title=result.title,
                phase=result.phase,
                status=result.status,
                summary=result.summary,
                evidence=result.evidence,
                needs=result.needs,
                examples=result.examples,
                duration_seconds=time.perf_counter() - started,
            )
        assert_no_performance_claims(result)
        run.results.append(result)

    run.finished_at = dt.datetime.now(dt.UTC)
    return run


__all__ = ["NOT_MEASURED", "ValidationRun", "all_checks", "run_validation"]
