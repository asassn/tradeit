# ADR-0003: Vendor-neutral provider protocols and a synthetic default

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 1

## Context

No market data vendor has been chosen yet, and the choice is consequential:
vendors differ in survivorship handling, whether they supply real filing
timestamps, how far back they go, and how they price. Committing to one now
would mean committing before we know which of those properties we need.

Meanwhile Phase 1 needs a working end-to-end pipeline to prove the storage
design, and a test suite that runs in CI without an API key or a network call.

## Decision

**Providers are `Protocol` classes**, split by capability —
`ReferenceDataProvider`, `PriceProvider`, `FundamentalProvider`. An adapter
implements only what its vendor actually supplies. Adapters register by name;
configuration selects by name; nothing in the pipeline imports a vendor module.

**Providers return validated domain objects, not DataFrames.** The type system
then carries the contract, and vendor-specific column naming stops at the
adapter boundary.

**Providers never see the clock.** They fetch what they are asked for; the
repository layer decides what is visible. The same stored row can therefore
serve a 2019 backtest and today's live screen.

**Adapters declare their limits** via `ProviderCapabilities` — in particular
whether they supply real publication timestamps and delisted instruments. The
ingestion run records this, so a backtest report can state whether its data was
survivorship-safe rather than leaving the reader to remember.

**The default provider is synthetic**: a seeded, offline generator with a
planted breakout pattern. It makes the whole pipeline runnable from a fresh
checkout and gives later phases a series whose correct answer is known.

## Alternatives considered

**Pick a vendor now and wrap it later.** Faster initially, but vendor
assumptions leak into the schema, and the leaks are found during the migration
rather than before it.

**A single fat `DataProvider` ABC.** Forces every adapter to stub methods its
vendor cannot serve, and `NotImplementedError` at runtime is a worse contract
than a type that never claimed the capability.

## Consequences

- Swapping vendors is a config change plus one new adapter module.
- The synthetic provider is a test instrument, not a market simulator. Its
  statistical properties must never be used to justify a strategy.
- Each new adapter must pass the conformance suite in
  `tests/unit/test_synthetic_provider.py`, which asserts the properties any
  honest provider has: deterministic reads, sessions only on trading days,
  `knowledge_time` after the close, announcements before their events.
