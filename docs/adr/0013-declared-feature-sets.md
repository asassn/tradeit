# ADR-0013: Registry membership does not authorise consumption

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 3 validation gate

## Context

The feature registry (Phase 3) declares every feature the platform can compute:
today 151 across indicators, relative strength, sectors, breadth and the two
regime models. It records how each is defined, when it warms up, what its nulls
mean, and hashes the whole set to a digest stored on every value.

That is a catalogue. It answers *does this feature exist and what is it*. It
does not answer *should this consumer read it* — and the two are one keystroke
apart, because `registry.active()` is right there and returns a list of exactly
the shape a model wants.

Taking it would be a mistake with three distinct costs:

**Reproducibility breaks silently.** A scorer consuming the active registry
gains an input the moment anyone registers an indicator. No file the scorer owns
changed, no review happened, no version was bumped — and every result it
produced before that moment was produced by a thing that no longer exists.

**Features get consumed that were never meant for consumption.** The registry
holds diagnostics, intermediate quantities and raw inputs alongside decision
features. `relative_volume` is a monitoring aid; `atr_percent` is an input to
sizing, not a ranking signal. A consumer that takes everything takes those too.

**Correlated variants silently reweight the model.** Of the 151 features, 60
are relative strength: five measures × three benchmarks × four lookbacks, plus
percentiles. Another 26 are moving averages, distances from them, momentum and
rate-of-change. A model handed all of them has not been given eighty-six pieces
of evidence about trend; it has been given a handful, many times over, and has
quietly made trend dominate everything else.

None of this is hypothetical carelessness. It is the default behaviour of the
most obvious code anyone would write.

## Decision

**A consumer declares the features it reads. Nothing else may reach it.**

`tradeit.analytics.feature_sets.FeatureSet` is that declaration: a name, a
`ConsumerKind`, a version, and an explicit list of feature names. It resolves
against a registry into a `ResolvedFeatureSet` — a sub-registry containing
exactly those features plus their transitive dependencies.

Four properties make the rule enforceable rather than aspirational:

**Declarations are explicit name lists, never patterns.** `rs_*` would
reintroduce the whole problem: the set would change meaning when a new `rs_`
feature is registered, and the change would appear in no diff of the
declaration.

**Two digests, because two different things can change.**
`declaration_digest` covers the choice of features. `ResolvedFeatureSet.digest`
covers the choice *and* the definitions. Adding an unrelated feature to the
registry moves neither; changing how a declared feature is calculated moves the
second only. Confusing a recomputation with a modelling change, or the reverse,
is how a backtest and a live run come to disagree with no visible cause.

**Access is checked, not trusted.** `ResolvedFeatureSet.view()` returns a
`FeatureView` that raises `FeatureAccessError` on an undeclared name. It
distinguishes an undeclared *input* (always an error) from a declared feature
with a *null value* (ordinary warm-up), because returning `None` for both would
make the bug indistinguishable from the normal case — and a model trained on
that difference is training on a bug.

**Dependencies resolve transitively.** A feature that declares `depends_on`
pulls its upstream into the resolved set whether the consumer asked or not —
declaring a percentile without the score it ranks would give a set whose stated
warm-up understates what it genuinely needs, and eligibility built on that
number lets a strategy scan before its inputs are real. No Phase 3 feature
declares a dependency yet; the mechanism exists because Phase 4 onwards will
compose features, and retrofitting it after the first composed feature ships is
how the warm-up understatement gets introduced.

**Warm-up follows the declaration, not the catalogue.** `ResolvedFeatureSet`
constructs the `EligibilityPolicy` for its consumer. A screen reading four
20-day features is ready in twenty sessions; using `registry.max_warmup` would
make it wait 272 for an ADX it never reads.

`FeatureSetCatalogue` holds every declaration so that *which consumers read
this feature* is answerable **before** a definition is changed, rather than
discovered afterwards by a moved backtest number.

## What this ADR does not decide

It does not choose any features. Which features belong in a scoring set, a
pattern classifier or a model is a Phase 4–8 modelling decision, and selecting
them now — with no out-of-sample harness — would be the overfitting the brief
prohibits. This ADR fixes only that the choice must be *stated*.

## Alternatives considered

**Convention and code review.** "Don't consume the whole registry" as a rule in
`ARCHITECTURE.md`. Free, and it fails the first time a deadline meets a
tempting nearby feature. The failure is also invisible in review: the diff that
breaks it is the diff that registers an indicator, in a file nobody associates
with the model.

**Tag features as consumable in the registry.** A `consumable: bool` on
`FeatureSpec` would stop diagnostics leaking, and nothing else. It is a property
of the feature, so every consumer still gets the same set, and a new consumable
feature still silently joins every model.

**Give each consumer its own registry.** Complete isolation, at the cost of
duplicated definitions — and the moment two consumers define `sma_50`
independently, they will drift, and stored values under one name will mean two
things. The registry must stay the single definition; the subset is a view onto
it.

**Freeze the input list inside each model artefact.** This is a real mechanism
and it is complementary rather than alternative: `ResolvedFeatureSet.version()`
produces exactly that artefact. What it cannot do alone is prevent the wrong
list being frozen in the first place, or answer *who reads this feature* before
a change.

## Consequences

- Adding a feature to the registry is now a safe, local act. It affects no
  consumer until a declaration names it.
- Changing a feature's *definition* is loud: every affected declaration's digest
  moves, and `FeatureSetCatalogue.consumers_of()` names them before the change.
- Phases 4–8 must each declare a feature set. This is friction, and it is the
  point: the declaration is where "why does this model read this?" gets
  answered while it is still cheap to answer.
- `FeatureSetCatalogue.unconsumed()` reports registry features nothing declares.
  Deliberately a review aid rather than a defect list — diagnostics and research
  features are *meant* to be unconsumed.
- Sets that feed trading decisions can be audited for undeclared rationale.
  Research and diagnostic sets are exempt, because a research set browsing
  broadly is legitimate; promoting a research result to production requires a
  narrower declaration.
