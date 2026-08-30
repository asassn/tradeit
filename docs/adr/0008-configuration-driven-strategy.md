# ADR-0008: No strategy constant may live in code

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 2

## Context

The brief is explicit: all important strategy parameters must be
configuration-driven, and no strategy constants should be buried inside code.

The failure this prevents is specific. Thresholds written as defaults or module
constants make it impossible to answer "what rules produced this signal?"
without reading the code at the commit that was deployed at the time — and if
the code has been refactored since, not even then. Parameter sweeps become
global-variable patching. A backtest and a live session cannot be shown to have
used the same rules.

## Decision

**Every number that affects a trading decision lives in a TOML file under
`config/strategies/`.** Not a default argument, not a module constant, not a
literal inside a detector.

`StrategyConfig` defines eleven validated sections — universe, liquidity,
fundamental, technical, patterns, breakout, scoring, sizing, risk, exits, costs.
Sections are frozen Pydantic models with `extra="forbid"`, so a typo'd key is an
error rather than a silent fallback to a default the author did not intend.

Application settings (DSNs, log level, trading mode) stay in environment
variables. They are a different kind of thing: they vary by deployment and do
not affect what a strategy decides.

TOML rather than YAML because `tomllib` is in the standard library and because
its strictness about types is a feature — a threshold silently parsed as a
string is a bug that surfaces months later.

### Cross-section validation

Individual field constraints are not enough. A configuration can satisfy every
one and still be incoherent as a whole, so `StrategyConfig` validates
combinations:

- per-trade risk exceeding portfolio heat — no position could ever open
- breakeven-stop R above trailing-stop R — the trailing stop is looser than the
  breakeven stop it replaces
- a single position permitted to exceed gross exposure
- a relative-strength lookback longer than required trading history — every
  instrument passing liquidity fails on warm-up, and the screen silently returns
  nothing

The last one is the instructive case: it produces an empty result set with no
error, which is the kind of failure that gets diagnosed as "the strategy found
nothing today" for weeks.

## Alternatives considered

**Parameters in the database.** Queryable and editable through a UI, but
changing a threshold stops being a reviewable diff, and the configuration a run
used can be edited after the run. Configurations *are* stored in the database —
as immutable content-addressed rows (ADR-0007) — but the file is the source.

**Python configuration modules.** Expressive, and arbitrary code in a
configuration cannot be hashed meaningfully or reviewed as data.

**Environment variables for everything.** Flat, untyped, and a strategy with
sixty parameters becomes sixty environment variables no one can diff.

## Consequences

- Implementations receive configuration; they never define fallbacks. A
  detector with `min_base_sessions: int = 25` in its signature violates this ADR
  even though it looks harmless.
- Changing a threshold is a reviewable diff that produces a new content hash and
  therefore a new strategy identity.
- Sweeps generate configurations via `with_overrides()`, so every variation
  tried is enumerable — the only real defence against reporting the best of two
  hundred attempts as if it were the first of one.
- A test asserts the shipped `baseline.toml` matches the code defaults, so the
  file and the model cannot drift apart while appearing to agree.
