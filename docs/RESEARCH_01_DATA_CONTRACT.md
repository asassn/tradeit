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

Measured over all 862 priced securities:

| verdict | securities |
|---|---|
| `coherent` — ends where the registrant did | 768 |
| `splice_located` — dormancy, then a second company's run | 46 |
| `tail_artefact` — dormancy, then a handful of stale prints | 7 |
| `wholly_misattributed` — no bar in the registrant's lifetime at all | 6 |
| `contaminated_boundary_unknown` — known wrong, **not repairable** | 1 |
| `unresolved` — overruns, and no rule explains it | 34 |

**58 of the 79 received a dated boundary**, placing **79,873 bars — 9.7% of the
corpus — outside their security's interval.** The corpus's suspect count falls
from 79 to 31.

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

**A located boundary is not a clean bill of health**, and 31 series still end
more than seven years past their registrant: 21 that no rule could explain, and
10 whose earliest qualifying dormancy was itself late. `coherent` says only that
a series' *ending* does not betray a splice — one wholly inside the registrant's
own lifetime would leave no trace in the shape at all.

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
