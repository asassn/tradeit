# `research-01` data dictionary

**Read this before querying the corpus.** It is written for someone — or some
tool — pointed at the file with no other context, and its purpose is to prevent
confident wrong answers rather than to list column names.

| | |
|---|---|
| file | `/Users/ericsasson/Documents/GitHub/tradeit/research01.sqlite` |
| format | SQLite 3, single file, no extensions required |
| size | ~61 GB |
| tables declared | 69 (16 carry data; the rest are declared for later phases) |
| in git | **no** — `.gitignore` excludes it; only code and docs are pushed |

Everything here is measured from the file, not from memory. **When a new fact
about how to read this corpus is discovered, it belongs in this document** —
this is the guide book, and a convention that lives only in a commit message
will be rediscovered the hard way.

---

## 0. Six ways this corpus will mislead you

Every one of these has produced a confident wrong answer during construction,
most of them mine. They are listed first because a reader who stops here has
still got the most important part.

### 0.1 Every trading day is stored **twice**

`security_price_facts.adjustment_basis` holds `raw` **and** `total` for the same
session — the vendor's unadjusted print and its split/dividend-adjusted one.

```sql
-- WRONG: reports double the real number of sessions
select count(*) from security_price_facts;
-- RIGHT
select count(*) from security_price_facts where adjustment_basis = 'raw';
```

This produced a measured "median density of 2.00 sessions per trading day",
which looked like a data-quality triumph and was an artefact of counting rows.

**Which basis to use:** `total` for returns and any cross-time comparison;
`raw` for reconstructing what a trader actually saw on the day. Never mix them
in one calculation.

### 0.2 `symbol_aliases.valid_to` is **exclusive**

The last day a security owns a ticker is `valid_to - 1 day`. A `NULL` means the
alias is open-ended — the SEC publishes it as current — and **not** that the end
is unknown.

```sql
-- which security held 'ACME' on 2004-06-30
select security_id from symbol_aliases
where alias_kind = 'ticker' and alias_value = 'ACME'
  and valid_from <= '2004-06-30'
  and (valid_to is null or valid_to > '2004-06-30');
```

Resolution is **per date**, never per ticker. Tickers are reused: two unrelated
companies can hold `ACME` in different decades, and asking "which security is
ACME" has no answer.

### 0.3 Rows the corpus knows are wrong are **still in the tables**

This corpus bounds what it *reads* rather than destroying what it was *sent*, so
an audit can always see the vendor's error:

* **~3,900 bars fall on days the US market was closed** — July 4th,
  Thanksgiving, Good Friday, and 2025-01-09, the national day of mourning. The
  importer now refuses new ones; these arrived before it learned to.
* **~91,000 bars sit outside their security's adjudicated interval** — the
  splice-detection work placed them there rather than deleting them.

Both are excluded by `tradeit.research01.series.price_series`, which is the
supported read path. **A raw `SELECT` includes them.**

### 0.4 Names are **point-in-time**

`issuers.display_name` is the registrant's name as its most recent index row
rendered it. Yahoo! is `YAHOO INC`, not Altaba. BlackBerry is
`RESEARCH IN MOTION LTD`. Block is `SQUARE, INC.`

**A name search for a renamed company returns nothing and looks like absence.**
Five major companies were reported missing from this corpus on exactly that
mistake; all five were present. **Search by CIK.**

### 0.5 `class_label` is not always a class label

For the 34 curated control securities (`source = 'control_identity_evidence'`)
this column holds long provenance prose — paragraphs of reasoning about why an
identity was or was not established. For the 879 rows with
`source = 'edgar_filing_text'` it is a real share-class title taken verbatim
from a Section 12(b) cover table (`Class A Common Stock, $0.001 par value`).
Filter on `source` before treating it as a label.

### 0.6 A "failure" in a splice-preventing fetch is not a coverage gap

The price backfill asks the vendor only for each registrant's **own** lifetime.
When a different company later took that ticker, the request returns nothing and
is logged as a failure. Measured: of 678 such failures the vendor could serve,
**547 (81%) were a different company's series.** The failure count is the number
of times the system refused to guess — reading it as missing data inverts its
meaning.

---

## 1. The identity spine

Nine concepts are kept apart deliberately: issuer identity, security identity,
ticker identity, listing venue, filing venue, legal lifecycle, security
lifecycle, mapping quality and adjudication completeness. Most of the schema's
complexity is the cost of not collapsing them.

```
issuers ──< issuer_identifiers        (CIK, and other registry keys)
   │
   └──────< securities ──< symbol_aliases         (tickers, per-date validity)
                  │
                  ├──< security_price_facts
                  ├──< security_corporate_action_facts
                  └──< security_fundamental_facts
```

### `issuers` — 40,819 rows

| column | meaning |
|---|---|
| `issuer_id` | primary key |
| `display_name` | **point-in-time** name — see §0.4 |
| `note` | which cohort seeded it, e.g. `index-cohort/1994` |
| `source` | `edgar_full_index`, `sec_fsds_sub`, `edgar_filing_text`, `control_identity_evidence` |

### `issuer_identifiers` — 40,820 rows

The registry keys. `namespace = 'sec_cik'` is the SEC's own key for a
registrant; `value_normalized` is what to join on. `citation` states, in prose,
what evidence established the identifier — **every row carries one**.

The design is deliberately regulator-neutral: an issuer with no SEC CIK (a bank
filing with the FDIC) is keyed on `fdic_cert` instead. Do not assume `sec_cik`
exists for every issuer.

### `securities` — 41,698 rows

One row per tradeable security. **An issuer may have several**: 879 share-class
securities were created from Section 12(b) cover tables, so Alphabet has one row
for `GOOGL` (Class A) and one for `GOOG` (Class C).

**The placeholder is not orphaned.** Each issuer also keeps its original
`class_label IS NULL` security, which carries the **fundamentals** — those are
issuer-level financials belonging to no single class. **Joining a class's prices
to its issuer's fundamentals goes through `issuer_id`, not `security_id`.**

### `symbol_aliases` — 16,065 rows

| column | meaning |
|---|---|
| `alias_kind` | `ticker` is the evidenced kind; `vendor_symbol` is weaker and must be asked for explicitly |
| `alias_value` | stored in the rendering that can be **priced** — `BRK-A`, not the filing's `BRK.A` |
| `valid_from` / `valid_to` | **half-open** `[from, to)` — see §0.2 |
| `knowledge_time` | when this belief was formed; later beliefs supersede earlier ones |
| `citation` | the filing sentence that binds the symbol, verbatim |

`knowledge_source` values and what they mean:

| value | strength |
|---|---|
| `edgar_filing_text` | the registrant's own words, with the sentence quoted in `citation` |
| `sec_company_tickers` | SEC's published current CIK→ticker file |
| `eodhd_symbol_span` | vendor-derived; weakest, 4 rows |
| `control_identity_evidence` | human-verified control |

---

## 2. The fact tables

### `security_price_facts` — 65,668,853 rows

Two rows per session per security — see §0.1. `volume_adjusted` says whether the
volume was scaled by the same factor as the prices.

Four check constraints hold on every row and are worth relying on:
`high >= low`, `high >= open`, `high >= close`, `low <= open`, `low <= close`,
`volume >= 0`. Measured: **0 violations in 65.7 million rows.**

### `security_corporate_action_facts` — 204,473 rows

`action_type` ∈ {`split`, `cash_dividend`, `stock_dividend`, …}. For a split,
`ratio` is the multiplier — `4` for a 4-for-1, `0.1` for a 1-for-10 reverse.
**`ex_date` is the ex-date, not the announcement date.**

A split applies to a bar only when its ex-date is **strictly after** the bar: a
split on the bar's own date is already in that day's print.

Verified against known history: AAPL 4:1 (2020-08-31) and 7:1 (2014-06-09),
TSLA 5:1 and 3:1, NVDA 10:1 and 4:1, AMZN 20:1 — all present at the right date
and ratio.

### `security_fundamental_facts` — 92,022,159 rows

From the SEC Financial Statement Data Sets. **Effectively begins 2011Q3**, not
2009 — the earlier archives are 223-byte headers.

`knowledge_time` is when the filing was public; `period_end` is what the number
describes. **Reading by `period_end` is look-ahead.** Always filter
`knowledge_time <= as_of`.

`basis` distinguishes as-reported from restated values.

### `filings` — 3,528,865 rows

One row per filing from the EDGAR full index. **Incomplete for issuers seeded
from `sec_fsds_sub`**: the index seeder skipped issuers already held, so 7,169
of 7,255 XBRL-cohort issuers show a last filing earlier than their own exit
date. Restrict to `issuers.source = 'edgar_full_index'` for any analysis that
depends on filing history being complete.

---

## 3. Tables declared and empty

53 tables exist for later phases and hold **0 rows**: `ohlcv_bars`,
`instruments`, `listings`, `patterns`, `backtest_runs`, `portfolios`, `orders`,
`trades` and others. **`ohlcv_bars` being empty is the one that surprises
people** — all prices live in `security_price_facts`; `ohlcv_bars` belongs to
the separate `full-01` machinery.

---

## 3a. The views — the short answer to everything in §0

Built by `scripts/research01_build_views.py`, defined in
`tradeit.research01.views`, rebuildable at any time. **They add no rows and
alter none**; dropping one costs nothing.

| view | what it gives you |
|---|---|
| `v_prices` | one row per session, **adjusted**, holiday and spliced bars already excluded, latest revision only |
| `v_prices_raw` | the same, on the **unadjusted** print |
| `v_security_tickers` | one row per ticker held, with `last_day` **inclusive** — the off-by-one in §0.2 already done |
| `v_dead_registrants` | the survivorship population with `has_ticker` / `has_prices` flags |

```sql
select * from v_prices where security_id = 42 order by session_date;
```

That query is correct by construction. It reads a full 9,237-bar series in
**0.09s**, and `tests/unit/test_research01_views.py` asserts the view returns
**identical** sessions to `price_series` — the views are not a second opinion.

`trading_sessions` is a materialised table, not a view, because an exchange
calendar cannot be expressed in SQL. Rebuild it whenever the corpus is extended.

### The one thing the views cannot do

**A view takes no `as_of`, so it cannot be point-in-time.** `v_prices` returns
*the current belief*. 327,924 keys in this corpus carry more than one revision,
so this is a real difference and not a technicality.

**Anything asking what was knowable on a past date must use
`price_series(session, security_id, as_of=...)`.** The views serve the simple
case; they do not replace the library, and this warning is repeated inside each
view's own SQL where `.schema` will show it.

## 4. How to read it correctly

**Use the library.** `tradeit.research01.series.price_series` applies the
adjudicated interval, the exchange calendar, per-date alias resolution and
point-in-time revision selection. A raw `SELECT` gets none of that.

```python
from tradeit.research01.series import price_series
bars = price_series(session, security_id, as_of=datetime(2026, 1, 1, tzinfo=UTC))
```

If you must query directly:

1. filter `adjustment_basis`
2. filter `knowledge_time <= as_of`
3. resolve the alias **per bar date**
4. exclude non-session dates (`tradeit.core.calendar.TradingCalendar`)
5. apply `tradeit.research01.tradability` before assuming a day was tradeable

---

## 5. What this corpus does **not** claim

* **It is not survivorship-safe.** The gate returns `SURVIVOR_BIASED`. Of 30,646
  registrants with a confirmed dated EDGAR exit, identity is held for 100%, a
  ticker for ~32%, and prices for ~21%. **No strategy result computed on it
  today is evidence of profitability.**
* **Completeness is measured; correctness beyond that is not.** 91.3% of dead
  registrants' series reach within three months of the exit. A dense series
  reaching its exit can still be another company's data spliced in.
* **It is a *daily* corpus.** No intraday data exists.
* **Pre-2006 exchange-delisting evidence does not exist in EDGAR.** For
  1994–2001 the exits are Form 15 *reporting* exits, not delistings.

---

## 6. Maintaining this document

**A new fact about how to read the corpus belongs here on the day it is found.**
The six traps in §0 were each discovered by getting an answer wrong first. When
the next one appears — and the rate of discovery says it will — add it to §0
with the wrong answer it produced, because the wrong answer is what makes the
rule memorable.
