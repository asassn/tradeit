# Proposal — buy one month of dead-company price data to measure it

**Status: PROPOSAL. Nothing bought, no vendor contacted.** The owner decides and
makes any purchase. Written against the criteria in
[`PHASE_06_PURCHASE_GATE.md`](PHASE_06_PURCHASE_GATE.md), which were fixed
before any vendor data was seen, rather than against new ones.

---

## The decision, in one sentence

**Spend $39 on one month of Sharadar's full-history US price data, to measure
whether it holds the dead companies our free sources don't — and cancel if it
doesn't.**

**What it costs to get wrong.** Buying and being wrong costs $39 and about a day
of work. Not buying leaves the dataset at 38.7% survivorship coverage, below the
45% the project's own rule requires before any backtest result can be believed.
Every free route has now been measured and none gets there
([`EDGAR_DELISTING_DENOMINATOR.md`](EDGAR_DELISTING_DENOMINATOR.md) §7h): EODHD
yielded 7.2% of the gap's pool, Tiingo 2.6% and nothing before 2015, and FMP's
free plan refused every dead company.

**Recommendation: Sharadar first, Norgate only if Sharadar fails.** Sharadar is
the cheapest way to measure, delivers plain downloadable files that work on this
Mac, and says it separates reused tickers — the one failure the purchase gate
treats as absolute. The vendor matrix already ranked it first to probe
(`PHASE_06_VENDOR_MATRIX.md` §5).

---

## Detail below — optional reading

### The gap being bought for

| | |
|---|---|
| dated Exchange Act exits | 21,618 |
| priced today | 8,371 (38.72%) |
| shortfall to 45% | **1,358** |
| Pool A — identity resolved, no prices | 4,022 |
| yield needed from Pool A alone | **33.8%** |
| where the deficit is | before 2015: 3.7% coverage pre-1999, 27.0% 1999–2006, 37.6% 2007–2014 |

### The candidates

Every price and page quote below was read on 2026-09-14 from the vendor's
public page — except Sharadar's ticker-table fields, read from an open-source
integration's documentation — which is grade `VERIFIED` for published text
under `PHASE_06_VENDOR_MATRIX.md` §0. **Every capability claim is
still `UNTESTED`** — a vendor's description of its coverage is not a measurement.

| | **Sharadar** (direct) | **Norgate Data** | **FMP, paid** | **Kibot** |
|---|---|---|---|---|
| dead companies included | "Active and delisted coverage extends back to the 90s" | "25222 delisted securities from the start of 1950"; **Platinum and Diamond only** | "Delisted Companies" listed on paid plans; whether their **price history** is served is not stated | claimed; eliminated earlier on price |
| history start | "since 1998"; ticker table `firstpricedate` "min 1986-01-01" | Platinum "back to 1990"; Diamond "back to 1950" | sources disagree: 5 years (Starter) and 30+ years on higher plans | "up to 64 years" |
| reused tickers kept apart (purchase-gate N4, absolute) | `permaticker`, "Sharadar's unique ID"; tickers "uniquified by Sharadar if reused" | `assetid` "does not change over the lifetime of a security, including through symbol changes" | not established | not established |
| price for full history | **Prices plan $39/month or $299/year**; Bundle $69/month or $499/year | **Platinum USD 346.50 / 6 months, USD 630 / 12 months**; Diamond USD 433.13 / 787.50; no monthly term listed | **not established** — pricing pages refused automated reading (HTTP 403) | $990–$2,400 one-time, eliminated in the matrix |
| free test before paying | free tier is the 30 Dow companies only — **cannot test dead companies** | 2-year trial — "limited to two years of history", so cannot test pre-2015 deaths | free plan measured: refuses every dead symbol | — |
| delivery | "pre-prepared bulk downloads", CSV and API | desktop updater; its requirements page references .NET Framework and Windows — **macOS support not confirmed** | REST API | archive files |
| licence as published | "Personal Use License" | "one individual natural person", personal trading and backtesting | not established | — |

**Why no free test is possible.** Sharadar's free data is 30 living companies;
Norgate's trial stops at two years. Neither can show whether pre-2015 dead
companies are present, which is the entire question. The cheapest measurement
of either is a paid month — $39 for Sharadar against a $346.50 six-month
minimum for Norgate.

**On licence.** Both sell a personal-use licence. The owner has already
described this project to EODHD as one individual's personal research on one
computer, with nothing shared; whether a given vendor reads that as personal use
is the vendor's classification and the owner's decision, as it was with EODHD.
It is noted here as a question to confirm, not scored.

### What the $39 would measure — thresholds fixed now, before any data exists

The month is a measurement, and it is judged on the purchase gate's own rules.
Nothing enters `research-01` until these are read.

| # | test | pass | source |
|---|---|---|---|
| 1 | **Pool A yield** — the same random, staged probe that measured Tiingo, graded on Sharadar's `permaticker` and its first and last price dates against each EDGAR exit | **≥ 33.8%** PLAUSIBLE, which alone clears 45% | §7h |
| 2 | **pre-2015 deaths** — the deficit's actual location | a material share of Pool A exits before 2015 graded PLAUSIBLE, not just recent ones | §7h era table |
| 3 | **dead controls** — of the 30 manually verified | ≥ 26 of 30 reconstructed; all six short-lived failures present | gate G4 |
| 4 | **reused tickers** — BBBY, GM, AOL | zero series spliced across two companies; each company separable | gate G5, **N4 absolute** |
| 5 | **raw prices and splits** | unadjusted prices available; implied split ratios step on known dates | gate G6, G7 |
| 6 | **bulk download in the month** | the full history lands inside the 30 days | gate G8 |

**Stop rule.** If test 4 fails, stop — N4 is absolute. If test 1 comes in under
about 15%, or test 2 finds nothing before 2015, cancel before renewal and move
to Norgate. Only a pass on all six reaches a separate decision about ingesting
the data and about an annual plan.

**One possible bonus, unmeasured.** Sharadar's ticker table carries each
company's SEC filings link. If that link holds a CIK, the same month could also
supply identities for some of the 9,311 dead companies we have not been able to
name (Pool B). That is a hope until it is read in a real file.

### What stays broken without it

- **The 45% rule stays in force**, so no backtest or signal result on this
  dataset can be believed — including any future strategy.
- **Research stays limited to paired comparisons** (§7h option 3), which survive
  the coverage hole but cannot answer "what does this strategy earn".
- **The pre-2015 deficit is untouched** — the era `research-01` was built for.

### If Sharadar fails

Norgate is the fallback: its stated dead-company count and 1990 start fit the
gap best, but it costs $346.50 minimum to measure, and its Windows desktop
delivery needs confirming against a Mac before anything is paid. Its trial
cannot answer the question for the same reason Sharadar's free tier cannot.

### Sources

- Sharadar plans: <https://sharadar.com/subscribe>
- Sharadar coverage and free tier: <https://sharadar.com/>
- Sharadar ticker table fields (`permaticker`, reused tickers, `firstpricedate`): <https://pkg.go.dev/github.com/stockparfait/stockparfait/ndl/sharadar>
- Norgate delisted coverage: <https://norgatedata.com/data-content-tables.php>
- Norgate US Stocks prices and levels: <https://norgatedata.com/stockmarketpackages.php>
- Norgate trial and `assetid`: <https://norgatedata.com/data-package-faq.php>
- Norgate system requirements: <https://norgatedata.com/system-requirements.php>
- Norgate licence: <https://norgatedata.com/subscribe/eula.php>
- FMP plan features (prices unreadable): <https://site.financialmodelingprep.com/insights/platform/how-to-choose-the-right-financial-modeling-prep-plan-for-your-workflow>
