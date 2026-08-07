# API Specification

FastAPI service, built in Phase 9. This document is the contract it will
implement; nothing here is running yet.

## Principles

**Read-mostly.** The API surfaces what the pipeline produced. It does not screen
on demand, does not size positions, and does not place orders directly — those
happen in scheduled jobs whose results are reproducible. An endpoint that ran a
scan inline would produce a result with no manifest, which is a result nobody
can reproduce.

**Every decision response carries its manifest.** Scores, candidates and
backtest results include the digests that produced them. A number without its
provenance is an opinion.

**`as_of` is a first-class parameter.** Any endpoint reading historical state
accepts it and defaults to now. This is the same clock the pipeline uses, so the
API cannot see data the pipeline could not.

**Writes are narrow and audited.** The only mutating endpoints are watchlist
curation, journal annotation, configuration management, and backtest submission.
Order placement is not exposed over HTTP in any mode.

## Conventions

- Base path `/api/v1`. Breaking changes get `/api/v2`; the old version keeps
  serving until its consumers are gone.
- Cursor pagination (`?limit=&cursor=`), never offset — offsets skip rows when
  the underlying set changes between pages.
- Dates are ISO `YYYY-MM-DD`; instants are RFC 3339 with an explicit offset.
- Money is a decimal *string* in JSON, never a float. `"123.45"` survives a
  JavaScript client; `123.45` does not always.
- Errors are RFC 9457 problem documents.

---

## Scanner and candidates

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/scans` | Scan runs, newest first |
| `GET` | `/scans/{run_id}` | One run with its manifest and summary counts |
| `GET` | `/scans/{run_id}/candidates` | Ranked candidates for that run |
| `GET` | `/scans/{run_id}/rejections` | What was screened out and why |
| `GET` | `/candidates` | Current candidates (`?session_date=&min_score=&sector=`) |
| `GET` | `/candidates/{instrument_id}` | One candidate with full score breakdown |

```jsonc
// GET /api/v1/candidates?session_date=2026-03-16&min_score=0.7
{
  "session_date": "2026-03-16",
  "manifest": {
    "run_id": "scan-2026-03-16",
    "as_of": "2026-03-16T21:15:00Z",
    "manifest_digest": "9f2c1a7b3e04",
    "strategy_config": "baseline@187aa4b2675e",
    "data_snapshot": "eod@4c1d8e93aa20",
    "code_version": "0.2.0"
  },
  "items": [
    {
      "instrument_id": 4412,
      "ticker": "EXMP",
      "rank": 1,
      "score": 0.81,
      "direction": "long",
      "components": [
        {"name": "relative_strength",     "normalised": 0.92, "weight": 0.25, "contribution": 0.230},
        {"name": "breakout_confirmation", "normalised": 0.88, "weight": 0.20, "contribution": 0.176},
        {"name": "pattern_quality",       "normalised": 0.79, "weight": 0.20, "contribution": 0.158},
        {"name": "fundamental_quality",   "normalised": 0.71, "weight": 0.15, "contribution": 0.107},
        {"name": "sector_strength",       "normalised": 0.68, "weight": 0.10, "contribution": 0.068},
        {"name": "volume_accumulation",   "normalised": 0.61, "weight": 0.10, "contribution": 0.061}
      ],
      "pattern": {"type": "cup_with_handle", "pivot_price": "84.20", "stop_price": "77.50"},
      "breakout": {"status": "confirmed", "volume_ratio": 2.4},
      "earnings_in_sessions": 12
    }
  ],
  "next_cursor": null
}
```

The `components` array is the explanation, and it is stored rather than
recomputed — a rationale assembled at render time is a rationalisation.

---

## Patterns and breakouts

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/patterns` | `?status=&pattern_type=&instrument_id=&session_date=` |
| `GET` | `/patterns/{id}` | One pattern with its full breakout event history |
| `GET` | `/instruments/{id}/patterns` | Pattern history for one instrument |
| `GET` | `/breakouts` | `?status=&session_date=` — approaching, triggered, confirmed |
| `GET` | `/breakouts/stats` | False-breakout rate by pattern type and regime |

`/breakouts/stats` is the endpoint that says whether the confirmation rules earn
their keep: the ratio of `confirmed` to `triggered` events, sliced by pattern
type and market regime.

---

## Portfolio and positions

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/portfolios` | All portfolios with mode and current equity |
| `GET` | `/portfolios/{id}` | One portfolio |
| `GET` | `/portfolios/{id}/equity-curve` | `?from=&to=&resolution=` |
| `GET` | `/portfolios/{id}/performance` | Metrics with trade count alongside every ratio |
| `GET` | `/portfolios/{id}/positions` | `?status=open` |
| `GET` | `/positions/{id}` | Position with executions and journal entries |
| `GET` | `/portfolios/{id}/orders` | `?status=&from=&to=` |
| `GET` | `/portfolios/{id}/trades` | Closed round trips |
| `GET` | `/portfolios/{id}/trade-plan` | Next session's proposed orders, unplaced |

`/trade-plan` is the one an operator reads each evening. It returns the sized,
risk-approved orders the pipeline generated, each with the candidate that
justified it and the risk verdict that allowed it — including any reduction and
which rule caused it.

Every portfolio response echoes `"mode": "paper" | "backtest" | "live"` at the
top level. A UI that cannot tell paper from live at a glance will eventually
confuse them.

---

## Risk

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/portfolios/{id}/risk` | Current heat, exposures, limit headroom |
| `GET` | `/portfolios/{id}/risk/history` | Daily risk snapshots |
| `POST` | `/portfolios/{id}/risk/evaluate` | Dry-run a hypothetical trade against the rules |

```jsonc
// GET /api/v1/portfolios/1/risk
{
  "portfolio_id": 1,
  "mode": "paper",
  "as_of": "2026-03-16T21:30:00Z",
  "equity": "104382.11",
  "open_risk": "4180.55",
  "heat_pct": 0.0400,
  "limits": [
    {"type": "portfolio_heat",   "measured": 0.0400, "limit": 0.0600, "headroom": 0.0200, "status": "ok"},
    {"type": "max_positions",    "measured": 8,      "limit": 12,     "headroom": 4,      "status": "ok"},
    {"type": "sector_exposure",  "measured": 0.2900, "limit": 0.3000, "headroom": 0.0100, "status": "near_limit",
     "detail": "Information Technology"},
    {"type": "max_drawdown",     "measured": 0.0410, "limit": 0.1500, "headroom": 0.1090, "status": "ok"}
  ],
  "trading_permitted": true,
  "blocking_reasons": []
}
```

`POST /risk/evaluate` is a dry run: it takes a proposed instrument, entry and
stop, and returns the verdict every rule would give, including which one binds.
It has no side effects and places nothing.

---

## Backtests

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/backtests` | Submit a run; returns 202 with a job handle |
| `GET` | `/backtests` | `?strategy=&status=` |
| `GET` | `/backtests/{id}` | Spec, manifest, metrics, caveats |
| `GET` | `/backtests/{id}/trades` | Every simulated trade |
| `GET` | `/backtests/{id}/equity-curve` | Daily equity |
| `GET` | `/backtests/{id}/attribution` | `?by=sector\|regime\|exit_reason\|factor` |
| `POST` | `/backtests/{id}/monte-carlo` | Launch a distributional study |
| `GET` | `/backtests/{id}/monte-carlo/{mc_id}` | Percentiles and ruin probability |

Backtest responses always include `"trustworthy": bool` with the reasons it is
false — too few trades, or data caveats carried forward from ingestion. A result
built on survivorship-biased data must not be presentable as a clean one by
omission, and a Sharpe ratio from nineteen trades must not be quoted at all.

---

## Journal

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/journal` | `?portfolio_id=&from=&to=&entry_type=` |
| `GET` | `/journal/{id}` | One entry with full decision context |
| `POST` | `/journal` | Add a human annotation |
| `GET` | `/positions/{id}/journal` | Everything recorded about one position |

Journal entries are append-only. There is no `PUT` and no `DELETE`: a record you
can edit after a loss is not a record of what you thought before it.

---

## Configuration

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/strategies` | Strategies with their active configuration |
| `GET` | `/strategies/{name}/configurations` | Version history with activation intervals |
| `GET` | `/configurations/{digest}` | Full parameter set by content hash |
| `GET` | `/configurations/{a}/diff/{b}` | Dotted paths that differ |
| `POST` | `/configurations/validate` | Validate without storing |
| `POST` | `/strategies/{name}/activate` | Activate a configuration (audited) |

`POST /configurations/validate` runs the same cross-section validators the
loader does — per-trade risk against portfolio heat, trailing versus breakeven
stop ordering, lookback against required history. It exists so a proposed change
fails in a review tool rather than at 21:15 on a trading day.

Activation writes a `system_logs` row and closes the previous configuration's
interval, so "which rules were live on 14 March?" stays a query.

---

## Operations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness. No dependencies checked. |
| `GET` | `/ready` | Readiness: database, cache, migration version |
| `GET` | `/jobs` | Recent job runs `?session_date=&status=` |
| `GET` | `/jobs/gate` | Whether trading is permitted, and what is blocking |
| `GET` | `/data-quality` | Open integrity issues |
| `GET` | `/instruments` | Search `?q=&exchange=&as_of=` |
| `GET` | `/instruments/{id}/bars` | `?timeframe=&from=&to=&adjustment=` |

`/jobs/gate` is the single question an operator asks each evening: did every
`blocks_trading` job complete for this session? If not, no plan is generated,
and the endpoint says which job failed.

---

## Authentication and authorisation

Bearer tokens, short-lived, issued against three roles:

| Role | Can |
|---|---|
| `viewer` | Read everything except credentials |
| `operator` | Viewer, plus journal writes, watchlist edits, backtest submission |
| `admin` | Operator, plus configuration activation and strategy enablement |

No role can place an order over HTTP. That is not an authorisation setting — the
endpoint does not exist. Order placement happens in the execution service,
behind the live-trading interlock (ADR-0004), and adding an HTTP path to it
would put the interlock behind a network boundary where a token compromise could
reach it.

Every mutating request is logged to `system_logs` with the actor, the payload
digest, and the resulting state change.

## Rate limits and caching

Read endpoints are cached in Redis keyed by `(path, query, as_of, manifest
digest)`. Because manifests are content-addressed, a cache entry is valid until
the underlying run changes — no TTL guessing, and no possibility of serving a
result computed from a configuration that has since been replaced.
