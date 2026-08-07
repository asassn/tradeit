# ADR-0004: Live trading requires an out-of-band authorization file

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 1

## Context

The brief is explicit: the system supports paper trading first, and "live
trading must remain disabled until separately authorized."

A configuration flag alone does not meet that bar. Environment variables are set
by CI jobs, copied between shells, inherited by containers, and pasted from
documentation. `TRADEIT_TRADING_MODE=live` in a stray `.env` is a plausible
accident, and its consequence is real money.

## Decision

Live mode requires **two independent things**:

1. `trading_mode = live` in configuration, and
2. a file at `~/.tradeit/LIVE_TRADING_AUTHORIZED` whose contents are exactly
   `I authorize tradeit to place real orders`.

If the flag is set without the file, `Settings` raises `ConfigError` at
construction. The process does not start in a degraded mode — it does not start.

The authorization file lives outside the repository, outside any Docker image,
and outside the environment, so enabling live trading requires a deliberate act
on the specific machine that will place the orders.

`AsOfClock.assert_fresh()` is a second, independent guard: a paper or live clock
more than 15 minutes behind wall time refuses to act, so a stalled job cannot
trade on stale state.

## Alternatives considered

**A `--live` CLI flag.** Same failure mode as the environment variable: one
character in a scheduled command.

**A database row.** Better than an env var, but a migration or a restored dump
could carry it between environments.

**A broker-side restriction only.** Necessary but not sufficient — it is outside
this system's control and does not prevent the platform from trying.

## Consequences

- Enabling live trading is a documented, manual, machine-local act.
- CI cannot enter live mode even if a test sets the variable, which is asserted
  in `tests/unit/test_config_and_ingest.py`.
- When a broker integration is built, it must check `settings.is_live` itself
  rather than trusting its caller. This ADR governs configuration; the execution
  layer will need its own interlock ADR.
