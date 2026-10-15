# Pricing the jump guard's threshold, 1990–2026

Measured 2026-10-15 at the owner's direction. `DISCONTINUITY_FACTOR` is a
detector threshold, so the decision is his; this exists so it is made against
a measurement rather than an anecdote. **67,377 unexplained price steps beyond
2.0×, in 6,043 securities.** Reproduce with
`scripts/research01_step_census.py` then `research01_step_verdict.py`.

## Why it was asked

§0.10 set the factor at **5.0** against the defects then visible — YELL's
7,653× and moves of 1e5 and beyond. The atlas of 2026-09-25 then found ten
securities stepping **4.0–4.4×**, under the threshold and quite large enough to
destroy a mean over 10,000 trades, and §52 criterion 4 found two more inside
the held-out window.

## What each option would have prevented

Judged against the **twelve securities that have actually damaged a result** —
ten from the atlas, two from Stage B — rather than against step counts.

| option | catches | new sessions | new securities | real moves lost |
|---|---|---|---|---|
| current **5.0** | **6/12** | — | — | — |
| **4.0** | **9/12** | 3,023 | 1,448 | ~1,875 |

*Measured after the change, 2026-10-15:* the guard went from **9,701 sessions
in 2,228 securities** to **12,753 in 2,804** — **+3,052 sessions**, within 1% of
the 3,023 predicted, and **9 of the 12 confirmed flagged** exactly as the
costing said. The "new securities" column above is **wrong as a prediction**:
576 securities were newly flagged, not 1,448, because 872 of those 1,448
already had some other session flagged at 5.0. The column counted securities
with a step in the band, which is not the same question. Left in place with
this correction rather than edited, since the costing is what the decision was
taken on.

An invariant worth keeping: every session flagged at 5.0 **must** also be
flagged at 4.0, since a lower factor is strictly more inclusive. The first
comparison showed 1,109 missing, which is impossible — two shards had not
finished. The check caught a premature conclusion that would otherwise have
read as a real inconsistency.
| 3.5 | 9/12 | 5,291 | 2,109 | ~3,408 |
| 3.0 | 9/12 | 10,601 | 2,960 | ~6,218 |
| 5.0 + a round-ratio rule ≥3.0× | 7/12 | 4,383 | 1,635 | fewer |

**4.0, 3.5 and 3.0 catch the same nine**, so 4.0 is the efficient choice:
lower buys nothing measured and costs two to three times as much. ~1,875 lost
moves is **5% of the 34,312 upward steps beyond 2.0×**, against a defect whose
damage to a mean is unbounded. The asymmetry is one-directional.

## The finding that matters more than the threshold

**Three of the twelve cannot be caught by any single-session rule.**
Securities 5533 and 3350 have **no step at or beyond 2.0×** and still produced
>400% outcomes; 12358's worst step is 2.67×. Their damage accumulates across
the holding period rather than arriving in one session.

So the jump guard is **structurally the wrong instrument for a quarter of this
problem.** The outcome-level guard-rail in `pattern_atlas_report.py` — which
refuses any cell containing a leg beyond 400% and names the security — is the
defence that actually caught all twelve, because it checks the *outcome*
rather than the mechanism. It stays whatever the factor becomes.

## Two errors in the measuring instrument, both corrected

**The round-ratio discriminator was too permissive.** It generated every a/b
for a ≤ 20 over seven denominators, so **4.25×, 4.33× and 4.67× counted as
split ratios** — a 17-for-4 split is not a thing. That inflated the share of
steps in the 4.0–5.0 band that looked like unrecorded splits from **38% to
66%**, in exactly the band being argued about. An instrument wide enough to
confirm whatever it is pointed at is not evidence. `SPLIT_RATIOS` is now
listed, not generated.

**Then the chance baseline was wrong the other way**, with a spurious `×4`
that made the clustering look like noise. Measured properly against local
background density either side of each round number, the clustering is real:

| ratio | steps within ±2% | density excess over local background |
|---|---|---|
| 2.0× | 10,983 | **13.0×** |
| 3.0× | 1,417 | 2.7× |
| 4.0× | 655 | 2.5× |
| 5.0× | 564 | 3.9× |
| 10.0× | 432 | **7.9×** |

Unrecorded splits do cluster on round ratios. They are simply not the whole
story: security 5698 steps **4.431×**, which is no split ratio at all — the
signature of a vendor restating *part* of a history, where the observed step is
the quotient of two differently-adjusted segments.

## What is not proposed

Lowering the factor **is not a fix for survivorship, for §0.9's volume
adjustment, or for the accumulating cases above**. It closes one documented
hole by three securities' worth of the twelve known. Said plainly so that
adopting it is not mistaken for the corpus becoming trustworthy.
