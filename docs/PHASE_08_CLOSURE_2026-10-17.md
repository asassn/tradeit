# Phase 8 closed — what the audit found, and what it cost

Audited and closed 2026-10-17, the same day as Phase 9 and under the same
delivery plan. **The pattern repeated exactly: Phase 8's portfolio and risk
machinery was built, tested and almost entirely unexercised, and three of the
things that had never run had never run for a reason nobody had written
down.**

## The audit

| module | tests | run on real data before | state |
|---|---|---|---|
| `portfolio/base` | 29 | yes, every backtest | complete |
| `portfolio/sizing` | 24 | yes, every backtest | complete |
| `portfolio/stops` | 22 | yes, every backtest | complete |
| `portfolio/cycle` | 11 | yes, every backtest | complete |
| `portfolio/allocation` | 18 | **weight 0.0 in every run** | **now exercised** |
| `risk/engine` | 14 | yes, every backtest | complete |
| `risk/rules` | 31 | yes, every backtest | complete |
| `risk/contextual` — correlation | 20 | **never** | **now exercised** |
| `risk/contextual` — sector | 20 | **never** | **now exercised** |
| `risk/snapshot` | 13 | **never** | **now exercised** |
| `portfolio/mandate` | 21 | **never** | **now exercised** |

## Why the four had never run, which is the finding

Not neglect. Each was blocked by something real, and in two cases *by the
module working correctly*.

**`CorrelationRule` fails closed on an unknown pair** — an unmeasured
correlation blocks rather than permits. Installing it without a correlation
source would therefore refuse every new position and halt trading on session
one. No source existed, so the rule was left out of every backtest, and
`DiversityAwareRanker` was passed `correlation_weight=0.0` alongside it. That
second line is the one that matters: **it disabled a named Phase 8 requirement
across every real-data backtest this project has run, including this week's
walk-forward, and it carried no comment at all** while the line above it
carried a careful note about §38's cost guard. A reader would reasonably have
concluded diversification was tested and found not to matter.

**`SectorExposureRule` pools unclassified holdings into one `UNCLASSIFIED`
bucket**, and `sectors` held **zero rows** against 17,433 securities with
prices. Every name would have landed in that one bucket, and a 30% sector cap
would have held the whole portfolio to 30% of equity. A missing-data gap, not a
code gap — and inventing classifications to close it would have been fabricated
evidence.

**`build_snapshot` takes both of those as inputs.** It could not run until they
existed.

**`check_population` needed a real population**, and the project had never
handed it one.

## What closing it cost, and what it did not

The sector gap was closed **free**, which is the finding worth keeping: SIC
codes sit in EDGAR filing headers already in the corpus, and
`derive_sectors_from_sic.py` turned them into a bitemporal interval table —
**16,768 rows over 13,738 securities, 76.4% of those with prices, every row
citing its accession**. No vendor, no purchase, no fabrication. The division
level of the SIC hierarchy was chosen over the two-digit major group because a
30% cap over eight divisions constrains and a 30% cap over eighty groups never
binds.

The 23.6% without a SIC observation stay pooled in `UNCLASSIFIED` and the cap
limits them collectively. That is conservative and it is a real limitation on
what any sector-limited result means.

## What running them found

**The correlation limit's configured 0.75 does not bind on fifteen large caps.**
0 of 66 measured pairs exceeded it, so the first run of
`backtest_correlation_limit.py` changed nothing — and reported so, rather than
reporting that the rule had been exercised. A binding override was needed to
show it refuses anything. **"It ran and nothing happened" is not an exercise**,
and the script now says so in those words when the counts match.

**A sector cap binds by resizing at least as often as by refusing.** The first
version of `backtest_sector_limit.py` compared trade counts only and printed
"the rule changed NOTHING" for a run in which the return moved 5.19% → 5.09%.
The cap had bound on every one of those sessions; it had simply taken a smaller
position rather than declining to open one. **A refusal-only test is blind to
the commoner case** — recorded because the detection flaw, not the rule, was
the thing that nearly went into this document as a result.

**`BacktestResult` carries no record of declined proposals.** A draft counted
`getattr(result, "planned", ())`, a field that does not exist, and so reported
`0` refusals as though it had measured them. Caught; the effect is read from
the trade count, which is a real field. Logged with the §41 family: the answer
arrived and it was not an answer to the question asked.

## The measurements

**Mandate purity, on the pattern atlas** — 50,000 trades from the extended
Stage A atlas, every one a Daily bar in the Swing mandate: **admissible**. An
expected answer, so the same check was run against a population with **one**
Day/5m observation spliced into a thousand Swing/1d ones and refused it,
naming the contaminant. A purity check that cannot fail on the data it is given
proves nothing.

**The daily risk record, on 2,516 real sessions** — 2010-2019, fourteen names,
the SIC sector map and a 66-pair correlation matrix:

| | |
|---|---|
| snapshots built | 2,516, one per session |
| of which held **nothing** | 892 (35.5%) |
| `correlation_cluster` breached | 89 sessions (3.5%) |
| `sector_exposure` breached | 14 sessions (0.6%) |
| largest single-sector share | 30.6%, Manufacturing |
| heat | median 0.55%, max 3.85% against a 6% limit |
| gross exposure | median 6.3%, max 32.4% against 100% |
| drawdown from peak | median 0.9%, max 3.0% against a 15% halt |

**The 892 empty sessions are the point of the module.** A risk record written
only when something trades cannot answer *"was that drawdown a control failure
or an ordinary run of losses?"*, and 35.5% of this run would have been missing
from it. The 103 breach-sessions are also the point: **both were breached with
no proposal involved** — prices moved and the book drifted outside a limit
nobody had traded into.

**One branch is still unexercised and this is not a quibble.** The honest-`None`
path for an unmeasured pair did not fire: 1,599 sessions held fewer than two
positions so no pair existed, and of the 917 where one did, **zero** hit an
unmeasured pair — the 66-pair matrix happens to cover every pair this book ever
held, though it covers only 66 of the universe's 91. The 13 unit tests remain
the only evidence for that branch. Recorded rather than rounded up, because the
first draft of the reporting lumped both reasons for a `None` together and
would have claimed the branch as exercised.

## Point-in-time honesty, stated rather than defaulted

Three simplifications were made explicitly, because each could have been
defaulted silently:

1. **The sector map is built as of the first session and held fixed.** 881
   securities changed SIC code at some point in the corpus, so a name
   reclassified mid-run keeps the sector it had when the run started. A live
   system re-reads as the clock advances.
2. **The correlation matrix is computed once over the first 126 sessions and
   held fixed**, so it uses information from before every decision but is not
   refreshed as the window rolls.
3. **Neither is a look-ahead violation as used here** — both are built from the
   window's opening, not its end — but both would be if the matrix were
   recomputed over the full span, which is how it would most naturally have
   been written.

## Status

**Phase 8 is closed.** Every module has now run against the corpus except the
one branch named above, and the two rules that had never been installed are
installed, binding, and measured. Like Phase 9, it consumed a day rather than
the weeks the plan allowed, for the same reason: the work was already there and
the audit was the work.

Track A's next gate is **Phase 7 — opportunity scoring**, target 2026-11-07.
