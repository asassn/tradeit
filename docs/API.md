# API Specification

FastAPI service. **This document is the contract the service will grow into,
and most of it is not implemented.** It is kept as the target rather than
trimmed to what exists, because the conventions below — manifests on every
decision response, `as_of` as a first-class parameter, no order placement over
HTTP in any mode — are decisions, not aspirations, and each new route is held
to them.

**"Built in Phase 9" was wrong and is corrected here**: the service is Phase
10's, and the sentence *"nothing here is running yet"* stopped being true on
2026-11-16.

## What is actually running

Measured, not planned. `src/tradeit/api/app.py`, started with:

```bash
PYTHONPATH=src .venv/bin/python -m tradeit.cli dashboard --record <path>
```

| route | answers |
|---|---|
| `GET /` | the screen — the committed React bundle, or the hand-written fallback page when none is built. Served `no-store` |

**`/securities/search` must stay declared before `/securities/{id}/valuation`.**
FastAPI matches routes in declaration order, so reversing them makes `search`
be tried as a `security_id`, fail integer validation and answer `422` — the
search box would simply stop working. A test holds the order.
| `GET /health` | is the record reachable, which record is it, does it hold anything |
| `GET /runs` | every recorded run, with trades **counted from the rows** |
| `GET /runs/{id}/trades` | one run's trades, with the total so a page cannot pass for the whole |
| `GET /walkforward` | in-sample windows paired with their out-of-sample tests, **with the distribution beside the mean** |
| `GET /sessions` | which sessions the record can speak about |
| `GET /sessions/{date}` | what was decided, and **`missing`** — read that first |
| `GET /instruments` | which securities the record can speak about |
| `GET /instruments/{id}/panel` | one security, each horizon separately, absent ones with a reason |
| `GET /instruments/{id}/scores` | one security's scores as recorded, unaggregated |
| `GET /forward/health` | the nightly capture agent's liveness, from **the capture record**. `501` when none is attached |
| `GET /securities/search?q=` | find a security by ticker or company name, from the corpus |
| `GET /securities/{id}/valuation` | the valuation panel, read from **the corpus**. `501` when no corpus is attached |
| `GET /securities/{id}/prices?basis=raw\|total` | the price history on either basis, sampled for drawing with exact extremes |
| `GET /securities/{id}/fundamentals` | the as-filed annual statement spine, **first-filed values only** |
| `GET /securities/{id}/provenance` | ticker intervals with their filing citations, corporate actions, disputed splits |

**There are no write routes**, so the "writes are narrow and audited" section
below describes none of what exists. The constraints the running service
enforces are in
[ADR-0035](adr/0035-the-dashboard-shows-what-the-record-holds.md).

**Three databases, and the routes are why.** Everything above reads the
decision record --- what the system decided. `/securities/{id}/valuation`
reads `research-01` --- what the world did. They are **separate id spaces**:
instruments live in the record, securities live in the corpus, and a route
accepting one for the other would return another company's valuation, a wrong
answer that looks entirely right. Start with `--corpus` to enable it; without
it the route answers `501`, because an empty panel would read as a claim about
the company rather than about this process.

`GET /forward/health` reads a **third** database again: the nightly capture
writes `forward.sqlite`, which is a different file from every record the
service could previously open — which is exactly why the one live-updating
part of this system had no web surface at all until 2026-11-17. **Liveness is
reported as an age, not a count**: a scheduled job does not crash, it stops,
and a payload of healthy totals with no age would show thousands of
observations beside a dead agent.

**The fundamentals route reports what was FIRST filed**, with the date each
figure became public. A later filing restating the same period is not shown:
it was not knowable at the time and acting on it is look-ahead. Apple's FY2017
revenue is the worked example — first filed in **2019**, when the FY2019 10-K
restated it under the ASC 606 tag.

Three rules travel with it. **A balance is an instant and a flow is not**, so
balance-sheet lines read `duration_qtrs = 0` and income and cash-flow lines
read `4`; querying one duration for every line silently removes the whole
balance sheet. **The columns are fiscal years** and a balance is read *as at*
each one, because instants land on quarter ends and mixing the two produces a
statement whose holes are an artefact of the join. And **revenue is coalesced
across the ASC 606 rename** — reading either tag alone shows a company whose
revenue stops or starts in 2018, which is an accounting-standard change
wearing the appearance of a business event.

**The price route serves both bases and names which it is showing.** Neither
is the default answer to every question: `v_prices_raw` is what the market
printed and what a multiple needs, and it jumps at every split; `v_prices` is
comparable across time and is **not point-in-time**, because the adjustment
embeds every corporate action since. On Apple the two differ by a factor of
112. Splits travel with either basis — on raw they explain the cliffs, on
adjusted they explain why there are none.

**The drawn series is sampled; the summary is not.** A 9,237-session history
is bucketed for drawing, but the high, the low and their dates are measured
over every row, so a sampled line can never misreport an extreme it skipped.

**The provenance route is the corpus's own argument**, and the content that
distinguishes `research-01` from a price feed: every ticker interval carries
the filing text that evidenced it, every split the corpus had to settle
carries its verdict and who decided it. Citations travel in full and are never
truncated — an interval without its evidence is an assertion. Two read rules
travel with the payload: `v_security_tickers` has the half-open interval
arithmetic done (reading the stored `valid_to` as inclusive is trap 0.2), and
an interval evidenced by `sec_company_tickers` proves the **current** holding
only, so its start date is a floor rather than a finding.

**The valuation route returns no verdict**, by decision rather than omission
--- [ADR-0036](adr/0036-valuation-is-a-panel-not-a-verdict.md). It reports a
free cash flow yield on enterprise value, where that sits in the security's
own history, what growth the price implies at several required returns, and
whether cheapness arrives with deterioration.

**Not implemented, and each for a stated reason:** sector-relative comparison
(needs the sector population resolved as of the session, a corpus-wide read),
live news and attention (gated on a source with honest publication
timestamps), and the onboard assistant (only as honest as the record beneath
it, which is one backtest and eighteen forward sessions deep).

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
