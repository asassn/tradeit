# `research-01` — data contract

The survivorship-safe historical research corpus. **The only corpus a
cross-sectional or economic statistic may ever cite.** `full-01` is frozen and
remains machinery-validation only (`docs/CORPUS_REGISTRY.md`).

**Not implemented.** This is the contract for approval.

---

## 1. Scope

| | |
|---|---|
| preferred start | **1998-01-01** — conditional, see §7 |
| end | rolling; the corpus is maintained forward, not re-purchased |
| universe | US-listed equities, **active and delisted**, no survivorship filter of any kind |
| timeframe | daily bars; higher timeframes derived |

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

1. **Prefer `raw_unadjusted`.** Sharadar's SEP publishes it; the existing
   scale-invariance work exists precisely because adjusted-only series are
   lossy. Where a vendor offers several bases, ingest the raw one and derive.
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

## 7. Is 1998-01-01 achievable?

**Conditionally yes, and the condition is testable before purchase.**

| requirement | 1998–2008 | 2009– |
|---|---|---|
| filing dates | **yes** — EDGAR full-index from 1993 Q1 | yes |
| accession numbers | **yes** — same source | yes |
| machine-readable statement values | **no** — pre-XBRL; vendor-parsed only | yes — XBRL |
| as-reported vs restated | vendor-supplied only | vendor + amendment chain |
| prices, active + delisted | vendor-dependent | vendor-dependent |
| corporate actions | vendor-dependent | vendor + 8-K |

**The deciding question is not whether values exist for 1998 — it is whether the
vendor's filing date for those values is real.** §4 of the vendor matrix states
the test and the acceptance rule.

**I do not recommend falling back to 2009 by default.** XBRL's start date is a
fact about machine-readable *values*, not about *dates*, and EDGAR supplies
authoritative dates from 1993. If the vendor's pre-2009 `DATEKEY` matches EDGAR,
1998 is sound. If it does not, the honest floor is **2003-01-01** — after the
questionable segment rather than straddling it — and the dot-com objective is
then unmet by that vendor and should be re-sourced rather than quietly dropped.

## 8. Coverage and capability reporting

Reuses the existing mechanism unchanged. `CapabilityIndex` already distinguishes
"no rows because nothing happened" from "no rows because the vendor refused",
and that distinction is the whole of survivorship honesty:

> **Absence is never evidence that a security did not exist.**

`research-01`'s import must report, per instrument and per dataset: obtained /
not offered / refused / empty. A gate check then fails the corpus if any control
security in `docs/DOTCOM_CONTROL_UNIVERSE.md` is absent without a recorded
reason.
