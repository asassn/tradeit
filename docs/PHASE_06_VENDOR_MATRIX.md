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
| **USER-VERIFIED** | read from the primary source **by the project owner, outside this session**, and reported here. Authoritative for published prices and page text; still subject to the data probe for capability claims |
| **CORROBORATED** | consistent across ≥2 independent search extracts, primary page not reachable |
| **UNVERIFIED** | single second-hand extract, or not established at all |

Consequently **all pricing in this document except where marked USER-VERIFIED is
UNVERIFIED**.

---

## 1. Vendor eliminations — price and capability only

> **Licence retention and deletion terms are out of scope for vendor selection.**
> What a vendor requires when a subscription ends is the operator's decision to
> manage, at the operator's discretion. It is not a selection criterion, it does
> not disqualify a vendor here, and no part of this system is designed around it.
> Vendors are judged on **coverage, data quality and price**.
>
> Sharadar and EODHD were previously eliminated on licence grounds alone, and
> that elimination is withdrawn. **Both return as candidates on their merits**,
> neither having ever failed a capability test — EODHD's capabilities were never
> the issue, and Sharadar was never probed at all.

### 1.1 Kibot — **ELIMINATED ON PRICE**

**The ~$14 figure this plan was built on was wrong.** *USER-VERIFIED from the
vendor's own pricing page.* Two products were conflated:

- **$14/month is the EOD *subscription*** — it refreshes data already purchased,
  at a quarterly cadence. It is not the price of the archive.
- **The archive is a separate one-time purchase**, priced per universe per
  interval. For the "All Stocks" universe (18,000+ tickers) the observed
  one-time prices run from **$600** (30-minute) through **$3,000** (1-minute) to
  **$9,000** (tick with bid/ask). Cross-universe bundles were **$990**, **$2,400**
  and **$4,800**.

So the corpus this plan assumed cost ~$14 costs **$990–$2,400** — between 70 and
170 times the assumption. Every downstream statement that treated a single ~$14
month as sufficient was wrong, and is corrected rather than quietly dropped.

**One question was never answered and would still gate any purchase.** The
package is described as *"every tradable US common stock"*. Whether *tradable*
means currently tradable — excluding the delisted names that are the entire
problem — is not resolvable from the pricing page, and is Kibot question 1
verbatim. No purchase should proceed without it, at any price.

`docs/KIBOT_DATA_PROBE.md` is retained in full. Its acceptance rules were written
before any data was seen and remain the standard any replacement vendor is held
to; only the vendor it names is out, and only on price.

### 1.2 The candidate field as it now stands

| vendor | status | on what ground |
|---|---|---|
| **Kibot** | eliminated | price — $990–$2,400 for the archive, out of budget |
| **Sharadar** | **candidate, unprobed** | never capability-tested; its earlier elimination was licence-based and is withdrawn |
| **EODHD** | **candidate, unprobed** | capabilities were never in question |
| **Twelve Data** | in use for operating prices; `full-01` was built from it | never capability-probed for archive use |
| **FMP** | in use for corporate actions and symbol reference | never capability-probed for archive use |
| **Tiingo** | working acquisition adapter | never capability-probed for archive use |

**Nothing here has been probed against the acceptance rules.** Every row above
is a statement about what has and has not been *tested*, and the item most likely
to fail for any of them is survivorship completeness (`KIBOT_DATA_PROBE.md` §G),
which can fail while everything else passes.

---

## 2. Vendor matrix

Split into two tables, because the **price/lifecycle spine** and the
**fundamentals spine** are different problems with different candidate sets.
**Every judgement here is coverage, quality or price.** Licence retention terms
are not a criterion and no row records them.

### 2a. Price and security-lifecycle history

| # | criterion | **Kibot** | **Sharadar** | **EODHD** | **Polygon / Massive** | **Twelve Data** |
|---|---|---|---|---|---|---|
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
for `intraday-01`** and should be evaluated on item H alongside Kibot.

### 2b. Fundamentals and filing metadata

| # | criterion | **SEC EDGAR** | **Sharadar** | **EODHD** | **Twelve Data** | **FMP** |
|---|---|---|---|---|---|---|
| 10 | fundamentals | **XBRL values 2009+ — CORROBORATED** | SF1, ~150 indicators — CORROBORATED | Extended Fundamentals — CORROBORATED | **5 years only — CORROBORATED, disqualifying** | **excluded by project rule** |
| 11 | as originally reported | **yes — "as filed", uncorrected — CORROBORATED** | `ARQ`/`ARY`/`ART` — VERIFIED | not established | no | excluded |
| 12 | restated | amendments are separate filings | `MRQ`/`MRY`/`MRT` — VERIFIED | not established | no | excluded |
| 13 | **actual filing date** | **`filed` in full-index 1994 Q3+; `filed` in FSDS 2009+ — VERIFIED** | `DATEKEY` — VERIFIED | not established | no | excluded |
| 14 | accession / filing id | **`adsh`, primary key — CORROBORATED** | not established | not established | no | excluded |
| 15 | genuinely PIT before 2009 | **filing *dates* yes from 1994 Q3; machine-readable *values* no before 2009** | **UNTESTED** — see §4 | **UNTESTED** — see §4 | n/a | n/a |
| 4 | permanent identifier | **CIK — VERIFIED** (but CIK↔ticker for 1998–2008 is the hard part; see improvement plan §5) | — | — | — | — |

### What the matrix now decides on its own

- **Sharadar and EODHD: candidates, unprobed.** Their earlier elimination was
  licence-based and is withdrawn; neither has ever been capability-tested for the
  archive role, and Sharadar's `permaticker` and as-reported/restated split are
  the strongest claimed feature set in the fundamentals table.
- **Polygon/Massive: still ruled out** for EOD on delisted coverage — the entire
  problem being solved — and **still a candidate for `intraday-01`**, where bulk
  flat-file delivery is its strongest feature.
- **Twelve Data: retained for forward daily prices**, ruled out for historical
  fundamentals (5 years of history, which is disqualifying on its own).
- **FMP: excluded by standing project rule** for fundamentals, ratios, earnings,
  estimates, statements, OHLCV, insider and institutional data. Permitted role is
  corporate-action and symbol-change corroboration only.
- **SEC EDGAR: adopt regardless, immediately.** Free, public domain, and the only
  source that can independently *verify* or *enumerate* what a price vendor
  claims.
- **Kibot: eliminated on price**, and entirely unverified as data. See §3.

**No vendor in either table has been probed.** The field is wider than it was —
three candidates rather than one — and none of them has been measured.

---

## 3. Kibot — what is claimed, and what must be proven

Everything in this section is a **vendor claim relayed via USER-VERIFIED reading
of Kibot's own pages**, not a measurement. The distinction matters more here than
anywhere else in this document, because the claim being made — a complete
survivorship-safe US equity history — is one no other vendor in the matrix makes
as directly.

| claimed | status |
|---|---|
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

Sharadar and EODHD are the two candidates that could supply pre-2009
fundamental *values*, and **neither has been tested.** The test is stated in
advance, before any data is seen, so it cannot be softened after the fact:

**Acceptance rule, fixed in advance:** a vendor date field whose distribution
clusters on fiscal quarter-ends rather than on plausible filing dates is **not**
point-in-time, whatever the vendor calls it. Compare against EDGAR's `filed` for
the same accession, by year, across 1998–2008.

Meanwhile, EDGAR supplies the *dates* authoritatively from **1994 Q3** for free,
which is what makes deferring pre-2009 *values* survivable — see
`RESEARCH_01_DATA_CONTRACT.md` §7.

---

## 5. Recommendation

1. **Do not purchase anything yet.** No vendor in §2 has been probed, and the
   acceptance rules in `KIBOT_DATA_PROBE.md` were written before any data was
   seen precisely so that a purchase decision could be measured rather than
   argued.
2. **Adopt SEC EDGAR now.** Free, public domain, and — via Form 25/15
   enumeration — the instrument that *measures* whether any price vendor's
   delisted roster is complete. This work is useful under every outcome and
   depends on no purchase.
3. **When a probe does run, run it on Sharadar first.** It has the strongest
   claimed feature set for the fundamentals spine — `permaticker`, the
   as-reported/restated split, `DATEKEY` — and it has never been tested at all.
   EODHD second, on the same basis.
4. **Kibot stays eliminated on price** unless the budget changes, in which case
   it is reconsidered unchanged — its question 1 (does *tradable* include
   delisted?) still gates any purchase at any price.
5. **Defer pre-2009 fundamental *values*.** EDGAR supplies the filing *dates*
   authoritatively from 1994 Q3 for free, which is what makes deferring the
   values survivable (`RESEARCH_01_DATA_CONTRACT.md` §7).

---

## Sources

- **Kibot pricing and EOD subscription pages** — one-time archive prices
  ($600–$9,000 by universe and interval; $990/$2,400/$4,800 bundles), the
  ~$14/month subscription, 64-year daily history, adjustment bases, delisted
  rosters — **USER-VERIFIED, primary source, blocked in this session**
  (`kibot.com` returned `EGRESS_BLOCKED` here)
- [quantrocket-client `fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py) — read directly; the only VERIFIED vendor-contract source read *in this session*
- [python-edgar — quarterly index files](https://github.com/edgarminers/python-edgar)
- [Notre Dame SRAF — SEC/EDGAR master index data](https://sraf.nd.edu/sec-edgar-data/master-index-data/)
