# `research-01` — data contract

The survivorship-aware historical research corpus. **The only corpus a
cross-sectional or economic statistic may ever cite.** `full-01` is frozen and
remains machinery-validation only (`docs/CORPUS_REGISTRY.md`).

**Not implemented.** This is the contract for approval.

> **`research-01` is the EOD corpus.** It is daily-based and serves the **Swing**
> and **Retirement** mandates, plus the context and setup layers of everything
> else. It causally derives Weekly and Monthly and nothing below Daily.
>
> A second corpus, **`intraday-01`** — 1-minute base, a shorter recent window,
> expected to be survivorship-biased — is specified separately in
> [`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md) §6.3 and probed
> separately (`KIBOT_DATA_PROBE.md` §H). **Nothing in this contract waits for
> it**, and the two are never merged: they have different base timeframes,
> different depths, different vendors and different survivorship postures, and
> mixing their observations into one statistical population is exactly the error
> the mandate separation exists to prevent.

---

## 1. Scope

| | |
|---|---|
| preferred start | **1998-01-01** — held. Deliberately chosen so future research spans the late dot-com build-up, the 2000–2002 collapse, the financial crisis, COVID, the 2022 tightening cycle, and the modern AI/speculative period |
| end | rolling; the corpus is maintained forward, not re-purchased |
| universe | US-listed equities, **active and delisted**, no survivorship filter of any kind |
| timeframe | daily bars; higher timeframes derived |
| price spine | 1998-01-01 → present, **subject to the Kibot probe** (`KIBOT_DATA_PROBE.md`) |
| filing spine | 1994 Q3 → present, SEC EDGAR, free |
| fundamental **values** | 2009 → present, SEC XBRL/FSDS, free. **1998–2008 deferred** — see §7 |

The start date is no longer conditional on a fundamentals vendor. §7 explains why
that separation is legitimate rather than a quiet retreat.

## 2. The one rule everything else serves

> **A ticker is a label, not an identity.**

`full-01` already proved why: Bed Bath & Beyond's survivorship control passed on
a *different company's* prices, because the ticker had been reused after
delisting and the series was spliced. That was caught by
`TICKER_REUSE_TOLERANCE_DAYS` only because someone thought to look for it. In
`research-01` it must be impossible by construction.

## 3. Security master

One row per **security**, never per ticker.

```
securities
  instrument_id          surrogate PK, permanent, platform-owned
  vendor_permanent_id    the vendor's own permanent id, if any
  cik                    SEC Central Index Key, nullable (non-filers, some funds)
  name
  asset_class
  primary_exchange
  country, currency
  listing_date
  delisting_date         NULL while listed
  delisting_reason       enum: acquired | merged | bankrupt_liquidated |
                         bankrupt_reorganised | taken_private | exchange_rule |
                         voluntary | reverse_merger | unknown
  first_seen_source      which feed first told us this security exists
  knowledge_time         when the platform first knew
```

**`delisting_reason = unknown` is a first-class value, not a failure.** Absence
of a reason is recorded as absence, never inferred.

### Ticker aliases — separate table, time-bounded

```
symbol_aliases
  instrument_id -> securities
  ticker
  exchange
  valid_from
  valid_to              NULL = still current
  change_reason         enum: ipo | rename | merger | reverse_split |
                        exchange_move | reuse_by_other_issuer | unknown
  source, knowledge_time
  EXCLUDE overlapping (ticker, exchange, [valid_from, valid_to)) per instrument
```

The exclusion constraint is the teeth: **the same ticker on the same exchange
cannot map to two securities at the same instant.** Ticker reuse then resolves
correctly by construction — `ticker + as_of` yields exactly one `instrument_id`,
or none.

`valid_to` is where an adjudicated splice boundary is recorded, and it is
**inclusive**: the date named is the last session that still belongs to this
security. A filing binds a symbol to a registrant and says nothing about when
that binding ended, so every alias seeded from filing text opens unbounded; a
close is written only where evidence located one, and the boundary's provenance
is *appended* to the citation rather than replacing it. The original clause is
the filing sentence that made the binding and is still true — overwriting it
would destroy the provenance of the binding in order to record the provenance of
its end.

### Corporate lineage — separate again

```
security_relationships
  from_instrument_id, to_instrument_id
  relationship          enum: acquired_by | merged_into | spun_off_from |
                        renamed_to | reverse_merged_into | successor_of
  effective_date
  ratio                 exchange ratio where applicable
  source, knowledge_time
```

Lineage is **recorded, never auto-followed**. A backtest that wants to treat an
acquired company's history as continuing into its acquirer must say so
explicitly; the default is that the security ended.

## 4. Prices

### Measured coverage of the dead, 2026-09-04

The survivorship gate has been run against the corpus after the dead-registrant
backfill. **The grade is unchanged and the numbers moved by a factor of five.**

| | before | after |
|---|---|---|
| CIKs with at least one price bar | 862 | **4,017** |
| dated confirmed exits we can price | 791 | **3,950** |
| `matched_coverage` | 2.71% | **13.54%** |
| `bounded_coverage` | 0.87% | **4.36%** |
| classification | `SURVIVOR_BIASED` | **`SURVIVOR_BIASED`** |

`bounded_coverage 0.044 below 0.25` is the gate's own reason. Controls remain
30/30, which does not rescue the grade and was never meant to.

**Coverage is concentrated where the cohort was built, and the shape says so:**

| exit era | priced | of | |
|---|---|---|---|
| 1994–1997 | 0 | 1,333 | no XBRL, no cohort |
| 1998–2005 | 796 | 9,113 | the dot-com cohort, 8.7% |
| 2006–2008 | 1 | 3,648 | **a hole: after the dot-com work, before XBRL** |
| 2009–2026 | 3,153 | 15,086 | **20.9%** |
| 2024 · 2025 | 251 · 281 | 701 · 664 | **35.8% · 42.3%** |

The 2006–2008 window is the honest weak point: it belongs to neither cohort.
The recent years are the strongest the corpus has ever been.

**The standing rule does not lift.** No strategy result computed on this corpus
is evidence of profitability, and it stays that way until the gate returns
something other than `SURVIVOR_BIASED` — which needs `bounded_coverage` above
0.25, roughly five times again what is held now.

**16,348,733 bars landed from 3,534 of 3,728 symbols**, with 5,126 bars refused
as unresolvable and 194 symbols failing outright. Every request was bounded at
both ends of the registrant's evidenced life.


**Store the vendor's raw facts. Derive everything else.**

```
price_facts                      immutable, append-only
  instrument_id, session_date, timeframe
  open, high, low, close, volume     as delivered
  adjustment_basis      enum: raw_unadjusted | split_adjusted |
                        split_and_dividend_adjusted     -- what the VENDOR sent
  vendor_adjusted_close nullable, only if the vendor supplied one
  vendor_split_factor, vendor_dividend   nullable, as delivered
  source, source_version, ingested_at, knowledge_time
  UNIQUE (instrument_id, session_date, timeframe, source, source_version)
```

Rules:

1. **Prefer `raw_unadjusted`.** The existing scale-invariance work exists
   precisely because adjusted-only series are lossy. Where a vendor offers
   several bases — Kibot claims unadjusted, split-adjusted and fully-adjusted —
   ingest **all** of them as separate vendor facts and derive our own adjusted
   series from the raw one. The extra bases are kept because the *relationship
   between them recovers the corporate actions* (`KIBOT_DATA_PROBE.md` §E), not
   because we read from them.
2. **`adjustment_basis` is what the vendor said it sent, not what we wish it
   was.** The existing `AdjustmentPolicyDeclaration` already refuses to guess.
3. **Never splice.** A bar is attached to an `instrument_id`, resolved through
   `symbol_aliases` at that bar's date. A reused ticker produces two securities
   and two series, and there is no code path that concatenates them.
4. Adjusted series are **derived** (§6), carry a `derivation_version`, and are
   recomputed rather than patched.

### The reconstruction, built — and an anomaly it exposed

The dependency named below is now closed. ``research01/series.py`` derives a
split-adjusted series from raw bars plus the actions **known at the as-of
instant**, which is the only valid route to an adjusted price for a past date.

The factor for a bar is the product of every split whose ex-date is after that
bar **and** whose ``knowledge_time`` is at or before the as-of. Both conditions,
always — dropping the second is how a backtest quietly learns tomorrow's
corporate actions. Volume moves opposite to price, because a split multiplies
the share count and adjusting one without the other breaks every turnover
measure. A bar **on** the ex-date is not adjusted: the split is already in that
day's print.

Only ``raw`` bars are read. Feeding a vendor ``total`` bar through this would
adjust an already-adjusted number twice, with the vendor's delivery epoch still
inside it.

#### 9.2% of series outlive their registrant by more than seven years

Testing the reconstruction on real data surfaced something the corpus had not
been asked before. Comparing where each series ends to where its registrant
stopped filing with the SEC:

| gap | securities |
|---|---|
| ends within 1 year of the last filing | **770 — 89.3%** |
| 1–7 years after | 13 — 1.5% |
| **more than 7 years after** | **79 — 9.2%** |

**Bimodal, not a tail.** The worst run 22–25 years past their registrant's final
filing: `Advanced Switching Communications` last filed in 2001 and has prices to
2020, with six compounding reverse splits along the way.

A delisted company can trade over the counter for a while without filing, so a
short overrun is a question. A quarter of a century is not — it is far more
likely to be **a second company that took the ticker**. The vendor marks a
reused ticker `_old`, but confirmation binds the *plain* symbol to whichever
registrant the filing named, and the plain symbol's history then runs on into
the next holder's.

``series_coherence()`` reports this and **does not filter**. Truncating would
discard legitimate post-delisting trading; dropping would hide a splice rather
than name it. The verdict is returned and the caller decides — with
``UNKNOWN`` for a security whose filing span is not known, because absence of
the comparison is not evidence that it would have passed.

**This is a third distinct splice shape**, after the vendor's own ticker reuse
and the storage bug that filed one company's prices under another. It affects
roughly one security in eleven and was invisible until something tried to *read*
the corpus rather than write to it.

#### The adjudication: 58 of the 79 given a dated boundary

`research01/adjudicate.py` is the next step and a **different act**. Coherence
reports a shape; adjudication asks what the evidence establishes about each
series, and where a boundary can be located it writes that boundary into
`symbol_aliases.valid_to` with a citation. **Filtering on a cited, recorded
interval is not filtering on a heuristic**, and that distinction is why these
are two modules rather than one.

The evidence, strongest first:

| evidence | what it establishes | dates a boundary? |
|---|---|---|
| **no overlap** — no bar predates the registrant's exit era | the whole series is somebody else's | n/a: nothing survives |
| **dormancy** — no vendor row at all for ≥ 180 days, then a substantial run | two occupants, and *where* they divide | **yes** |
| **registry reuse** — `company_tickers.json` gives the plain ticker to a different CIK today | the symbol was re-issued | no |
| **filed exit** — a confirmed dated exit in the EDGAR denominator | why the vendor lost the series | no, corroborates only |
| **regime break** — price level *and* traded volume both break by ≥50× at one date, no split behind it | the two sides are not the same tradable thing | **yes** |

Dormancy is stronger than it looks: the vendor emits **zero-volume rows** for
sessions in which a security did not trade — 19.7% of the corpus is such rows —
so an absent row means an absent *security*, not a quiet one. The 180-day
threshold is set far above any market closure: the longest in the modern US
market is four sessions, which appears in this corpus as the seven-day hole
after September 11th 2001 and **must never be read as a boundary**.

Two rules exist because a first version got them wrong:

* **The anchor is the *later* of the last filing and the filed exit, never the
  earlier.** A registrant that filed until 2005 demonstrably existed until 2005
  whatever a Form 25 from 2003 says, and anchoring on the Form 25 would licence
  cutting away trading the company really did.
* **Registry reuse is meaningless unless the series overruns.** Asking the
  registry question of every series returned 36 securities as contaminated, and
  **not one was among the 79** — every one was a vendor `_OLD` symbol, for which
  "somebody else holds the plain ticker today" is true *by construction*. The
  vendor had already separated them correctly. An answer arrived, and it was not
  an answer to the question asked.

#### The regime break, for series with no hole to find

Twenty-one of the 79 had **no dormancy at all**: an unbroken daily series
running 11–25 years past the registrant. Three explanations were tested against
the data and two were refuted.

**Refuted — a flatlined quote.** If the vendor were padding a dead symbol with
stale rows, the tail would be zero-volume. Measured: 30 of the 35 residual
series trade with real volume right up to their final bar. They are not
carcasses.

**Refuted as a discriminator — the exit type.** A registration termination or an
exchange delisting might have been expected to separate "went dark and kept
trading" from "ceased to exist". It does not, and `lifecycle.py` says why in its
own words: *a delisted security can keep trading and a deregistered issuer can
keep existing*. Even the derived `CONFIRMED_SECURITY_EXTINGUISHED` is delisting
**and** deregistration, neither of which stops a Grey Market quote. The exit
type is consistent with an overrun in every case, so it discriminates nothing.

**What does work.** At the true boundary the **price level and the traded volume
both change by a large factor** with no split behind it. `BWN` runs at $0.05
with no volume and becomes $8.93 on 130,000 shares a day; `ZIPL` goes from
$2.33 on 25,000 shares to half a cent on none. Taking the **smaller** of the two
ratios is the whole design: a penny stock triples routinely and a thin quote's
volume goes from nothing to something all the time, so either alone establishes
nothing.

**The threshold of 50× comes from a null test, not from taste.** Run inside each
registrant's *own lifetime* — where one company is present by construction —
the detector scores at most 49.6 across all 768 coherent series: 9.6% reach 3,
1.8% reach 5, one reaches 25, and **none reaches 50**. Nought out of 768 bounds
the false-positive rate below roughly 0.4%; it does not establish zero.

Two details that took a wrong answer to find. A median over a window straddling
a step keeps returning the majority side, so **every** candidate within half a
window either side scores identically — windows detect, and the adjacent bar's
own discontinuity locates. And the detector must be given the sessions with
positive closes while **dormancy is given every session the vendor emitted**:
filtering zero closes out of the dormancy input manufactured a hole, and with it
a boundary, in one series.

The verdict is deliberately **not** called a splice. The cause may be a ticker
changing hands; it may equally be the vendor stitching two sources or
re-denominating a quote. What the evidence supports is that the two sides are
not the same tradable thing — which is what the cut needs — and not a claim
about which company each side is.

Measured over all 862 priced securities:

| verdict | securities |
|---|---|
| `coherent` — ends where the registrant did | 768 |
| `splice_located` — dormancy, then a second company's run | 46 |
| `tail_artefact` — dormancy, then a handful of stale prints | 7 |
| `wholly_misattributed` — no bar in the registrant's lifetime at all | 6 |
| `regime_break` — level and liquidity both break, no split | 5 |
| `contaminated_boundary_unknown` — known wrong, **not repairable** | 1 |
| `unresolved` — overruns, and no rule explains it | 29 |

**63 of the 79 received a dated boundary**, placing **91,231 bars — 11.0% of the
corpus — outside their security's interval.** The corpus's suspect count falls
from 79 to 28.

`ZIPL` is the worked example: 390 bars kept, running $12.38 in May 1999 to one
cent in August 2001 — a dot-com dying exactly as one should — and **4,370 bars
dropped**, belonging to whoever held the symbol through 2025.

**Nothing was deleted.** The bars remain and the interval says which of them
belong; discarding them would destroy the record of a defect that took three
attempts to find. `price_series()` and `known_splits()` exclude them by default,
and `include_disputed=True` returns them for an auditor checking the cut.

**Bounding the bars alone was a half-fix.** Corporate actions were fetched under
the same symbol as the prices, so a series holding two companies holds two
companies' splits. With only the bars cut, `ASCX`'s $18.00 close in 2000 still
read as **three trillion dollars** — the exact absurdity that set the
investigation off, surviving the fix meant to end it. Actions are bounded by the
same interval, and the close now reads $180.00 against a single 2001 reverse
split that is genuinely the registrant's.

**A located boundary is not a clean bill of health**, and 28 series still end
more than seven years past their registrant: 16 that no rule could explain, and
12 whose located boundary was itself late — `BWN`'s cut in 2016 removes another
company's run and leaves fifteen years of unexplained quiet quote behind it.

`coherent` says only that a series' *ending* does not betray a splice — one
wholly inside the registrant's own lifetime would leave no trace in the shape at
all.

#### The EDGAR successor search: 0 of 30, and what that is worth

`research01_successor.py` asks EDGAR who else claimed each unbounded ticker.
Full-text search proposes; `confirm_ticker` — the same function that established
these identities — disposes, with the same refusals and **no laxer variant to
raise the hit rate**. A false negative costs coverage; a false positive would cut
away a registrant's real trading on a coincidence.

**A zero from an unvalidated pipeline is worthless, so the pipeline validates
itself first.** This repository has already had one "confirmed 0 of 40" that was
a bug in the extractor rather than a fact about the filings. Before any search
runs, a self-test fetches Apple's most recent 10-K and requires it to bind
`AAPL` to CIK 320193 through the same fetch, strip, extract and confirm path. If
that fails, nothing runs.

**A bare-token search cannot answer for a common token, and the first run did
not notice.** `AWS` returns over ten thousand filings — Amazon Web Services —
and the hundred that relevance ranks first are a needle in a haystack. The
phrase `"symbol AWS"` returns **zero**, which is an answer. So the search climbs
a ladder of narrowing phrases and uses the first whose *entire* result set fits
inside 300 hits, unioning in the bare token whenever it too can be exhausted:

| rung | when |
|---|---|
| `"symbol X"`, all forms | ≤ 300 hits |
| `"under the symbol X"`, all forms | if the above is still too broad |
| `"symbol X"`, 10-K family | last resort — `TSX` needs it, being how every Canadian filer writes the Toronto exchange |
| **+ bare token** | whenever *it* is exhaustible; it is the rung that catches a Section 12(b) table |

Restricting to the 10-K family was itself a mistake, found by reading the
counters: Item 5 is where an *established* registrant states its symbol, but a
successor that has just taken a ticker says so first in a registration statement
or an 8-K and may never file a 10-K. `"symbol BIR"` returns one 10-K and
**thirty-three filings overall**.

**Result: 0 of 30. 633 filings read and parsed, 822 unable to bind, and every
query rung exhausted** — no negative here rests on a truncated result set.

#### Two false positives, and why this route may not write

Both findings this search has ever produced were wrong, and both were the same
construct:

> "listed for trading on the Toronto Stock Exchange under the symbol **TSX: XPL**"
> — Xplore Technologies, 0001104659-07-061307
>
> "under the symbol **TSX-V: GGC**" — Silvermex Resources, 0001062993-11-001823

The symbol is the half *after* the colon; `TSX` is the exchange. `_BOUND_SYMBOL`
read the venue as the ticker, and a cut of seventeen years was one step away.
**The fix for the first let the second straight through** — the capture stops at
the word boundary before the hyphen — and was only caught because it was checked
rather than assumed. The lookahead now skips a bounded run of venue characters
before demanding the colon, and is narrowed by `_STOPWORDS` so a colon
introducing prose still binds. The qualified form is **refused, not parsed**:
capturing `XPL` would be more useful and is a wider change than a defect of this
shape warrants.

The corpus was checked for the same contamination: **zero** of its 859 ticker
aliases were bound through the venue half, and only one alias equals an exchange
abbreviation at all.

**This route therefore proposes and never applies.** The structural rules write
boundaries themselves because each carries a null test — the regime-break
detector fires on none of 768 single-company lifetimes, so its false-positive
rate is measured. This one has no such number and an observed rate of two out of
two. Findings are written to JSON with their citations for a person to read,
which is the rule `acquire.py` already enforces structurally: a program that
reads a filing has not satisfied the requirement that a *person* read it.

#### What the zero does and does not license

It does **not** clear these 16 series. It says no SEC filing since each
registrant went quiet binds its ticker to anyone else, within a search whose
every hit was examined — and **EDGAR full-text search begins in 2001**, so a
handover completed before then is invisible to it. `NOT ESTABLISHED` here means
*this search could not show it*, never *it did not happen*.

What it does do is shift the leading explanation. With no successor found and no
dormancy and no regime break, the remaining hypothesis for most of these is not
a splice at all but **an issuer that deregistered and whose shares went on
trading** — which `lifecycle.py` says in its own words is exactly what a
registration termination permits. That is a hypothesis and is recorded as one:
the series stay flagged, uncut, and excluded from nothing.

### Point-in-time for a backfill, and the dependency it creates

**A vendor file delivered today containing a 1999 bar is not a bar we recorded in
1999.** Two columns already separate the two questions and neither may stand in
for the other:

| field | answers | a 1999 raw bar delivered in 2026 |
|---|---|---|
| `knowledge_time` | when could a diligent observer first have *used* it? | 1999 session close |
| `ingested_at` | when did our pipeline write the row? | 2026 |

A 1999 `knowledge_time` is not a claim that we existed in 1999. It is a claim
that the *print* was public then, which is true and checkable.

**That reasoning fails for an adjusted series, and this is the consequential
part.** A split-adjusted 1999 close computed by a vendor today embeds every
corporate action between 1999 and today; its value depends on the future.

The rule is **not** that adjusted prices are never historically knowable — a
series adjusted as of 2005, using only splits public by 2005, is point-in-time
valid. What is invalid is a **vendor-delivered** adjusted series, because its
adjustment epoch is the delivery date and nothing earlier. Such rows therefore
take `knowledge_time` = delivery and are invisible to any earlier as-of.

> **Consequence, named now rather than discovered in milestone 4.**
> **Point-in-time corporate actions are a hard dependency for any historical
> backtest on this corpus.** Deriving our own adjusted series — from raw bars
> plus the actions known at the as-of instant — is the *only* route to an
> adjusted series valid at that instant, because every vendor-delivered one is
> stamped at delivery. `security_corporate_action_facts` is not an optional
> enrichment; it is on the critical path, and a backtest that needs adjusted
> prices cannot run until it is populated.

`security_price_facts.knowledge_time_basis` records which of the four routes
produced a row's timestamp: `source_disseminated`, `session_close`,
`computed_at_delivery`, or `delivery_unestablished`. **It is not derivable from
`adjustment_basis`** — a *raw* bar whose session close cannot be established
also falls back to delivery time, and is then indistinguishable from an ordinary
raw bar without this column.

## 5. Corporate actions

```
corporate_action_facts           immutable, append-only
  instrument_id
  action_type    split | reverse_split | dividend_cash | dividend_stock |
                 spinoff | merger | acquisition | ticker_change |
                 delisting | rights | unknown
  ex_date, record_date, pay_date, effective_date    nullable as applicable
  ratio, cash_amount, currency
  counterparty_instrument_id     nullable — the other side of a merger/spinoff
  source, source_version, knowledge_time
```

Two vendors disagreeing about a split ratio produce **two rows**, not a
resolution. Disagreement is data; picking a winner silently is not.

## 6. Fundamentals — the three-date rule

### What is actually loaded, measured

`security_fundamental_facts` held **zero rows** until 2026-09-02. It now holds
**92,022,159 as-reported facts** over **17,000 securities** and **482,780
distinct metrics**, from all 70 quarterly Financial Statement Data Set
archives. Zero rows violate `knowledge_time >= event_time`; zero duplicate
revisions exist. The database is 45.4 GB.

**The cohort was the whole problem, and the fix was the cohort.** The first
import reached **fifteen** securities — Apple, Microsoft, Amazon, Cisco, GM —
because `research-01`'s identity was a dot-com cohort that stopped filing
between 1998 and 2005 while the Data Sets begin in 2009. *A fundamentals set
consisting entirely of survivors, attached to a corpus built to avoid
survivorship bias, is worse than none.* So 17,015 registrants were seeded from
the SEC's own submission index:

| | |
|---|---|
| CIKs with fundamentals | **17,000** |
| — with a confirmed dated EDGAR exit | **7,253 (42.7%)** |
| — that exit falling 2009 or later | 7,251 |

**Forty-three per cent dead**, against 2.71% coverage of dated exits on the
price side. This is the first part of the corpus that is survivorship-*measurable*
rather than survivorship-*biased* — and it is fundamentals only. Holding a
fundamental fact for a registrant is **not** holding a price for it: no ticker
was evidenced for the seeded cohort, so the survivorship gate, which counts CIKs
with a price bar, does not move on this at all.

The honest ceiling: only **48.1%** of the 15,086 confirmed dated exits from 2009
onward filed XBRL. This route cannot reach the other half.

### The free fundamentals do not begin in 2009

Measured across all seventy archives. `2009q1.zip` holds a header row and
nothing else — 223 bytes, zero submissions — and the XBRL mandate phased in by
filer size after it:

| quarter | filings | | period | facts loaded |
|---|---|---|---|---|
| 2009q2 | 22 | | 2008 | 313,475 |
| 2009q3 | 435 | | 2009 | 1,092,581 |
| 2010q3 | 1,412 | | 2010 | 3,302,599 |
| **2011q3** | **7,102** | | 2011 | 5,967,278 |
| 2026q2 | 7,714 | | 2012 | 6,655,444 |

**Anything cross-sectional before 2011Q3 is a sample of large accelerated
filers, not of the market**, and a screen run on it would be measuring company
size. The number a reader reaches for is "2009", and it is wrong by two and a
half years.

### Three refusals the import makes, each found by running it

**A fact cannot be knowable before the event it describes.** The first real run
died on `ck_security_fundamental_knowledge`:
`CommonStockDividendsPerShareDeclared` for the quarter ending 2010-12-31, in a
filing submitted 2010-11-03. For a *declared* dividend that date is genuine; on
a reported result the same shape is look-ahead. `num.txt` does not say which,
and inventing a tag taxonomy to guess would be fabrication, so such rows are
skipped and counted under `KNOWLEDGE_PRECEDES_EVENT`. The boundary is tested:
filed *on* the period end is kept, because the rule is `<`, not `<=`.

**Only consolidated rows.** A row with `segments` or `coreg` populated is a
dimensional breakdown — revenue by geography, by business unit — and is skipped
and counted, never summed into the consolidated figure of the same name.

**Re-running adds nothing.** `RejectReason.DUPLICATE` always promised this and
for this importer it was false: the dedupe set lived for one call while
`uq_security_fundamental_revision` lives in the database, so clearing a progress
file re-inserted a quarter and died. Writes are now batched
`INSERT … ON CONFLICT DO NOTHING`, sized in **bound parameters rather than rows**
— fourteen columns times five thousand rows is seventy thousand parameters and
SQLite refuses above 32,766.

### A known source defect, recorded rather than cleaned

**571 rows carry a `period_end` outside 1990–2030**, including year 1011 and
1932. They are what the SEC's own file says and are kept, because discarding
them would hide a source defect rather than record it; every one links to the
filing it came from. **A consumer must bound its own period range** — 571 in
92,022,159 is 0.0006%, and one of them in an unbounded screen is still wrong.

### The index that made it usable

`ix_security_fundamental_pit` leads with `security_id` and serves "this
company's history of this metric". **A screen makes the opposite read** — this
metric, for every company, for periods in a range, as knowable on a date — and
filters on no security at all, so the planner fell back to `SCAN`. Measured on
92M rows: **3 minutes 30 seconds** for one cross-section.
`ix_security_fundamental_cross_section` on `(metric, period_end,
knowledge_time)` takes the same query to **3.1 seconds**, for 7.8 GB.


```
filings                          the causal anchor
  instrument_id, cik
  accession        UNIQUE — the SEC's own identifier
  form_type        10-K, 10-Q, 8-K, 10-K/A, 25, 15, ...
  period_end
  filed_at         when submitted to EDGAR
  accepted_at      nullable
  knowledge_time
  source, source_version

fundamental_facts                immutable, append-only, one row per number
  instrument_id
  filing_id -> filings           nullable when the vendor gives no accession
  accession                      denormalised for provenance
  metric                         vendor's own name, unmapped
  statement_type                 income | balance | cash_flow | metric | other
  fiscal_period, fiscal_year, period_end
  value, unit
  is_original_report             true = as-reported, false = restated
  restates_period_end            nullable
  filed_at, knowledge_time
  source, source_version, ingested_at
  UNIQUE (instrument_id, metric, period_end, fiscal_period, accession, source, source_version)
```

**The three dates, and the rule that must never bend:**

| field | meaning |
|---|---|
| `period_end` | the fiscal period the number describes |
| `filed_at` | when the filing containing it was submitted |
| `knowledge_time` | when TradeIt may first use it |

`knowledge_time` is derived from `filed_at`, **never** from `period_end`. A
quarter ending 31 March was not knowable on 31 March. The existing importer
already quarantines fundamentals lacking a filing timestamp rather than
guessing, and that behaviour is retained unchanged.

**Restatements create facts; they never overwrite them.** Uniqueness includes
`accession`, so a restatement of the same period under a later accession is a
new row. The point-in-time read is then simply:

```sql
SELECT DISTINCT ON (instrument_id, metric, period_end) value
FROM fundamental_facts
WHERE knowledge_time <= :as_of
ORDER BY instrument_id, metric, period_end, knowledge_time DESC
```

which yields *what the platform believed then*. A restatement filed afterwards
does not exist at that as-of, which is the correct answer rather than a special
case.

## 7. Is 1998-01-01 achievable? — the answer is now tiered

**The single most useful move available is to stop treating "prices" and
"fundamentals" as one problem.** They have different sources, different licences
and different start dates, and conflating them is what previously made the whole
corpus hostage to a fundamentals vendor.

| requirement | 1998–2008 | 2009– | source |
|---|---|---|---|
| daily prices, active **and** delisted | **no vendor selected or probed** | same | commercial |
| corporate actions | derivable from a vendor's adjustment bases — UNTESTED | same + 8-K | commercial + EDGAR |
| filing dates, accessions, form types | **yes** — EDGAR full-index | yes | SEC EDGAR |
| machine-readable statement **values** | **no** — pre-XBRL | **yes** — XBRL/FSDS | SEC EDGAR |
| as-reported vs restated | n/a while values are absent | yes — amendment chain | SEC EDGAR |
| delisting **reasons** | Forms 25 / 15 / 8-K 1.03 | same | SEC EDGAR |

### 7.1 The price corpus reaches 1998 or it does not ship

The start date is a **coverage** requirement, not a sourcing convenience: a
corpus that begins in 2003 cannot show the dot-com build-up and collapse, which
is the entire objective. If a probed vendor reaches 1998-01-01 with universe
breadth, the price spine ships; if none does, we are back to sourcing prices —
not to moving the date.

#### Owner's ruling, on EODHD's Jan 2000 delisted start

EODHD asserts near-complete US **delisted** coverage from **January 2000**, and
active US coverage "from the beginning" (`PHASE_06_VENDOR_MATRIX.md` §1.3). For
1998 and 1999 it would therefore supply survivors and not the companies that
died, which is survivorship bias in the two years the dot-com build-up occupies.

Three options were put to the owner: hold §7.1 and treat EODHD as insufficient;
amend §7.1 to 2000; or take EODHD for 2000+ and source 1998–1999 separately.

> **Decision: the third. §7.1 is NOT amended and the 1998 target stands.**

What that means in practice, stated so it cannot drift:

- **EODHD is a candidate for the 2000-onward price spine**, subject to the
  sample files and to price, neither of which is settled.
- **1998–1999 delisted coverage remains an open sourcing requirement.** It is a
  known, named gap rather than a silently shortened corpus.
- **The corpus may not be described as reaching 1998 until that gap is filled.**
  A `research-01` built on EODHD alone begins, for survivorship purposes, in
  2000, and any survivorship claim about 1998–1999 made on it would be false.

This is deliberately not the cheapest reading. Amending the date to 2000 would
have been one line and would have quietly redefined the thing the corpus was
built to show.

### 7.2 Pre-2009 fundamental *values* — three options, evaluated

**A constraint that used to sit here has been removed.** It read that no
subscription source could be used if cancelling it would force deletion of
`research-01`, and it eliminated Sharadar and EODHD outright on that ground
alone. **That elimination is withdrawn and the rule is gone.** What a vendor
requires when a subscription ends is the operator's own decision, taken at the
operator's discretion; it is not a corpus-design constraint, it is not a
selection criterion, and no part of this contract is built around it
(`PHASE_06_VENDOR_MATRIX.md` §1). Vendors are judged on **coverage, data quality
and price**. Nothing else was resting on this rule — it filtered candidates and
never shaped a schema, an interface or a stored field.

**Option 1 — parse SEC filing HTML/text ourselves.**
Free, public domain, permanent, no licence risk of any kind, and the values are
literally as-filed. Against it: roughly 11 years × several thousand issuers ×
~4 filings a year is on the order of 300,000+ documents, largely unstructured
ASCII and early HTML with no consistent table markup, no tagging, and inconsistent
line-item naming. Realistic accuracy on headline items (revenue, net income,
total assets, shares outstanding) is good; anything deeper degrades fast.
**Verdict: viable but a project in itself**, and not a fallback that can be
casually invoked.

**Option 2 — a historical fundamentals dataset from a vendor.**
The right shape, and **no candidate has been tested.** Sharadar and EODHD both
claim pre-2009 fundamentals; neither has been probed, and the point-in-time
acceptance rule in `PHASE_06_VENDOR_MATRIX.md` §4 is the test that decides them.
**Verdict: keep looking, and test before buying** — the failure mode here is a
`filed_at` reconstructed from a period-end, which is invisible in aggregate and
contaminates exactly the dot-com results the corpus exists to produce.

**Option 3 — defer pre-2009 fundamental values entirely.**
`research-01` v1 ships as: **prices 1998+**, **filing metadata and knowledge-time
1994 Q3+**, **fundamental values 2009+**. Pre-2009, the corpus knows *that* a
company filed, *when* it filed, and *what form* — the full causal spine — but not
the numbers inside.

**Recommended: Option 3 now, Option 1 narrowly, Option 2 only after a probe.**

The justification is specific rather than convenient: this platform detects and
validates **price and volume structure**. Every detector in Phase 4/5 is
geometric. The dot-com objective is to expose that machinery to a full
speculative build-up and collapse *with the failures still in the data* — which
is a price-and-survivorship problem, not a fundamentals problem. Pre-2009
statement values would enrich later cross-sectional work; their absence does not
block the objective, and it must be recorded as a declared corpus limitation in
`CORPUS_REGISTRY.md` rather than left implicit.

Option 1 is worth doing **narrowly and immediately** for the control universe
only: a few hundred filings, headline metrics, hand-checkable. That both proves
the parsing approach and gives the controls their required "at least one filing
with `accession`, `filed_at` and `period_end`".

### 7.3 What is explicitly *not* being conceded

Falling back to a 2009 start is still rejected. XBRL's start is a fact about
machine-readable **values**, not about **dates** or **prices**, and EDGAR
supplies authoritative dates from 1994 Q3 — four years before the research-01
start. Moving the corpus to 2009 would discard the dot-com window to solve a
problem the dot-com window does not have.

## 8. Source provenance is a contract condition

**Every source in `ohlcv_bars.source`, `fundamental_facts.source` and
`corporate_actions.source` must be recorded before its first row is written**,
so that any published number can be traced to the vendor that supplied it and
re-derived if that vendor is ever replaced.

This is a rule of the data contract at the same level as the three-date rule and
the no-splicing rule. It exists for reproducibility: a corpus whose rows cannot
say where they came from cannot be audited, corrected, or rebuilt from a
different source.

SEC EDGAR is public domain and free, which is why it carries the *spine* —
filing dates, accessions, form types and delisting events — under every outcome
and independent of any vendor decision.

## 9. Coverage and capability reporting

Reuses the existing mechanism unchanged. `CapabilityIndex` already distinguishes
"no rows because nothing happened" from "no rows because the vendor refused",
and that distinction is the whole of survivorship honesty:

> **Absence is never evidence that a security did not exist.**

`research-01`'s import must report, per instrument and per dataset: obtained /
not offered / refused / empty. A gate check then fails the corpus if any control
security in `docs/DOTCOM_CONTROL_UNIVERSE.md` is absent without a recorded
reason.

## 10. Corpus classification and prohibited conclusions

**`research-01` is assigned one of four classifications by measurement, not by
intention.** The thresholds are fixed in advance (`KIBOT_DATA_PROBE.md` §G7) and
the assignment is published in `CORPUS_REGISTRY.md` beside the corpus,
permanently.

The point of naming the class is not honesty for its own sake. It is that **each
class prohibits a specific list of conclusions**, and without the list the caveat
degrades into a disclaimer nobody acts on.

| class | what it means |
|---|---|
| **`SURVIVORSHIP_SAFE_RESEARCH_GRADE`** | the disappeared population is substantially present and measured. **Its quantitative threshold is deliberately unset** until the denominator has been built and its distribution examined; passing all 30 controls is necessary and not sufficient |
| **`MATERIALLY_SURVIVORSHIP_CORRECTED`** | most of it is present; the deficit is quantified and its direction known |
| **`PARTIALLY_SURVIVORSHIP_CORRECTED`** | a real but incomplete correction; the deficit is large enough to change conclusions |
| **`SURVIVOR_BIASED`** | the corpus is what survived. `full-01` holds this class today |

### What each class prohibits

| conclusion type | research-grade | materially corrected | partially corrected | survivor-biased |
|---|---|---|---|---|
| machinery validation, causality, provenance, reproducibility | ✅ | ✅ | ✅ | ✅ |
| descriptive geometry of detected structures | ✅ | ✅ | ✅ | ✅ |
| **unconditional base rates** ("how often does X occur") | ✅ | ✅ *with the stated deficit* | ❌ | ❌ |
| **cross-sectional frequency** across the universe | ✅ | ✅ *with the deficit* | ❌ | ❌ |
| **cross-era / regime comparison** (1998–2002 vs 2015–2026) | ✅ | ⚠️ only if per-era coverage is comparable | ❌ | ❌ |
| **cohort or lifespan claims** (newly listed, speculative names) | ✅ | ❌ *if the short-lived bucket is thin* | ❌ | ❌ |
| **failure-tail claims** ("what fraction end in delisting") | ✅ | ❌ | ❌ | ❌ |
| **small-cap / exchange-tier claims** | ✅ | ❌ *if exchange skew is present* | ❌ | ❌ |
| anything conditioned on **why** a security ended | only where `delisting_reason` is known and its prevalence published | same | same | ❌ |

Two rules apply at every class:

1. **A quantified deficit must accompany the claim, not a footnote about it.** "Base
   rate 12%, over a corpus with measured `bounded_coverage` 0.58 and a short-lived
   deficit of 0.41" is a usable statement. "Base rate 12% (corpus has some
   survivorship bias)" is not.
2. **Both coverage bounds are published together**, always. `matched_coverage`
   alone is the standard way this measurement is made to look better than it is.

### Independent of survivorship class

These limits hold whatever the classification turns out to be, and are declared
rather than discovered:

- **`pre_2009_fundamental_coverage = limited`** — filing dates, accessions and
  form types are present from 1994 Q3; statement *values* only from 2009 (§7.2).
  **TradeIt must never silently substitute a restated or hindsight value for a
  missing as-reported one**, and `knowledge_time` is never manufactured — an
  absent filing timestamp quarantines the fact, as the existing importer already
  does.
- **OTC / pink-sheet trading is out of scope.** A security that delisted from an
  exchange and continued OTC is treated as ended: a recorded modelling decision.
- **Delisting reasons are frequently absent.** `delisting_reason = unknown` is a
  first-class value and its prevalence is published.

## Which filings can bind a ticker — measured 2026-09-06, mostly negative

The ticker resolver reads **annual reports only** (`10-K`, `10-K405`, `10-KSB`,
`10-K/A`, `20-F`, `40-F`). 53% of dead registrants never filed one, so the
obvious question is which other filings name a trading symbol. Counting which
forms exist is not an answer to that question, and treating it as one produced a
57% estimate that collapsed to 9% the moment real documents were opened.

**Twelve fetches, four families, three dead ends.** Each was sampled at three
points across its date range before any extractor was written.

| family | untickered dead registrants holding one | carries a trading symbol? |
|---|---:|---|
| `8-A12B` / `8-A12G` | 3,982 | **No.** Registers a *class* on a *named exchange* — "Title of each class", "Name of each exchange". Samples from 1995, 1999 and 2020 contain the word "symbol" **zero times** |
| `25` / `25-NSE` | 1,243 | **No.** A 1.5–3.6 KB administrative delisting notice. No symbol in any sample |
| `DEF 14A` | 6,213 | **No.** One sample's only match was *"any logo or symbol authorized by the Sub-Adviser"* — a trademark sense, and precisely the false positive `confirm.py`'s stopword list exists to refuse |
| `424B*` | 6,315 | **Sometimes**, and in the right shape: *"The Company's Class A common stock is listed on the New York Stock Exchange, Inc. under the symbol \"HFI.\""* |
| `10-Q` | 7,217 | **No, in practice.** The cover page carries the 12(b) table only from 2019; **30** of the 7,217 filed a 10-Q that late. These registrants died first |

`8-A12B` was the most promising on paper — it is the form that puts a class on an
exchange, and `evidence.py` already classifies it as a birth form — and it is
useless for this purpose. Three fetches established that, against an extractor
that would have taken a day to write and would have returned nothing.

### What remains, and the split that matters within it

`424B` divides on whose offering the document describes, and the distinction is
not cosmetic:

```
424B1 / 424B4 / 424A  — the issuer's own offering        1,782
424B2 / 424B3 / 424B5 — resale, merger, shelf takedown   4,533
```

A `424B3` for a merger or spin-off describes **the other party**. One sample
yielded three symbols — `LPS`, `FNF`, `BKFS` — none of which was necessarily the
filer's. `confirm.py` refuses a filing naming several distinct symbols, so that
case fails closed; the danger is the document that names exactly one, belonging
to somebody else. That binds silently and wrongly.

**So the safe pool is 1,782 registrants, not the 11,311 the form counts
suggested.** At the annual-report route's observed rates that is a few hundred
further identities — real, evidenced, and worth having, but an order of
magnitude smaller than the form counts implied.

**Do not re-investigate `8-A12B`, `25`, `DEF 14A` or `10-Q` for ticker binding.**
The answer is recorded here so the next reading of the form counts does not
start the same search again.

## Why a dead registrant has no ticker — measured 2026-09-06

Two causes were assumed to be one. Separating them changes what is worth
building.

### Cause 1: a defect. The document could not be addressed

`_annual_reports` read `primaryDocument` from the SEC submissions index and
skipped any filing where it was empty. **Every pre-2001 submission is a single
`.txt` with no primary document named**, so those filings were reported as
`no_annual_report` — a registrant that filed three annual reports was counted
as having filed none.

```
untickered dead registrants with an annual report in the index   9,472
  latest annual report before 2001 (primaryDocument always empty) 3,488
```

Fixed: an empty `primaryDocument` now falls back to the complete submission
text file at `.../<accession>/<accession>.txt`. **The fallback is not the
preference** — a complete submission concatenates every exhibit, so a symbol
appearing in an exhibit could be misread as the filing's own statement. It is
used only where the SEC names no primary document.

### Cause 2: not a defect. The registrant had no listed stock

Two of the recovered pre-2001 annual reports were then read in full, and the
extractor's silence turned out to be correct:

| registrant | what the filing says |
|---|---|
| `AMERICAN RESTAURANT GROUP HOLDINGS INC`, 10-K405 1997 | *"Securities registered pursuant to Section 12(b): … **None**. Section 12(g): **None**"*. The words "symbol", "traded", "listed on" and "Nasdaq" appear **zero** times in 99,766 characters |
| `FIDELITY LEASING INCOME FUND III LP`, 10-K 1998 | 12(b) *"Not applicable"*; 12(g) *"Limited Partnership Interests"* |

The first files a 10-K because it has **registered public debt**; the second is
a **limited partnership**. Neither has an exchange-listed equity, so neither has
a ticker, and no extractor improvement will produce one.

This is the fund lesson in a second guise. A registrant filing Exchange Act
annual reports is **not** thereby a listed equity: debt-only issuers,
partnerships and bond-registering subsidiaries all file them. The
`reporting_regime` split (§7g) removed investment companies from the coverage
denominator; it does not remove these, and they are the same kind of entry —
denominator that can never have a numerator.

**The measurement that would settle it is in the filings already fetched.** A
10-K cover page states its Section 12(b) securities, and *"None"* is positive
evidence of no listed class rather than an absence of evidence. Recording that
verdict per registrant would separate *"we have not found the ticker"* from
*"there was never a ticker to find"* — the same distinction `UNRESOLVED` draws
everywhere else in this system.

**Not built and not applied.** Scoping the denominator by it would change what
the corpus claims to be true and needs authorisation, as the fund scoping did.

## The 2,819 tickered-but-unpriced, accounted for — 2026-09-06

**A correction.** This was called "the single largest recoverable gap" in a
status report earlier the same day. It is not recoverable, and the reason is
the safeguards working rather than failing.

2,819 dead registrants hold an evidenced ticker and no price bar. Every one was
put through three independent tests.

### 1. Does the vendor carry the symbol at all?

EODHD publishes its own delisted US inventory — 59,925 symbols. Intersecting:

```
tickered dead registrants with no bars     2,819
  symbol absent from the vendor entirely   2,141   (76%)
  symbol present                             678   (24%)
```

The 2,141 are unrecoverable from this vendor at any price. No amount of
identity work reaches them.

### 2. For the 678 it does carry, whose series is it?

Each was fetched over its full available range and the series compared against
the registrant's own lifetime:

| | n | |
|---|---:|---|
| **a different company that took the ticker later** | **547** | 81% |
| overlaps the registrant's life | 130 | 19% |
| vendor returned nothing | 1 | |

**Four out of five would have imported another company's prices.** Worked
examples, all previously "failures":

| symbol | registrant's life | vendor's series |
|---|---|---|
| `SPK` | 1996-05 → **2001-07** | **2021-08** → 2023-01 |
| `THS` | 1996-05 → **1999-12** | **2005-06** → 2026-02 |
| `CUNB` | 1995-03 → **1996-12** | **2005-11** → 2017-10 |

The backfill asked for each registrant's *own* window, got nothing, and
recorded a failure. That is the window bound doing exactly what §"bounded by
their own lifetimes" says it is for. **A recorded failure here is a prevented
splice**, and reading the failure count as lost coverage inverts its meaning.

The 130 "overlaps" are generous: the test allowed ±365 days of slack, so
adjacency counts as overlap. The true figure is lower.

### 3. Is the security already priced under a sibling registrant?

**99** of the 2,819 share their ticker with a *different registrant that is
priced*, over an overlapping interval. These are REIT/operating-partnership
pairs and holding-company structures — **two SEC registrants, one traded
security**:

| symbol | unpriced registrant | priced sibling |
|---|---|---|
| `EOP` | CIK 1038339 | CIK 1043866 — 4,582 bars |
| `ACFC` | CIK 1284077 | CIK 1404296 — 5,318 bars |
| `AAII` | CIK 771729 | CIK 1013243 — 4,114 bars |
| `ACO` | CIK 863881 | CIK 813621 — 8,230 bars |

`resolve_security` assigns each bar to exactly one security, so the sibling
holds them all. **Giving both a copy would double-count one stock**, which is a
worse error than the gap it would close.

### What is actually left

After the vendor's absence, ticker reuse and sibling coverage, the residue that
could plausibly be recovered is **on the order of thirty registrants**, not
2,819. **Dead-side pricing is finished** as far as this vendor can take it.

**The lesson, which is the reusable part:** a failure count from a
splice-preventing fetch is not a coverage deficit. It is the number of times
the system refused to guess, and it should be read alongside *why* each refusal
happened before anybody plans work against it.

## Verifying 60.5 million bars — 2026-09-07

Coverage and completeness were already measured. **Correctness was not**, and
the gate says so in its own limitations. These are the checks run after the
live universe landed.

### What held

| check | result |
|---|---|
| OHLC coherence (the four check constraints) | **0** incoherent bars in 60,508,185 |
| duplicate `(security, session, basis, knowledge_time)` | **0** |
| bars that failed to resolve to a security | **0** of 36,548,491 newly landed |
| known splits at the right date and ratio | **7 of 8** — AAPL 4:1 and 7:1, TSLA 5:1 and 3:1, NVDA 10:1 and 4:1, AMZN 20:1 |

### Aggregate history, reconstructed blind

Nothing in the pipeline knows about market events. Ranking months by the
**median** daily return on the adjusted basis reproduces them anyway:

```
2008-10   -0.260%/day     Lehman aftermath -- the worst month
2018-12   -0.210%/day     December 2018 selloff
2022-09   -0.151%/day     worst month of the 2022 bear
1990-08   -0.148%/day     Iraq invades Kuwait
2018-10   -0.076%/day     October 2018 correction
```

**The first version of this test was wrong and is recorded as such.** It used
the *mean* on the *raw* basis and reported +72,159%/day for 2020-03 and
+3,302,269%/day for 2006-05. Those are reverse splits: a 1-for-1000 reverse
split multiplies an unadjusted price by a thousand in one session, and a mean
is defenceless against it. The months it ranked "worst" happened to look
plausible, which is exactly what makes the error dangerous — **a broken metric
that agrees with your expectations is harder to catch than one that does not.**
Use the median, and the adjusted basis, or the statistic measures corporate
actions rather than markets.

### What did not hold

**3,930 bars (0.0065%) fall on dates the US market was closed** — July 4th,
Labor Day, Thanksgiving, Juneteenth, Good Friday, and 2025-01-09, the national
day of mourning. Roughly eight to ten dates a year, every year. The vendor
emits them; nothing in the import refuses them. `TradingCalendar` already knows
the real schedule, so this is a check that can be added rather than a fact that
must be tolerated.

**12.8% of adjusted bars close identical to the previous session and 5.8% carry
zero volume**, consistently across every era. That is not an error — it is what
a full universe including microcaps and OTC names looks like — but it means
**a liquidity filter is a precondition for any backtest**, not a refinement of
one. A median daily return of exactly 0.000% in months like 2020-03 is that
population showing through.

### The coverage hole this exposed: multi-class issuers

Alphabet is absent from the corpus — no ticker, no bars — and it is **not** an
identity failure. CIK 1652044 is held. It carries **two** tickers, `GOOGL`
(Class A) and `GOOG` (Class C), and `research01_bind_current_tickers.py`
refuses any CIK holding more than one, because the corpus stores **one
placeholder security per issuer with a NULL `class_label`**. Binding both
classes to one row would conflate two securities that trade at different
prices.

**The refusal is right and the model is incomplete.** 1,158 registrants are in
this state, including some of the largest US companies and most closed-end
funds with preferred classes. Reaching them needs one security per share class
with an evidenced label — an identity question, not a fetching one, and the
next real piece of architecture on the data side.
