# Identity churn on real data

`phase4.identity_stability` reported that 148,922 of 156,433 pattern identities
from the `diag-01` diagnostic scan (95.2%) carry a re-mint suffix, at a mean of
2.28 observations per identity. This document answers the question that number
raises and cannot itself settle: **is one structure being torn into many
identities, or are many structures genuinely beginning and ending?**

The two call for opposite responses. If identities genuinely end, re-minting is
correct and merging them would be the bug. If one continuous structure is being
fragmented, then every per-identity rate in the platform is computed over an
inflated denominator.

## What was measured

Reproduced on a three-instrument, eight-year synthetic corpus (39,218
identities, 95.6% re-minted — the same regime as the real scan), because the
per-cause tallies needed instrumenting and the user's database is not reachable
from here. `phase4.identity_churn` now emits this table on every gate run, so
the real numbers arrive with the next scan.

| detector | ids | forked | fork% | obs med | p90 | p99 | life med | single% |
|---|---|---|---|---|---|---|---|---|
| ascending_triangle | 3,138 | 2,988 | 95.2% | 2.0 | 3 | 15 | 6 | 22.3% |
| base_on_base | 242 | 231 | 95.5% | 2.0 | 2 | 48 | 6 | 0.0% |
| breakout_retest | 3,835 | 3,635 | 94.8% | 1.0 | 2 | 4 | 0 | 62.5% |
| bull_flag | 7,119 | 6,726 | 94.5% | 2.0 | 3 | 15 | 6 | 26.8% |
| cup_handle | 4,107 | 4,002 | 97.4% | 2.0 | 2 | 8 | 6 | 14.7% |
| double_bottom | 5,910 | 5,825 | 98.6% | 2.0 | 2 | 6 | 6 | 39.6% |
| flat_base | 3,516 | 3,282 | 93.3% | 2.0 | 3 | 18 | 6 | 8.2% |
| inverse_head_shoulders | 1,147 | 1,129 | 98.4% | 2.0 | 2 | 4 | 6 | 10.5% |
| pennant | 1,789 | 1,647 | 92.1% | 2.0 | 3 | 6 | 6 | 20.3% |
| tight_consolidation | 1,142 | 983 | 86.1% | 2.0 | 3 | 14 | 6 | 19.4% |
| vcp | 7,273 | 7,035 | 96.7% | 2.0 | 3 | 10 | 6 | 15.8% |

`life med` is calendar days between first and last observation; `single%` is the
share of identities observed on exactly one session.

Two further numbers decide the question:

- **1,280 base hashes name more than one identity, the most crowded naming 209.**
  A base hash is a content hash of instrument, timeframe, pattern type and
  structural *start*. 209 identities sharing one means the same structure,
  beginning on the same date, was re-minted 209 times over eight years.
- **Re-mints by cause: 36,933 after termination, 550 on an illegal transition.**

## The determination: fragmentation, from a lookup defect

The first reading of these numbers was that terminations were too eager —
patterns ageing out and being re-detected as new structures — and the proposed
remedy was to stop terminating a structure that is still being detected. Two
further measurements ruled that out, and it is worth recording why, because the
wrong fix would have merged genuinely separate lives to work around a defect
that would still have been there.

**72.6% of re-mints overlap their predecessor.** Measuring the gap between a
predecessor's last observation and its successor's first detection:

| gap | share of re-mints |
|---|---|
| ≤ 0 days (overlapping) | 72.6% |
| ≤ 1 day | 93.2% |
| ≤ 3 days | 98.6% |
| ≤ 5 days | 99.8% |

A negative gap means the new identity was minted **while the previous life was
still open**. No termination rule can produce that. And the 209-identity base
shows first-detection dates on consecutive trading days:

```
a0adf03763  2016-08-25..2016-08-31  near_breakout -> broken_out_unconfirmed
2016-08-31  2016-08-31..2016-09-06  near_breakout -> expired (lost)
2016-09-01  2016-09-01..2016-09-23  near_breakout -> broken_out -> expired
2016-09-02  2016-09-02..2016-09-23  near_breakout -> broken_out -> expired
2016-09-06  2016-09-06..2016-09-23  ...
```

The cause is that **the tracker could not find its own live identity.** A
detector emits the same content hash every session. After one re-mint the
tracker holds that structure under a suffixed key (`<hash>:<session>`). The
lookup went straight into the open set with the detector's key, missed, fell
through to the re-mint branch — and minted another identity. Every session, for
as long as the structure kept being detected. `_retired.discard(key)` was also
called with the *suffixed* key, so the base stayed retired permanently and the
branch could never stop firing.

A second, smaller mechanism: **10,024 identities were born already terminal** —
their only observation is `detected -> invalidated`. A detector that keeps
reporting a dead structure minted one such identity per session, each of which
recorded nothing the identity that actually lived it did not already carry.

## The fix

Two rules, no threshold moved. Both are stated in full in
[PATTERN_IDENTITY.md](PATTERN_IDENTITY.md).

1. **`_live`**, a one-entry-per-structure index from the detector's key to the
   tracked identity currently alive for it. Corrects the lookup.
2. **A life cannot begin already over.** A detection arriving in a terminal
   state, for a structure whose previous life has already terminated, does not
   mint a new identity.

`grace_sessions`, `resolution_carry_sessions`, the lifecycle's legal edges and
every detector parameter are untouched.

## Before and after

Same corpus, same detectors, same configuration; only the tracker changed.

| | before | after | change |
|---|---|---|---|
| total identities | 39,218 | 5,548 | **−85.9%** |
| re-minted identities | 37,483 (95.6%) | 3,813 (68.7%) | −89.8% |
|  … after termination | 36,933 | 1,609 | −95.6% |
|  … on an illegal transition | 550 | 2,204 | +301% |
| single-observation identities | 10,095 (25.7%) | 275 (5.0%) | −97.3% |
|  … born already terminal | 10,024 | 273 | −97.3% |
| observations/identity med / p90 / p99 | 2 / 3 / 13 | 5 / 20 / 59 | — |
| lifespan days med / p90 / p99 | 6 / 18 / 24 | 8 / 32 / 91 | — |
| identities per base med / p90 / **max** | 11 / 64 / **209** | 2 / 7 / **21** | **−89.9%** |
| breakout events | 34,061 | 6,187 | −81.8% |

The one number that rises is the honest one. Illegal-transition forks went from
550 to 2,204 because most detections previously never reached the legality
check at all — the lookup missed first and the re-mint branch swallowed them.
That class was always this large; it was hidden underneath the larger defect.
Total forks still fall from 37,483 to 3,813.

## What remains, and what it is

The residual churn is now dominated by the legality fork: a detection proposing
an edge the lifecycle does not have — overwhelmingly `BROKEN_OUT_UNCONFIRMED`
re-detected as `NEAR_BREAKOUT` or `MATURE`, which is a *failed breakout of the
same structure*. Whether that should be an edge of the state machine rather than
a new identity is a separate and much smaller question, and it is a lifecycle
decision rather than a defect. It is deliberately left open.

Nothing here was chosen against a market outcome. No forward return, win rate,
profitability or trading result was computed, consulted, or available.
