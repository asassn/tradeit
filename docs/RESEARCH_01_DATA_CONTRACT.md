# `research-01` — data contract

The survivorship-safe historical research corpus. **The only corpus a
cross-sectional or economic statistic may ever cite.** `full-01` is frozen and
remains machinery-validation only (`docs/CORPUS_REGISTRY.md`).

**Not implemented.** This is the contract for approval.

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

| requirement | 1998–2008 | 2009– | source | licence |
|---|---|---|---|---|
| daily prices, active **and** delisted | **candidate: Kibot** — UNTESTED | same | commercial, one-time | **permanent retention permitted** — USER-VERIFIED |
| corporate actions | derivable from Kibot's three adjustment bases — UNTESTED | same + 8-K | commercial + EDGAR | as above |
| filing dates, accessions, form types | **yes** — EDGAR full-index | yes | SEC EDGAR | public domain |
| machine-readable statement **values** | **no** — pre-XBRL | **yes** — XBRL/FSDS | SEC EDGAR | public domain |
| as-reported vs restated | n/a while values are absent | yes — amendment chain | SEC EDGAR | public domain |
| delisting **reasons** | Forms 25 / 15 / 8-K 1.03 | same | SEC EDGAR | public domain |

### 7.1 The price corpus reaches 1998 without any encumbered vendor

If the Kibot probe passes, `research-01`'s price spine runs from 1998-01-01 under
a licence that permits keeping it forever. **No retention-prohibited source
enters the corpus.** That is the whole reason the start date is no longer
conditional.

If the probe fails, we are back to sourcing prices — not to moving the date.

### 7.2 Pre-2009 fundamental *values* — three options, evaluated

The constraint is absolute: **no subscription source whose cancellation would
force deletion of `research-01`.** That eliminates Sharadar and EODHD outright
(`PHASE_06_VENDOR_MATRIX.md` §1), and it is the filter every future candidate
must pass *before* its data is examined.

**Option 1 — parse SEC filing HTML/text ourselves.**
Free, public domain, permanent, no licence risk of any kind, and the values are
literally as-filed. Against it: roughly 11 years × several thousand issuers ×
~4 filings a year is on the order of 300,000+ documents, largely unstructured
ASCII and early HTML with no consistent table markup, no tagging, and inconsistent
line-item naming. Realistic accuracy on headline items (revenue, net income,
total assets, shares outstanding) is good; anything deeper degrades fast.
**Verdict: viable but a project in itself**, and not a fallback that can be
casually invoked.

**Option 2 — a one-time or perpetually licensed historical fundamentals dataset.**
The right shape, but **no qualifying candidate has been found.** Every source
examined so far requires deletion on termination. Academic sources (Compustat via
WRDS and similar) carry stricter redistribution and retention terms, not looser.
**Verdict: keep looking, retention-first — filter on the licence before
evaluating the data**, which is the inverse of how vendors are normally assessed
and the lesson of this milestone.

**Option 3 — defer pre-2009 fundamental values entirely.**
`research-01` v1 ships as: **prices 1998+**, **filing metadata and knowledge-time
1994 Q3+**, **fundamental values 2009+**. Pre-2009, the corpus knows *that* a
company filed, *when* it filed, and *what form* — the full causal spine — but not
the numbers inside.

**Recommended: Option 3 now, Option 1 narrowly, Option 2 only if a
retention-permitting source appears.**

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

## 8. Retention is a contract condition, not a procurement detail

**No dataset may enter `research-01` unless its licence permits retaining it —
and datasets derived from it — indefinitely after any subscription ends.**

This is now a rule of the data contract, at the same level as the three-date rule
and the no-splicing rule, because it has the same failure mode: violate it and
the corpus must be destroyed rather than corrected.

Consequences already in force:

1. Sharadar and EODHD are **excluded from `research-01`** on licence grounds
   alone, regardless of data quality.
2. Every source in `price_facts.source`, `fundamental_facts.source` and
   `corporate_action_facts.source` must have a recorded retention determination
   before its first row is written.
3. **Twelve Data and FMP retention terms are UNVERIFIED** and must be established
   before either becomes part of the permanent corpus — this affects the forward
   accumulation model in `FORWARD_SURVIVORSHIP_SYSTEM.md`, not just the
   historical backfill.
4. SEC EDGAR is public domain: no licence, no termination, nothing to fail.

## 9. Coverage and capability reporting

Reuses the existing mechanism unchanged. `CapabilityIndex` already distinguishes
"no rows because nothing happened" from "no rows because the vendor refused",
and that distinction is the whole of survivorship honesty:

> **Absence is never evidence that a security did not exist.**

`research-01`'s import must report, per instrument and per dataset: obtained /
not offered / refused / empty. A gate check then fails the corpus if any control
security in `docs/DOTCOM_CONTROL_UNIVERSE.md` is absent without a recorded
reason.
