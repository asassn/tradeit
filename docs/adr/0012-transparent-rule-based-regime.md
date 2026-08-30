# ADR-0012: The first-generation regime classifier is rule-based and explainable

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 3

## Context

Market regime drives position sizing, exposure limits and breakout quality
assessment in later phases. It is one of the highest-leverage single values the
system produces, and the brief requires the first-generation classifier to be
transparent rather than a black box.

The case for that requirement is stronger than "explainability is nice":

**There is almost no training data.** Regimes change perhaps twenty or thirty
times in the whole modern record. A model with meaningful degrees of freedom
fitted to that is memorising, not learning, and no amount of cross-validation
fixes a sample size that small.

**The test set is contaminated by construction.** Any model fitted on 2000-2024
has seen every crash that will be used to evaluate it. The out-of-sample
evidence a regime model most needs is the evidence that does not exist yet.

**When it is wrong, the first question is why.** A regime label that shrinks
position sizes by half needs an answer better than a feature-importance chart.

## Decision

**A weighted sum of seven named signals**, each scoring in [-1, +1]: primary
trend, benchmark agreement, moving-average slope, breadth above the 200DMA,
new highs versus new lows, sector participation, and volatility regime. Weights
and band thresholds are configuration (ADR-0008).

**Both supporting and contradicting evidence are recorded.** A BULL reading with
three contradicting signals is a materially different statement from one with
none, and storing only what supported the conclusion hides exactly that. The
output format is the one the brief specifies.

**Per-benchmark divergences reach the evidence lists without being scored.** The
aggregate agreement signal scores the balance across SPY, QQQ and IWM, which
means a single lagging index can be outvoted and disappear. "IWM below 50DMA" is
precisely what a reader needs to see, so divergences are appended as unscored
observations — they enrich the explanation without touching the composite, and
`reconciles` still verifies the signals sum to the score.

**Confidence falls near band edges and when signals disagree.** A composite of
0.251 against a BULL threshold of 0.25 is a coin flip, and reporting it as a
confident classification would be a lie the consumer cannot detect.

**SEVERE_RISK_OFF is an override, not a band.** Extreme volatility with a
negative composite is a different operating environment from an ordinary bear —
one where sizing should shrink regardless of trend. Extreme volatility with a
*positive* composite is not risk-off; 2020 H2 was volatile and rising.

**Thresholds were not fitted to historical returns.** The brief is explicit and
it matters: tuning them in Phase 3, with no out-of-sample harness, produces a
model that looks excellent on the data it was tuned on.

**Volatility regime is a separate model.** A market can trend strongly with
elevated volatility. One row carrying both would force a single confidence
number for two independent judgements.

## Alternatives considered

**Hidden Markov model.** The textbook approach, genuinely elegant, and it
produces state assignments that are hard to explain and easy to overfit on
twenty transitions.

**Gradient-boosted classifier on labelled regimes.** Requires labels, which
requires deciding what the regimes were — the question being asked.

**A single continuous risk score, no discrete states.** Attractive, and it
merely relocates the thresholding into whatever consumes it, where it would be
less visible.

## Consequences

- Regime output is auditable: every classification carries the signals, their
  weights, their contributions, and the evidence on both sides.
- Phase 9 can backtest the thresholds against out-of-sample data. That is where
  tuning belongs.
- The classifier will misclassify transitions — rule-based models lag turning
  points. Confidence scoring makes that visible rather than hidden, and
  downstream consumers can require a confidence floor.
- A learned model may supersede this later. `model_metadata` is ready for that
  (Phase 2), and this one becomes the baseline it must beat out-of-sample.
