# Volume methodology

Volume is the largest single component of the breakout quality score at 24%, and
it is also where the most confident wrong numbers get produced. Two separate
hazards, needing two separate defences.

## Hazard one: one threshold for every setup

"Breakouts need 1.5x volume" is a statement about one family's behaviour
promoted to a law. A flat base is a quiet structure by construction; demanding
flag-like volume of it is demanding that it stop being a flat base. Item 7 of
the brief is explicit: *do NOT require one universal relative-volume threshold
for all setups.*

So the relative-volume figure that scores 100 is **per family**:

| Family | Full at | Why it differs |
| --- | --- | --- |
| `high_tight_flag` | 2.2× | explosive continuation; the signature is expansion off an already-elevated base |
| `vcp` | 2.0× | the whole thesis is dry-up then expansion, so the expansion leg is where the evidence lives |
| *(default)* | 1.8× | |
| `flat_base` | 1.5× | a quiet structure; flag-like volume would contradict the definition |
| `tight_consolidation` | 1.5× | same reasoning |
| `breakout_retest` | 1.4× | the level was already cleared once; the second clearance is not usually the volume event |

Families absent from the map use the shared default. **Absence means "no
justified difference", not "not thought about"** — the six above are the ones
where a structural argument exists.

Zero is the point at which volume becomes a real negative rather than merely
neutral: 0.7× average. Below-average volume on a breakout is evidence against.

## Three outputs, kept separate

| Output | Measures |
| --- | --- |
| `RELATIVE_VOLUME_SCORE` | breakout volume against its own recent average |
| `VOLUME_EXPANSION_SCORE` | breakout volume against the consolidation and the impulse leg |
| `VOLUME_CONFIRMATION_SCORE` | the 0.6 / 0.4 composite the quality score consumes |

They can disagree, and the disagreement is informative. A stock whose whole
volume profile stepped up last month shows a weak relative figure and a strong
expansion one; reporting only the composite would hide which was which.

The expansion terms compare against the pattern's own legs — the consolidation
it formed in, and the impulse that created it — which is the comparison that
survives an instrument whose volume regime shifted. The monitor slices those
legs out of the series using the detectors' own segment vocabulary
(`consolidation`, `handle`, `base`, `flag` / `flagpole`, `impulse`, `advance`).
An unrecognised family yields empty legs, which shows up as two absent
comparisons rather than as a comparison against the wrong bars.

**Where no structural reference exists, the relative figure carries the whole
component** rather than being averaged against a stand-in. An invented neutral
would move the composite while adding nothing.

## The averaging window excludes the bar being measured

Including a 5× volume day in the average it is being compared against dilutes
exactly the signal the comparison exists to detect, and the effect grows as the
window shrinks. The 20-session mean and 50-session median are both taken over
history strictly before the breakout bar.

The median is reported alongside the mean because a single 10× day inside the
averaging window drags the mean and not the median.

## Hazard two: comparing a partial day to a full day

The brief's own example:

> 10:15 AM volume = 800,000. Average full-day volume = 2,000,000. This does not
> mean relative volume = 0.4 in a useful sense.

It is 40% of a typical day done in the first 45 minutes, which is heavy. Getting
the number right needs a time-of-day curve. Getting it **honest** needs four
quantities kept distinct all the way to the consumer:

| Quantity | Status |
| --- | --- |
| `CURRENT_OBSERVED_VOLUME` | what has actually traded — a fact |
| `TIME_NORMALIZED_VOLUME` | observed ÷ the fraction typically done by this time — derived from facts, from a curve that could be wrong |
| `PROJECTED_VOLUME` | what the full session would print if the rest behaved typically — **a projection, never a fact** |
| `COMPLETED_BAR_RELATIVE_VOLUME` | only defined once the bar has closed |

`VolumeReading` carries all four and refuses to let a projection be read as a
completed figure: **`completed_relative_volume` is `None` on a partial bar, full
stop.** A caller wanting a number for an in-progress session has to ask for
`projected_relative_volume` by name, and the name says what it is. Consumers that
treat `None` as zero produce a wrong answer loudly rather than a plausible one
quietly, which is the intended failure mode.

Any score built on a projection records `volume_from_projection` in its
measurements and adds a contradicting-evidence line saying the figure is
projected rather than measured. The fact travels with the number.

## The curve is built from prior sessions only

`build_intraday_curve` takes complete prior sessions and returns the mean
cumulative share of session volume traded by the end of each bucket. Using the
rest of today's volume to normalise this morning's would be a leak so direct it
barely needs stating — and it is exactly what "volume so far vs. average volume
by this time" looks like when implemented carelessly against a dataframe that
contains the whole day.

The curve is U-shaped in practice: heavy at the open and into the close, quiet at
lunch. That is why a flat "fraction of the clock elapsed" normalisation
understates morning volume and overstates midday volume, and the tests assert
`expected_fraction(10:15) > elapsed_fraction(10:15)`.

Fewer than three usable sessions yields `None` rather than a curve fitted to two
days, because a two-day curve is a description of two days.

## The projection floor

`min_elapsed_fraction` defaults to 8%. Below it no projection is reported, and
the reason is stated on the reading:

> only 3.2% of the session has elapsed, below the 8% floor: dividing by a sliver
> of the session multiplies its noise rather than removing it

Dividing by 2% of a session multiplies the noise in the first two minutes by
fifty. A projection built on that is a number with no information in it, and
emitting it would be worse than emitting nothing.

## Volume persistence

The confirmation-side measure, and the one place a naive implementation inverts
the meaning. Post-breakout volume is compared against the **pre-breakout
average**, not against the breakout bar's own volume.

Dividing by the breakout volume rewards a low-volume breakout for having had
little volume to fall from. During Phase 5 development this showed up as
`low_volume_breakout` scoring a *higher* confirmation than `clean_breakout`,
which is how the defect was found. Full at 1.0× the pre-breakout average, zero at
0.4×.
