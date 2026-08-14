# Phase 6 — vendor and data-contract verification

**Nothing was purchased. No subscription was taken. No implementation began.**

## 0. Verification conditions — read this first

This session's egress policy **blocks every primary vendor source**. Confirmed
`EGRESS_BLOCKED` on: `data.nasdaq.com`, `help.data.nasdaq.com`, `sharadar.com`,
`quantrocket.com`, `resources.quandl.com`, `sec.gov`, `polygon.io`, `eodhd.com`,
`kibot.com`, `flounderteam.github.io`. The proxy README states these are
organisation policy denials and are not to be routed around, so they were not.

What *was* reachable: web search (result extracts only) and
`raw.githubusercontent.com`.

**Confidence grades are used strictly, and no secondary-source claim is promoted
to VERIFIED.**

| grade | meaning |
|---|---|
| **VERIFIED** | read directly from a primary or open-source integration source *in this session* |
| **USER-VERIFIED** | read from the primary source **by the project owner, outside this session**, and reported here. Treated as authoritative for licence text; still subject to the data probe for capability claims |
| **CORROBORATED** | consistent across ≥2 independent search extracts, primary page not reachable |
| **UNVERIFIED** | single second-hand extract, or not established at all |

Consequently **all pricing in this document except where marked USER-VERIFIED is
UNVERIFIED**.

---

## 1. The licensing question — now answered, and it eliminates two vendors

> subscribe one month → download → cancel → retain and use indefinitely

This is not a preference. It is **load-bearing**: `research-01` is a permanent
research corpus, and every derived artefact in the platform — adjusted series,
features, backtests, `scan_runs` — is a *dataset derived from* whatever the
corpus was built on. A licence that requires deleting derived datasets on
termination requires deleting `research-01` itself.

### 1.1 Sharadar — **PROHIBITED**

The current **Sharadar Personal Use License** requires, upon termination:

- discontinuing use of Services Data;
- deleting all copies of Services Data within 30 days;
- **deleting datasets *derived from* Services Data within 30 days.**

— *USER-VERIFIED from the current licence text.*

The third clause is decisive and disqualifying. Under it, a one-month download
followed by cancellation would oblige us to delete not only the price and
fundamental files but `research-01`, every scan run derived from it, and every
result ever published from it.

**Verdict: Sharadar is not recommended for the one-month permanent-backfill
model.** It may only be reconsidered under a *different written commercial or
custom licence that explicitly grants post-termination retention* — a
possibility, not a plan, and not something to assume.

This supersedes the previous "UNRESOLVED / provisional preference" verdict in
this document. The earlier negative signal (the Nasdaq Private Market data terms)
pointed the right way; the governing licence confirms it.

### 1.2 EODHD — **PROHIBITED**

EODHD's terms likewise require deletion of stored provider data within one month
after termination — *USER-VERIFIED*. EODHD therefore fails the same requirement
for the same reason, and is eliminated as a permanent-archive source.

EODHD's data *capabilities* were never the problem. The licence is.

### 1.3 Kibot — **PERMITTED, per licence text**

Kibot's licence explicitly states that delivered data may be kept permanently
and that cancellation does not require deletion — *USER-VERIFIED*.

This is the only candidate so far whose licence, on its own terms, supports the
architecture. **It does not yet establish that the data is fit for purpose**;
that is what `docs/KIBOT_DATA_PROBE.md` exists to determine, and every capability
claim below is explicitly untested.

### 1.4 The blocker nobody had checked: Twelve Data and FMP

An implication of §1.1 worth stating plainly, because it was previously
invisible: **we have never verified our own existing subscriptions' retention
terms.** `full-01` was built from Twelve Data prices. If Twelve Data's terms
require deletion of stored data on termination, then:

- `full-01` and any successor corpus containing Twelve Data prices are
  retention-encumbered;
- the *forward accumulation* model in `FORWARD_SURVIVORSHIP_SYSTEM.md` — which
  assumes today's Twelve Data bars become permanent history — does not hold.

`full-01` is frozen, machinery-validation-only, and never cited for economic
claims, so nothing published is at risk today. But **the forward model must not
be built on an unverified retention right**, having just eliminated two vendors
for exactly that defect.

**Action: verify Twelve Data and FMP post-termination retention terms before
`research-01` embeds either source permanently.** Status: **UNVERIFIED**.
Recorded as Blocker 3 in `PHASE_06_IMPROVEMENT_PLAN.md` §1.

**Attempted in this session and blocked.** `twelvedata.com` and
`site.financialmodelingprep.com` both returned `EGRESS_BLOCKED` from the egress
proxy, as did `sec.gov` and every other vendor domain. Per the proxy README these
are organisation policy denials and were not routed around. **No retention
determination for either vendor is claimed, and none is inferred from the fact
that our API access currently works** — access and retention are different
grants.

The exact written questions to send are in
[`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §3.1, and the register of
determinations for every source lives there rather than here.

**Classification for both: `UNCLEAR — WRITTEN CONFIRMATION REQUIRED`, which is
treated as prohibited** for the permanent corpus until answered.

---

## 2. Vendor matrix

Split into two tables, because the licence findings split the problem: the
**price/lifecycle spine** and the **fundamentals spine** now have different
answers and different candidate sets.

### 2a. Price and security-lifecycle history

| # | criterion | **Kibot** | **Sharadar** | **EODHD** | **Polygon / Massive** | **Twelve Data** |
|---|---|---|---|---|---|---|
| L | **post-termination retention** | **permanent retention permitted; cancellation does not require deletion — USER-VERIFIED** | **PROHIBITED** — 30-day deletion incl. *derived* datasets — USER-VERIFIED | **PROHIBITED** — one-month deletion — USER-VERIFIED | not established | **UNVERIFIED — §1.4** |
| 1 | price history start | "up to 64 years" daily EOD; 1998 coverage claimed — USER-VERIFIED (vendor claim) | "deep history to 1998" — CORROBORATED | "from January 2000" vs "30+ years" — CORROBORATED, internally inconsistent | not established | "back to the first trading date" — CORROBORATED |
| 2 | active **and** delisted | **active + delisted rosters, and a delisted-only roster — USER-VERIFIED (vendor claim); completeness UNTESTED** | yes — CORROBORATED | yes — CORROBORATED | **"spotty at best" — CORROBORATED** | not established |
| 3 | delisted history truly downloadable | **UNTESTED — probe item A** | claimed — CORROBORATED | claimed — CORROBORATED | doubtful | unknown |
| 4 | permanent identifier | **UNTESTED — probe item D. Expect none; expect ticker-keyed files** | `permaticker` — UNVERIFIED | not established | not established | not established |
| 5 | raw / unadjusted prices | **unadjusted, split-adjusted and fully-adjusted equity data — USER-VERIFIED (vendor claim); methodology UNTESTED — probe item E** | yes, three bases — CORROBORATED | not established | flat files — CORROBORATED | in use for `full-01` |
| 7 | splits | **UNTESTED — probe item E** | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes |
| 8 | dividends | **UNTESTED — probe item E** | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes |
| 9 | delisting **reason** | **expect none — a price vendor. Reasons come from EDGAR** | `ACTIONS` — CORROBORATED | delisted product — CORROBORATED | weak | not established |
| 16 | API vs bulk | **UNTESTED — probe item A** | both — UNVERIFIED | both — CORROBORATED | REST + S3 — CORROBORATED | REST |
| 18 | price | **~$14/month EOD subscription — USER-VERIFIED (vendor page); exact tier for the full historical universe UNTESTED — probe item A** | UNVERIFIED (~$69/mo cited) | UNVERIFIED (~€59.99/mo cited) | UNVERIFIED (~$29/mo cited) | existing subscription |

### 2a-i. Intraday history — a separate corpus, a separate decision

Added because `research-01` is no longer the only corpus: the Day mandate and the
Swing mandate's trigger layer need true 1-minute history, which cannot be derived
from daily bars at any price (`MULTI_TIMEFRAME_MANDATES.md` §6.2). **This does
not gate the EOD decision and must not delay it.**

Every cell is **UNTESTED**. Probe items H1–H12 in `KIBOT_DATA_PROBE.md`.

| # | criterion | **Kibot** | **Twelve Data** | **Polygon / Massive** |
|---|---|---|---|---|
| I-L | **post-termination retention** | permanent retention permitted per the licence — USER-VERIFIED, **assumed to cover intraday products; confirm** | UNVERIFIED — §1.4 | not established |
| I-1 | historical intraday exists | advertises intraday products — untested | intraday API, depth not established | flat files include trades/aggregates — CORROBORATED |
| I-2 | **raw 1-minute bars** | untested — H2 | untested | untested |
| I-3 | earliest intraday history | untested — H3 | untested | untested |
| I-4 | **delisted securities intraday** | **expected no** — H4 | expected no | expected no |
| I-5 | **bulk file delivery** | untested — H5. **Hard requirement**: ~295k REST requests otherwise | REST only, as used today | S3 flat files — CORROBORATED, its strongest feature |
| I-6 | regular vs extended hours separable | untested — H8 | untested | untested |
| I-7 | timestamp convention (UTC? open- or close-stamped?) | untested — H9 | untested | untested |
| I-8 | corporate-action treatment intraday | untested — H10 | untested | untested |

**Note the reversal:** Polygon is ruled out for the EOD corpus because delisted
coverage is its weakest area, but bulk flat-file delivery is its *strongest* —
and bulk delivery is the hard requirement for intraday, where delisted coverage
is expected to be unavailable from everyone. **Polygon is therefore not ruled out
for `intraday-01`** and should be evaluated on item H alongside Kibot. Its
retention terms are unestablished and decide whether it is a candidate at all.

### 2b. Fundamentals and filing metadata

| # | criterion | **SEC EDGAR** | **Sharadar** | **EODHD** | **Twelve Data** | **FMP** |
|---|---|---|---|---|---|---|
| L | **post-termination retention** | **public domain — no licence, no termination — VERIFIED** | **PROHIBITED — §1.1** | **PROHIBITED — §1.2** | UNVERIFIED — §1.4 | UNVERIFIED — §1.4 |
| 10 | fundamentals | **XBRL values 2009+ — CORROBORATED** | SF1, ~150 indicators — CORROBORATED | Extended Fundamentals — CORROBORATED | **5 years only — CORROBORATED, disqualifying** | **excluded by project rule** |
| 11 | as originally reported | **yes — "as filed", uncorrected — CORROBORATED** | `ARQ`/`ARY`/`ART` — VERIFIED | not established | no | excluded |
| 12 | restated | amendments are separate filings | `MRQ`/`MRY`/`MRT` — VERIFIED | not established | no | excluded |
| 13 | **actual filing date** | **`filed` in full-index 1994 Q3+; `filed` in FSDS 2009+ — VERIFIED** | `DATEKEY` — VERIFIED | not established | no | excluded |
| 14 | accession / filing id | **`adsh`, primary key — CORROBORATED** | not established | not established | no | excluded |
| 15 | genuinely PIT before 2009 | **filing *dates* yes from 1994 Q3; machine-readable *values* no before 2009** | moot — licence PROHIBITED | moot — licence PROHIBITED | n/a | n/a |
| 4 | permanent identifier | **CIK — VERIFIED** (but CIK↔ticker for 1998–2008 is the hard part; see improvement plan §5) | — | — | — | — |

### What the matrix now decides on its own

- **Sharadar and EODHD: eliminated on licence, not on capability.** No probe of
  either is worth running for the permanent-archive role. Both remain
  theoretically available as *recurring subscriptions*, which is a different
  funding model and a different decision.
- **Polygon/Massive: still ruled out** on delisted coverage — the entire problem
  being solved.
- **Twelve Data: retained for forward daily prices**, ruled out for historical
  fundamentals (5 years), and now carrying an **open retention question** (§1.4).
- **FMP: excluded by standing project rule** for fundamentals, ratios, earnings,
  estimates, statements, OHLCV, insider and institutional data. Permitted role is
  corporate-action and symbol-change corroboration only.
- **SEC EDGAR: adopt regardless, immediately.** Free, public domain, no
  termination clause to fail, and the only source that can independently *verify*
  or *enumerate* what a price vendor claims.
- **Kibot: the only live candidate for the price spine** — and entirely
  unverified as data. See §3.

---

## 3. Kibot — what is claimed, and what must be proven

Everything in this section is a **vendor claim relayed via USER-VERIFIED reading
of Kibot's own pages**, not a measurement. The distinction matters more here than
anywhere else in this document, because the claim being made — a complete
survivorship-safe US equity history for ~$14/month — is one that no other vendor
in the matrix makes at that price.

| claimed | status |
|---|---|
| delivered data may be kept permanently; cancellation does not require deletion | **USER-VERIFIED licence text.** The only capability-independent fact here, and the reason Kibot is a candidate at all |
| ~$14/month EOD subscription | USER-VERIFIED page price. **Which tier actually exposes the full historical delisted universe is untested** |
| up to 64 years of daily EOD history, stocks/ETFs/futures/forex | vendor claim, **untested** |
| unadjusted / split-adjusted / fully-adjusted equity data | vendor claim, **untested** |
| active+delisted and delisted-only rosters, 1998 coverage | vendor claim, **untested** |

**A cheap price for a claim nobody else makes is a reason for more scrutiny, not
less.** The specific failure mode to look for is a delisted roster that exists
but is thin: heavy on large, well-known failures and light on exactly the
short-lived, small, thinly-traded 1999–2002 listings whose absence *is*
survivorship bias. That is probe item G, and it is the one that can fail while
every other item passes.

**Kibot is not recommended for purchase in this document.** It is recommended as
the sole subject of a data probe — see `docs/KIBOT_DATA_PROBE.md` §8 for the
minimum-cost way to run that probe and the decision gate that follows it.

---

## 4. The pre-2009 point-in-time question, restated

**XBRL does not exist before 2009.** Any vendor's pre-2009 fundamentals were
derived by parsing filing documents, and a `filed_at` that was *reconstructed*
from a period-end rather than taken from the filing record would make the
pre-2009 segment **silently non-point-in-time** — worse than not having the data,
because the defect is invisible in aggregate and contaminates precisely the
dot-com results the corpus exists to produce.

The licence findings change who this test applies to. Both vendors that could
have supplied pre-2009 fundamental *values* are eliminated, so **there is
currently no candidate to run this test against.** The test itself is retained
verbatim, to be applied to any future pre-2009 fundamentals source:

**Acceptance rule, fixed in advance:** a vendor date field whose distribution
clusters on fiscal quarter-ends rather than on plausible filing dates is **not**
point-in-time, whatever the vendor calls it. Compare against EDGAR's `filed` for
the same accession, by year, across 1998–2008.

Meanwhile, EDGAR supplies the *dates* authoritatively from **1994 Q3** for free,
which is what makes deferring pre-2009 *values* survivable — see
`RESEARCH_01_DATA_CONTRACT.md` §7.

---

## 5. Recommendation

1. **Do not purchase Sharadar or EODHD** for the permanent archive. Eliminated on
   licence. Reconsider only under a written retention-granting licence.
2. **Do not purchase Kibot yet.** Run the probe in `docs/KIBOT_DATA_PROBE.md`
   first. The licence is right; the data is unproven.
3. **Adopt SEC EDGAR now.** Free, permanent, and — via Form 25/15 enumeration —
   the instrument that *measures* whether any price vendor's delisted roster is
   complete. This work is useful under every outcome and depends on no purchase.
4. **Verify Twelve Data and FMP retention terms** before either becomes part of a
   permanent corpus (§1.4).
5. **Do not accept a subscription source for pre-2009 fundamentals** if
   cancellation would force deletion of `research-01`. On current findings, that
   rules out every candidate examined, and the recommended answer is to defer
   pre-2009 fundamental *values* rather than to accept an encumbered source.

---

## 6. How to resolve a retention question before spending anything

Retained from the previous revision because it worked — the question below is
what produced the findings in §1. Ask in writing, keep the reply, and treat
silence as prohibition.

> Does our subscription permit us to download the full historical dataset during
> the subscription period, cancel, and then **retain and continue to use that
> downloaded data internally, indefinitely, after cancellation** — including
> datasets and research results *derived* from it — with no redistribution and no
> external publication? If yes, which clause of which agreement grants it?

Three outcomes: **ALLOWED in writing** → proceed to the probe. **PROHIBITED** →
eliminated for the archive role. **No clear answer** → treat as prohibited.

---

## Sources

- **Sharadar Personal Use License** — 30-day deletion of Services Data *and
  derived datasets* on termination — **USER-VERIFIED, primary source, blocked in
  this session**
- **EODHD terms** — one-month deletion of stored provider data after termination
  — **USER-VERIFIED, primary source, blocked in this session**
- **Kibot licence and EOD subscription pages** — permanent retention permitted;
  ~$14/month; 64-year daily history; adjustment bases; delisted rosters —
  **USER-VERIFIED, primary source, blocked in this session** (`kibot.com`
  returned `EGRESS_BLOCKED` here)
- [quantrocket-client `fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py) — read directly; the only VERIFIED vendor-contract source read *in this session*
- [Nasdaq Private Market — Data License Terms](https://www.nasdaqprivatemarket.com/data-terms/) — the original delete-on-termination signal; *not* the Sharadar governing document
- [python-edgar — quarterly index files](https://github.com/edgarminers/python-edgar)
- [Notre Dame SRAF — SEC/EDGAR master index data](https://sraf.nd.edu/sec-edgar-data/master-index-data/)
- Blocked in this session: `data.nasdaq.com`, `sharadar.com`, `eodhd.com`,
  `kibot.com`, `polygon.io`, `twelvedata.com`, `sec.gov`, `quantrocket.com`
