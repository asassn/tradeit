# ADR-0009: One deployable, several worker queues

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 2

## Context

The architecture names sixteen subsystems. That naturally suggests services, and
the decision should be deliberate rather than drifted into.

The actual workload: one batch of jobs after the close (roughly 90 minutes of
mostly-sequential work), intraday polling over a watchlist of tens of names, and
occasional multi-hour backtests. Single user, single region, no external
consumers.

## Decision

**One application image, several entry points**: API, scheduler, workers,
dashboard. Modules are separated by import boundaries and enforced dependency
direction, not by network calls.

Worker concurrency is split into four queues because their failure modes and
latency requirements genuinely differ:

| Queue | Work | Latency tolerance |
|---|---|---|
| `realtime` | Intraday breakout and risk checks | Seconds |
| `compute` | Indicators, scans | Minutes |
| `backtest` | Backtests, Monte Carlo | Hours |
| `maintenance` | Partitions, vacuum, backfill | Days |

A four-hour backtest must not delay a fifteen-second breakout check. That is a
scheduling problem, and queue separation solves it without a network boundary.

## Alternatives considered

**Microservices per subsystem.** Would add service discovery, network
partitions, distributed tracing, and deployment skew — where the scanner runs
one version of the scoring rules and the backtester another, which is precisely
the drift the reproducibility design exists to prevent. It would buy independent
scaling that nothing here needs.

**A single process with threads.** Simpler still, but a crashed backtest would
take down the API, and Python's GIL makes CPU-bound indicator computation
contend with request handling.

## Consequences

- Deployment is one image and one migration. Version skew between subsystems is
  structurally impossible.
- The dependency rule (`api → backtesting → portfolio/risk → strategy →
  analytics → storage → data → core`) is enforced by review and by import
  linting, not by the network. This requires discipline that service boundaries
  would enforce automatically — the trade-off is accepted knowingly.
- Horizontal scaling is per-queue worker count. If a single database instance
  ever becomes the bottleneck, read replicas come before service decomposition.
- Should this ever need to serve other consumers, the module boundaries are
  already where the service boundaries would go.
