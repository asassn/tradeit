# Pattern Labeling

The human-label vocabulary, the review infrastructure, and the protocol for
building a real-market labelled corpus once market-data egress is available.

**Nothing has been labelled.** Phase 4 builds the infrastructure and stops. No
model is trained on this and none should be until humans have labelled real
examples. The reason to build it now is that **the sampling discipline has to
exist before the corpus does** — otherwise the corpus gets built out of whatever
was easy to find, and no amount of later care recovers from that.

---

## 1. The five labels

| Label | Meaning | What happens next |
| --- | --- | --- |
| `POSITIVE` | this is the pattern | enters the corpus |
| `NEGATIVE` | this is not the pattern | enters the corpus |
| `AMBIGUOUS` | judged, and the honest judgement is "arguably" | enters the corpus |
| `ABSTAIN` | the *reviewer* declined to judge | **reassign** to another reviewer |
| `INSUFFICIENT_EVIDENCE` | the *example* cannot be judged | **retire** from the queue |

### Why ABSTAIN and INSUFFICIENT_EVIDENCE are both kept

They look redundant until you ask what happens next.

An **ABSTAIN** is about the reviewer — outside their competence, or they
recognise the instrument and do not trust themselves to be neutral. The example
is fine; someone else should see it.

An **INSUFFICIENT_EVIDENCE** is about the example — not enough history before the
structure, a data gap through the middle, a security too illiquid for the
geometry to mean anything. No reviewer can judge it.

Merging them would mean either re-queuing dead examples forever or silently
discarding examples a second reviewer could have handled. Different downstream
action, so different label.

`AMBIGUOUS` is a real category rather than a fudge. Chart structure is genuinely
continuous, and forcing a binary manufactures agreement that does not exist.
Only the three judgement labels enter agreement statistics — an abstention is not
a disagreement.

---

## 2. Append-only by revision

A reviewer changing their mind is itself a fact about how hard the example is.

The unique key is `(instrument, session, timeframe, family, reviewer, revision)`.
A re-review is a new row. Inter-reviewer agreement computed after overwrites is
computed over a population that erased its own disagreements, which is exactly
the population you must not compute it from.

`latest_per_reviewer` gives the read most consumers want, built on top of the
full history rather than replacing it.

---

## 3. What is pinned at labelling time

The detector's prediction, its version, the configuration digest, the quality,
the state, and the **evidence coverage**.

Without them, human-versus-detector agreement is computed against whatever the
detector does when the query runs, which is a different detector. Coverage
matters too: agreement with a detector scoring on 40% of its intended evidence is
a different measurement from agreement with one that had everything.

A quality rating attached to `NEGATIVE` is refused rather than quietly stored —
*"this is not a cup, quality 70"* has no reading.

---

## 4. The review queue

A **query service**, not a stored queue. A queue table would need reconciling
with the patterns it points at every time a scan runs, and the only state it
would hold that labels do not already hold is "assigned but not yet reviewed" —
a UI concern, addable when there is a UI to need it.

| Strategy | Selects | Why it exists |
| --- | --- | --- |
| `RANDOM` | uniformly at random | **the only strategy that yields an unbiased estimate of anything** |
| `SCORE_BAND` | stratified across quality bands | the middle of the distribution, not only the extremes |
| `BORDERLINE` | near the decision boundaries | where a label is worth most |
| `DISAGREEMENT` | reviewers already split | the hard cases |
| `COMPETING` | several families on one geometry | the confusion cases |
| `POSITIVE_CONTROL` | highest-scoring | precision measurement |
| `NEGATIVE_CONTROL` | lowest-scoring / rejected | recall measurement |
| `LOW_COVERAGE` | score rests on little evidence | where coverage and quality diverge |
| `TERMINAL` | structures that failed | **under-sampled by every other strategy** |

`RANDOM` is seeded and reproducible. A labelling run that cannot be reproduced
cannot be audited for selection bias, which is the one thing it most needs to be
auditable for.

`TERMINAL` deserves its own note: failed structures are what a false-positive rate
is computed from, and every other strategy under-samples them because they stop
appearing in live scans.

---

## 5. The real-market labelling protocol

To be executed when market-data egress is available. The Phase 3 data gate must
close first — this protocol assumes point-in-time bars with a working
`knowledge_time`.

### 5.1 What the corpus must contain

Not "a thousand examples". A corpus stratified so that each of these is
represented at a stated minimum:

| Stratum | Minimum | Why |
| --- | ---: | --- |
| Textbook successes | 100 | the easy case; establishes a ceiling |
| **Messy successes** | 200 | what the detector will actually meet |
| **Failed patterns** | 300 | the population a false-positive rate needs |
| Ambiguous structures | 150 | where inter-reviewer agreement is measurable |
| Bull-market periods | 250 | |
| Bear-market periods | 250 | regime is the largest confound |
| Sideways periods | 250 | |
| Earnings-gap structures | 100 | the repricing-versus-accumulation case |
| High-volatility periods | 150 | |
| Low-volatility periods | 150 | |
| **Delisted / failed securities** | 100 | survivorship bias enters here or nowhere |
| ≥5 sectors | 100 each | |
| ≥3 market-cap bands | 150 each | |
| ≥3 liquidity bands | 150 each | thin names are where geometry lies |

Counts are minimums per **family**, not in total, for the families that will be
evaluated. A corpus that meets them for the bull flag and not for the cup can
report on the bull flag only.

### 5.2 Sampling methodology

Four sources, mixed in stated proportions so that no single one dominates:

1. **Uniform random (instrument, date) pairs — 40%.** Drawn from the point-in-time
   universe **as it stood on that date**, including securities that later
   delisted. This is the only stratum that supports an unbiased base-rate
   estimate, and it is the largest for that reason.
2. **Detector candidates, stratified by score band — 30%.** Equal counts per band
   including the lowest, so precision is measurable across the range rather than
   at the top.
3. **Negative controls — 20%.** Instrument-dates where the detector found nothing,
   drawn uniformly. Without these, recall is unmeasurable.
4. **Human-submitted candidates — 10%.** Capped deliberately. Analyst-chosen
   examples are the most biased source available and the most tempting to
   over-weight.

Every draw is seeded and the seed recorded with the corpus.

### 5.3 The rule that matters most

> **Future returns must never determine whether an example enters the structural
> labelling dataset.**

Not as a tiebreak. Not as a filter on "interesting" examples. Not by drawing from
a watchlist of names that worked.

The dataset evaluates **chart structure as of the labelling date**. An example
selected because the stock later went up teaches the detector that patterns which
work look like patterns which worked, which is circular and unfalsifiable — and
the resulting precision figure would be an artefact of the sampling, not a
property of the detector.

Permitted selection inputs: random time/instrument draws, detector candidacy,
score-band strata, known historical regimes, human-submitted charts, negative
controls.

Forbidden: subsequent return, subsequent drawdown, whether the breakout
"worked", whether the name is a famous winner, anything else computed from bars
after the labelling date.

Future returns **may** be attached afterwards, as a separate outcome table keyed
by (instrument, date), for outcome research. That is a different study with a
different design and it must not feed back into corpus membership.

### 5.4 Presentation to reviewers

- Charts rendered **to the labelling date only**. No bars after it, no exceptions.
- Instrument identity **masked** where practical. A reviewer who recognises the
  ticker is labelling their memory of the outcome.
- Detector prediction **hidden during first review**, revealed afterwards.
  Anchoring on a shown score is well documented and would make agreement
  statistics meaningless.
- Each example seen by **at least three reviewers**, drawn from a pool of at
  least five, with assignment randomised.

### 5.5 What gets measured

Once labelled, and not before:

- Per-family precision at score bands.
- Per-family recall against the negative-control stratum.
- Inter-reviewer agreement (Krippendorff's alpha over the three judgement labels).
- Human-versus-detector agreement, conditioned on evidence coverage.
- **Cross-family calibration** — the mapping that lets a cup's 80 and a pennant's
  80 be compared. This does not exist and cannot be established without this
  corpus.
- Whether any weight should change. This is the *only* legitimate basis for
  re-weighting a component, and it is why the V-bottom's composite has not been
  adjusted: there is currently no evidence about what it should be, only a
  preference.

### 5.6 What must not be measured here

Win rate, average forward return, breakout success rate, expectancy, CAGR,
Sharpe. Those belong downstream. Computing them from this corpus would let the
detector be validated by the thing it is supposed to be an input to.

---

## 6. Current status

- Vocabulary, service, revisions, agreement, and the nine queue strategies: **built
  and tested**.
- Schema: `pattern_labels` with `label`, `revision`, `detector_coverage`,
  `config_digest` (migration `0005_phase4_gate`).
- Labels recorded: **none**.
- Real-market corpus: **not started** — blocked on market-data egress, which is
  the Phase 3 gate item that remains open.
