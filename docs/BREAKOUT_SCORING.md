# Breakout scoring

Four numbers, and most of the design is about keeping them from collapsing into
one. Every weight below is **transparent, configurable, versioned and unfitted** —
Phase 5 is forbidden from searching historical outcomes, and Phase 9 is the
phase with a walk-forward harness to argue with these properly.

`BREAKOUT_SCORER_VERSION` is bumped whenever a component definition, curve or
weight default changes here, and every stored event records it.

## The boundary and its zone

Before anything is scored, three prices are established and frozen
([ADR-0022](adr/0022-the-boundary-is-snapshotted-at-open.md)):

- **nominal** — where the pattern says resistance is;
- **zone** — nominal ± tolerance. Inside it, price is *at* the level;
- **threshold** — the top of the zone. A qualifying close must exceed this.

The tolerance is the **larger** of a percentage floor (15 bps) and an ATR
multiple (0.10 × ATR), widened for a low-confidence boundary (up to 1.6× at
confidence zero, interpolated to 1.0× at 100) and capped at 2%. Taking the larger
rather than the sum is deliberate: the two terms are alternative expressions of
the same uncertainty, and adding them would double-count a volatile penny stock.

## BREAKOUT_QUALITY_SCORE

Computed once, on the session of the first qualifying close, and never
recomputed. See [ADR-0021](adr/0021-breakout-quality-is-frozen.md).

| Component | Weight | Required | Measures |
| --- | --- | --- | --- |
| `boundary_quality` | 0.16 | ✓ | how much the level itself deserves belief |
| `penetration` | 0.16 | ✓ | how far through, blended with extension |
| `candle_quality` | 0.22 | | the breakout bar's own anatomy |
| `volume_confirmation` | 0.24 | | relative volume and structural expansion |
| `relative_strength` | 0.10 | | price versus benchmark around the break |
| `market_context` | 0.06 | | regime at the time |
| `sector_context` | 0.06 | | sector strength at the time |

The two required components are geometric: without a boundary there is nothing
to break, and without a penetration reading nothing has been broken. Everything
else is corroboration, and its absence lowers coverage rather than quality.

### boundary_quality

`0.4 × confidence + 0.3 × touch term + 0.3 × pattern quality`. The touch term
saturates at five: beyond that, more touches stop being evidence of a real level
and start being evidence that price cannot get through it, a distinction Phase 5
has no way to adjudicate and therefore declines to score in either direction. An
unattached level (not claimed by any detector) substitutes 40 for the pattern
term and is marked, because a breakout of a level nobody's detector claimed is a
weaker object and the dataset must be able to separate the two populations.

### penetration and extension

**Two scores, one component.** Extension is not an independent dimension of
quality — it is the other end of the same measurement. A close 3 ATR through the
level scores high on penetration and low on extension *for the same underlying
fact*, and giving them separate weights would count that fact twice. The
component blends them 0.65 / 0.35 and both raw scores survive into its
measurements, so a consumer that wants to weigh them differently has the numbers.

Penetration is measured from the **threshold**, not the nominal level, so
clearing the ambiguity zone is worth zero and everything above it is genuine.
Full at 0.75 ATR beyond the zone. Extension starts counting 1.0 ATR above the
level and reaches zero at 4.0 ATR.

Item 5 asks that a massive gap far above resistance be characterised as both
higher momentum and worse entry without the engine picking. It is: the
`extreme_extension` scenario scores penetration above 80 and extension below 40,
and neither figure is hidden behind the composite.

Without ATR the penetration score falls back to a percentage beyond the zone
expressed against the zone's own width. Distances in ATR return `None` rather
than silently switching units — a fallback to percent is how an ATR-expressed
threshold ends up compared against a percentage, producing a number that looks
entirely plausible.

### candle_quality

| Term | Weight | Full at |
| --- | --- | --- |
| close location in range | 0.45 | 0.85 (zero at 0.25) |
| body as a fraction of range | 0.25 | 0.60 |
| upper wick (inverted) | 0.15 | zero at 0.50 |
| true range over ATR | 0.15 | 1.5 |

Close location carries most of the information and the reasoning is mechanical
rather than folkloric: a bar that traded up through a level and closed at its low
means every buyer above the level is losing money at the close. That is a
different market from the same penetration closing on the high, whatever the two
bars' penetration figures say.

A zero-range bar — a limit-up print, a thin name that traded once — has no shape
to read. Rather than dividing by zero, the close is placed at the top of a
degenerate range, which is what such a bar actually did.

### volume_confirmation

See [`BREAKOUT_VOLUME.md`](BREAKOUT_VOLUME.md). Three outputs kept separate
(relative, expansion, confirmation) because they can disagree: a stock whose
whole volume profile stepped up last month shows a weak relative figure and a
strong expansion one, and reporting only the composite would hide which was
which. Where no structural reference exists, the relative figure carries the
whole component rather than being averaged against a stand-in — an invented
neutral would move the composite while adding nothing.

### context components

Regime, sector and relative strength together carry 22% of the weight. They are
evidence, never a veto: see [`BREAKOUT_ARCHITECTURE.md`](BREAKOUT_ARCHITECTURE.md).
Missing inputs are marked unavailable with a mandatory reason and excluded from
both numerator and denominator; scoring an absent input as zero would report a
data gap as a market judgement.

The regime score mapping is deliberately flat (BULL_TRENDING 90 … BEAR_TRENDING
15). The gap between BULL_TRENDING and NEUTRAL is not four times the gap between
NEUTRAL and BEAR_CHOPPY, and pretending to that precision would give the
smallest-weighted component in the engine its most opinionated curve.

**RS is not RSI.** They share three letters and nothing else: RS here is price
versus a benchmark, RSI is a bounded oscillator over a single series. The four
facts item 23 asks for are kept as separate booleans rather than collapsed into a
rating, because they occur in informative combinations — a new RS high *before*
price is much stronger than one made with price, and RS deteriorating while price
breaks out is the combination most worth flagging.

## CONFIRMATION_SCORE

Recomputed each session from post-breakout evidence only.

| Component | Weight | Measures |
| --- | --- | --- |
| `close_acceptance` | 0.30 | consecutive closes above the level |
| `follow_through` | 0.30 | progress, adverse excursion, expansion, volume |
| `volume_persistence` | 0.15 | post-breakout volume vs the pre-breakout baseline |
| `retest_quality` | 0.15 | how well a pullback held, where one occurred |
| `relative_strength_after` | 0.10 | RS behaviour since the break |

Note what is **absent**: nothing about the breakout bar itself. Folding the
event's own quality back in would correlate the two scores by construction and
destroy exactly what they exist to separate.

Every component is optional. A breakout on its first day has no follow-through
evidence, has not retested, and may have no benchmark — so it scores on
acceptance alone at low coverage, which is the honest description of its
situation. Filling the gaps with neutral 50s would manufacture a middling
confirmation score for an event about which almost nothing is yet known.

`follow_through` returning *unavailable* rather than zero on an empty window is
the same point: a breakout that has not had time to follow through is not the
same as one that has had time and failed to.

**Volume persistence is measured against the pre-breakout baseline**, not
against the breakout bar's own volume. Dividing by the breakout volume is the
obvious implementation and is perverse: it rewards a low-volume breakout for
having had little volume to fall from. This was a real defect during Phase 5
development, caught because `low_volume_breakout` was scoring a *higher*
confirmation than `clean_breakout`.

## EVIDENCE_COVERAGE

The fraction of intended component weight that was actually available, computed
over both component sets. Separate from quality and never multiplied in — the
Phase 4 rule ([ADR-0017](adr/0017-quality-and-coverage-are-separate.md)) applies
unchanged.

Every unavailable component carries a mandatory reason, and
`event.coverage_gaps()` returns them, so an operator staring at 63% knows whether
the fix is a benchmark series or more history.

**A known characteristic.** An event that never retests has `retest_quality`
permanently unavailable, so a momentum breakout cannot reach 100% confirmation
coverage. This is honest — retest evidence is a kind of evidence the event has
not produced — but it means the coverage figure has a family-dependent ceiling
and should be read against other events of the same shape rather than against an
absolute.

## CONFIDENCE

How sure the engine is that it has identified the **event** correctly, which is
not how good the event looks. Item 41's distinction.

| Term | Weight |
| --- | --- |
| boundary confidence from the pattern layer | 0.20 |
| definition — touch count, and whether it is attached at all | 0.15 |
| pattern quality (or 40 for an unattached level) | 0.15 |
| quality coverage | 0.20 |
| decisiveness — how far clear of the zone the close sits | 0.15 |
| agreement — how tightly the components cluster | 0.15 |

Decisiveness matters because a close 0.02 ATR above the threshold is an
identification that would flip under a slightly different tolerance. Agreement
matters because components spread from 15 to 95 average to something respectable
while describing an event nobody would recognise.

The separation is demonstrated rather than asserted. `poor_confidence_boundary`
runs the *identical* clean price series against a single-extreme, one-touch,
unattached level: at n=200 the median quality is 82.3 against 92.8, while median
confidence falls from 83.4 to 47.4. An equally good-looking breakout, identified
far less certainly.

## Reconciliation

`ScoreResult.reconciles` checks that the components actually produce the
composite, guarding the specific failure of a late adjustment applied to the
score without being recorded as a component — an explanation that does not add
up to the number it explains. Asserted for every event in the engine tests.
