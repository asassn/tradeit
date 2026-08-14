# Phase 6 — vendor and data-contract verification

**Nothing was purchased. No subscription was taken. No implementation began.**

## 0. Verification conditions — read this first

This session's egress policy **blocks every primary vendor source**. Confirmed
`EGRESS_BLOCKED` on: `data.nasdaq.com`, `help.data.nasdaq.com`, `sharadar.com`,
`quantrocket.com`, `resources.quandl.com`, `sec.gov`, `polygon.io`, `eodhd.com`,
`flounderteam.github.io`. The proxy README states these are organisation policy
denials and are not to be routed around, so they were not.

What *was* reachable: web search (result extracts only) and
`raw.githubusercontent.com`.

**Confidence grades are used strictly, and no secondary-source claim is promoted
to VERIFIED.**

| grade | meaning |
|---|---|
| **VERIFIED** | read directly from a primary or open-source integration source in this session |
| **CORROBORATED** | consistent across ≥2 independent search extracts, primary page not reachable |
| **UNVERIFIED** | single second-hand extract, or not established at all |

Consequently **all pricing in this document is UNVERIFIED**, and the licensing
question — the one that decides the whole plan — is **UNRESOLVED**.

## 1. The licensing question, answered first

> subscribe one month → download → cancel → retain and use indefinitely

### Verdict: **UNCLEAR / REQUIRES VENDOR CONFIRMATION**, with a negative signal.

The only Nasdaq-family data licence text reachable in this session says the
opposite of what the plan needs. From the Nasdaq Private Market **Data License
Terms and Conditions** (a *different* product, but the same licensor family):

> upon expiration or termination, [customers must] immediately cease accessing
> and using the data, and at Nasdaq's election, return or permanently delete the
> data and all copies in their possession … customers may retain copies solely
> to the extent required by applicable law or bona fide internal archival,
> audit, or compliance policies.
>
> — [Nasdaq Private Market, Data License Terms](https://www.nasdaqprivatemarket.com/data-terms/) · CORROBORATED, **not the governing document for Sharadar**

A "cease use and delete on termination" clause of that shape would make the
one-month-and-keep model **prohibited**. Whether the Sharadar-via-Nasdaq-Data-Link
subscriber agreement contains the same clause **could not be established**: the
Data Link help centre article on cancellation and the Data Link terms pages are
both blocked here. Search returned no quotable Data Link clause on
post-termination retention.

**Per your own rule — "Do not recommend purchase if retention rights are
unresolved" — I do not recommend purchase.** See §6 for how to resolve it in
writing before spending anything.

This is not a formality. The entire cost-minimisation strategy in
`PHASE_06_IMPROVEMENT_PLAN.md` §8 rests on perpetual retention of a one-time
download. If retention is prohibited, the architecture is unchanged but the
funding model becomes a recurring subscription, and that should be known before
a card is entered rather than after.

## 2. Vendor matrix

Criteria numbered as in the milestone brief.

| # | criterion | **Sharadar** (Core US Equities Bundle) | **EODHD** | **Polygon / Massive** | **Twelve Data** | **FMP** | **SEC EDGAR** |
|---|---|---|---|---|---|---|---|
| 1 | price history start | "deep history to 1998" — CORROBORATED; one extract says fundamentals to 1990 — UNVERIFIED | US tickers "from January 2000"; "30+ years" for major tickers — CORROBORATED, **internally inconsistent** | not established | "back to the first trading date" — CORROBORATED | in use; not re-verified | n/a (no prices) |
| 2 | active **and** delisted | yes, SF1 + SEP — CORROBORATED | yes, dedicated delisted product — CORROBORATED | **"spotty at best"; a reviewer explicitly advises against Polygon for delisted** — CORROBORATED | not established | not established | n/a |
| 3 | delisted history truly downloadable | claimed via SEP/SF1 — CORROBORATED | claimed: EOD prices, fundamentals, dividends, splits for delisted symbols — CORROBORATED | doubtful | unknown | unknown | n/a |
| 4 | permanent identifier | `permaticker` — UNVERIFIED | not established | not established | not established | not established | **CIK — VERIFIED** |
| 5 | raw / unadjusted prices | **yes** — SEP publishes unadjusted, split-adjusted, and split+dividend+spinoff-adjusted — CORROBORATED | not established | flat files available — CORROBORATED | already used for `full-01` | n/a | n/a |
| 6 | adjusted prices | yes (two methods) — CORROBORATED | yes — CORROBORATED | yes | yes | yes | n/a |
| 7 | splits | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes | yes | 8-K text only |
| 8 | dividends | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes | yes | 8-K text only |
| 9 | M&A / delisting metadata | `ACTIONS` incl. spinoffs, ticker changes — CORROBORATED | delisted product — CORROBORATED | weak | not established | not established | **form types 25, 15, 8-K, S-4 — VERIFIED as *filings*, not as parsed events** |
| 10 | fundamentals | SF1, ~150 indicators — CORROBORATED | Extended Fundamentals plan — CORROBORATED | no | **5 years only — CORROBORATED, disqualifying** | **excluded by project rule** | XBRL 2009+ |
| 11 | **as originally reported** | **`ARQ`/`ARY`/`ART` = As Reported — VERIFIED** | not established | n/a | no | excluded | **yes — "as filed", uncorrected — CORROBORATED** |
| 12 | restated | **`MRQ`/`MRY`/`MRT` = Most Recent Reported — VERIFIED** | not established | n/a | no | excluded | amendments are separate filings |
| 13 | **actual filing date** | **`DATEKEY` is the filing date — VERIFIED** | not established | n/a | no | excluded | **`filed` in full-index 1993+; `filed` in FSDS 2009+ — CORROBORATED** |
| 14 | accession / filing id | not established | not established | n/a | no | excluded | **`adsh` accession, primary key — CORROBORATED** |
| 15 | **genuinely PIT before 2009** | **UNVERIFIED — the single highest-risk assumption. See §4.** | UNVERIFIED | n/a | n/a | n/a | **filing *dates* yes from 1993; machine-readable *values* no before 2009** |
| 16 | API vs bulk | both — UNVERIFIED | both, bulk fundamentals CSV — CORROBORATED | REST + S3 flat files — CORROBORATED | REST | REST | **bulk HTTP, free — CORROBORATED** |
| 17 | rate limits | not established | not established | tier-dependent | tier-dependent | tier-dependent | SEC fair-access guidance |
| 18 | **current price** | **UNVERIFIED** ($69/mo cited from a Jan-2024 source; a competitor advertises against it at $49/mo — neither is a current primary quote) | **UNVERIFIED** (€59.99/mo cited for fundamentals) | **UNVERIFIED** ($29/mo entry cited) | existing subscription | existing subscription | **free** |
| 19 | exact tier required | Core US Equities Bundle (SF1+SEP+TICKERS+ACTIONS) — inferred, UNVERIFIED | "Extended Fundamentals" for bulk — CORROBORATED | n/a | n/a | n/a | none |
| 20 | licensing | **UNRESOLVED — §1** | not established | not established | existing | existing | **public domain, no licence** |

### What the matrix decides on its own

- **Polygon/Massive: ruled out** for this purpose. Delisted coverage is the
  entire problem being solved and is reportedly its weakest area.
- **Twelve Data: ruled out as a historical fundamentals source** (5 years), and
  retained for what it already does well — forward daily prices.
- **FMP: excluded by standing project rule** for fundamentals, ratios, earnings,
  estimates, statements, OHLCV, insider and institutional data. Its permitted
  role is narrow; see the improvement plan §5.
- **EODHD: a genuine second candidate**, not a fallback. It has an explicit
  delisted-companies product and bulk fundamentals. Its weakness is that its own
  marketing gives two different history depths ("from January 2000" vs "30+
  years"), and the point-in-time question — does it carry a real filing date? —
  is entirely unestablished. That single question decides whether it is a
  contender at all.
- **SEC EDGAR: adopt regardless.** Free, authoritative, and the only source that
  can *verify* another vendor's filing dates.

## 3. What was VERIFIED, and from where

Read directly from
[`quantrocket-client/quantrocket/fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py)
— a production integration against Sharadar SF1, authoritative about the
contract it consumes:

- `DATEKEY` **is the filing date**, and is the field to index on for
  point-in-time work.
- The client **shifts `DATEKEY` forward one day to avoid lookahead bias** — an
  independent practitioner reaching the same conclusion this project reached
  from first principles.
- `CALENDARDATE`, `REPORTPERIOD` (fiscal period end) and `LASTUPDATED` are
  distinct fields.
- Dimensions: `ARQ`/`ARY`/`ART` = **As Reported**; `MRQ`/`MRY`/`MRT` = **Most
  Recent Reported**; Q/Y/T = quarterly/annual/trailing-twelve-month.

The as-reported/most-recent split is exactly the raw-versus-derived distinction
the Phase 6 architecture requires, supplied by the vendor rather than
reconstructed. That is the strongest single argument for Sharadar.

## 4. The pre-2009 point-in-time risk

**XBRL does not exist before 2009.** Any vendor's pre-2009 fundamentals were
derived by parsing filing documents. The *values* are one question; the
**`DATEKEY` is the more important one.**

A `DATEKEY` that was reconstructed — set to a period-end, or to a fixed offset
from one — rather than taken from the filing record would make the pre-2009
segment **silently non-point-in-time**. That is worse than not having the data,
because the defect is invisible in aggregate and would quietly contaminate every
dot-com-era result the corpus was built to produce.

**This is testable before purchase and must be tested.** EDGAR's quarterly
full-index runs from **1993 Q1** and carries the filing date for every filing,
free. The probe in `PHASE_06_IMPROVEMENT_PLAN.md` §7 compares vendor `DATEKEY`
against EDGAR's `filed` date, by year, across 1998–2008.

**Acceptance rule, fixed in advance:** a vendor date field whose distribution
clusters on fiscal quarter-ends rather than on plausible filing dates is **not**
point-in-time and must not be treated as such, whatever the vendor calls it.

## 5. Recommendation

**Do not purchase yet.** Two blockers, in order:

1. **Retention rights unresolved (§1).** Your rule, and the right one.
2. **Pre-2009 point-in-time fidelity unverified (§4).** Decides whether
   1998-01-01 is achievable at all.

**Provisional preference, subject to both:** Sharadar Core US Equities Bundle,
with EODHD as a serious alternative to be evaluated on the same probe rather
than dismissed, and SEC EDGAR adopted permanently and immediately as a free
verification and forward-fundamentals source.

Sharadar leads on one specific, verified ground: it is the only candidate where
the as-reported/restated distinction and a filing-date field are *confirmed to
exist in the contract*. EODHD may match it; that is unestablished, not refuted.

## 6. How to resolve §1 before spending anything

Ask the vendor, in writing, and keep the reply. Suggested wording:

> Does our subscription permit us to download the full historical dataset
> during the subscription period, cancel, and then **retain and continue to use
> that downloaded data internally, indefinitely, after cancellation** — with no
> redistribution and no external publication? If yes, which clause of which
> agreement grants it? If our use is internal research and development for a
> non-public software product, which licence tier applies?

Three outcomes:

- **ALLOWED, in writing** → proceed to the probe, then purchase one month.
- **PROHIBITED** → the architecture is unaffected; the funding model becomes a
  recurring subscription, and the choice is between paying it and starting
  `research-01` at 2009 from EDGAR alone. Bring that back for a decision.
- **No clear answer** → treat as prohibited. An unresolved retention right that
  is discovered later, after the data is embedded in a research corpus, is a
  much worse problem than one discovered now.

## Sources

- [quantrocket-client `fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py) — **read directly; the only VERIFIED vendor-contract source in this report**
- [Nasdaq Private Market — Data License Terms](https://www.nasdaqprivatemarket.com/data-terms/) — the delete-on-termination signal; *not* the Sharadar governing document
- [Nasdaq Data Link help — cancelling premium subscriptions](https://help.data.nasdaq.com/article/473-can-i-cancel-my-premium-data-subscriptions-at-any-time-how-do-i-cancel-my-subscription) — **blocked**
- [Sharadar Core US Equities Bundle](https://data.nasdaq.com/databases/SFA) — **blocked**
- [Sharadar — Fundamentals documentation](https://sharadar.com/docs/fundamentals) — **blocked**
- [Sharadar — Subscribe](https://sharadar.com/subscribe) — **blocked; the authoritative pricing page**
- [EODHD — Delisted stock companies data](https://eodhd.com/financial-apis/delisted-stock-companies-data-2) — **blocked**
- [EODHD — Bulk fundamentals API](https://eodhd.com/financial-apis/bulk-stock-fundamentals-api) — **blocked**
- [Polygon — Flat Files](https://polygon.io/flat-files) — **blocked**
- [Twelve Data — Fundamentals](https://twelvedata.com/fundamentals) — **blocked**
- [SEC — Financial Statement Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets) — **blocked**
- [python-edgar — quarterly index files since 1993](https://github.com/edgarminers/python-edgar)
- [Notre Dame SRAF — SEC/EDGAR master index data](https://sraf.nd.edu/sec-edgar-data/master-index-data/)
