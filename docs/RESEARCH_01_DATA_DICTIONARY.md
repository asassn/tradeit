# `research-01` data dictionary

**Read this before querying the corpus.** It is written for someone — or some
tool — pointed at the file with no other context, and its purpose is to prevent
confident wrong answers rather than to list column names.

| | |
|---|---|
| file | `/Users/ericsasson/Documents/GitHub/tradeit/research01.sqlite` |
| format | SQLite 3, single file, no extensions required |
| size | 67.4 GB |
| tables | 71 declared, plus 4 views. Only a handful carry data; the rest are declared for later phases, and an empty one is not a broken one |
| self-describing | `select * from corpus_readme order by ordinal` — §0 lives inside the file |
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

**Four of the six are now handled by the query views in §3a, and 0.5 has been
fixed outright.** They are still described here, because a view helps only a
caller who uses it and a raw `SELECT` still meets every trap below.

**This section also lives inside the database.** `select * from corpus_readme
order by ordinal` returns the same guidance, so a tool pointed at the file with
nothing beside it is not left guessing. The table is generated from the same
constants that build the views, so the two cannot drift apart.

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

### 0.5 `class_label` was not always a class label — **fixed, not documented**

This entry is kept as a record of a trap that no longer exists, because notes
written before 2026-09-08 still describe it.

The column once held two unrelated things: a real share-class title taken
verbatim from a Section 12(b) cover table (`Class A Common Stock, $0.001 par
value`) for 879 rows, **and** paragraphs of identity reasoning — up to 4,156
characters — for the 34 curated control securities. A reader had to check
`source` before believing the column.

`scripts/research01_cleanup.py` moved the prose to `securities.identity_evidence`
(migration `0017`) and left `class_label` NULL on those rows, which is the
honest value: a share-class title was never established for them. Nothing was
discarded, and the move was verified byte-for-byte against an independent
extract. **Measured after the move: `class_label` is at most 128 characters
across all 879 rows that have one.**

```sql
-- a share-class title, for every row that has one
select class_label from securities where class_label is not null;
-- why an identity is believed, for the 34 control securities
select identity_evidence from securities where identity_evidence is not null;
```

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

### `symbol_aliases` — 18,913 rows

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

### `security_price_facts` — 71,197,370 rows

Two rows per session per security — see §0.1. `volume_adjusted` says whether the
volume was scaled by the same factor as the prices.

Four check constraints hold on every row and are worth relying on:
`high >= low`, `high >= open`, `high >= close`, `low <= open`, `low <= close`,
`volume >= 0`. Measured: **0 violations in 65.7 million rows.**

### `security_corporate_action_facts` — 218,666 rows

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
| `v_registrants` | every registrant with a CIK, with `has_ticker` / `has_prices` coverage flags |

```sql
select * from v_prices where security_id = 42 order by session_date;
```

That query is correct by construction. It reads a full 9,237-bar series in
**0.09s**, and `tests/unit/test_research01_views.py` asserts the view returns
**identical** sessions to `price_series` — the views are not a second opinion.

`trading_sessions` is a materialised table, not a view, because an exchange
calendar cannot be expressed in SQL. Rebuild it whenever the corpus is extended.

### Fundamentals: a backfill with holes, and two ways to misread it

`security_fundamental_facts` holds 92,022,159 rows and both of these will bite.

**It is not point-in-time before roughly 2013.** `knowledge_time` begins in
**2009** and the median gap from `period_end` to `knowledge_time` is **1,574
days**, with 97.6% exceeding 400. SEC's XBRL datasets start around 2009 and
restate history, so the recorded instant is when this corpus learned a fact,
not when the market could have. Securities with an annual figure both knowable
at the session and fresh (period end within two years):

| session | securities |
|---|---|
| 2005-06-30 | **0** |
| 2010-06-30 | 409 |
| 2013-06-30 | 7,584 |

**A study that ignores `knowledge_time` reads a 2016 restatement into a 2013
decision**, and one that respects it finds nothing at all before 2013.

**2,664,473 rows (2.895%) have a NULL `value`.** They cluster in concepts a
filing tags whether or not there is an amount:

```
446,687  CommitmentsAndContingencies
156,082  PreferredStockValue
 79,879  IncomeTaxExpenseBenefit
 42,707  Revenues
```

**NULL is not zero.** Reading one as zero puts a company with unknown equity at
the bottom of every quality ranking, and `float(None)` will simply kill a long
job — it ended a 32-minute run that a 30-security staging pass had not
contained a single NULL to reveal.

### `filings` holds company-filed forms only, and that is deliberate

**8,260,531 filings**, up from 3,528,865 on 2026-09-10. The addition came
entirely from the EDGAR full-index already on disk — 129 quarters, 1994 to
2026, 27,084,670 rows — of which the corpus had ingested 11.4%.

Two rules decide what is in this table, and both change what a query means.

**Ownership forms are excluded.** EDGAR lists a filing once *per filer*, and an
ownership form has two: the reporting owner and the subject company. A Form 4
filed by Bank of Nova Scotia about Foamex International appears in the index
under **both** CIKs, and `filings` has `UNIQUE (accession)` — one row, one
issuer. The corpus resolves such filings to the **subject**, because "filings
about company X" is what a research query means. The index cannot say which of
the two CIKs is the subject, so Forms 3/4/5, SC 13*, SC 14*, 13F and 144 are
**not ingested from the index at all**. Those already present came from other
sources and are attributed to the subject.

**So do not count Form 4s here and conclude anything about insider activity.**
The 396,721 Form 4 rows are whatever earlier work loaded, not a census.

**Ambiguous attributions are skipped, not guessed.** Even among company-filed
forms, 471,673 accessions appeared under more than one tracked CIK and were
left out. A wrong issuer is read as evidence; a missing filing is read as
absence, and absence is the safer error.

**Filings exist for 40,818 tracked CIKs only.** 12,518,837 index rows belong to
filers this corpus does not track and were skipped. This is not an EDGAR
mirror.

### Sectors: `issuer_sic_observations`, and why the earliest filing is the wrong one

**12,871 issuers carry a SIC classification**, covering **13,327 of 14,257
priced securities (93.5%)**. It is the only sector classification in this
corpus, it came from SEC filing headers rather than a vendor, and it is
point-in-time.

```sql
-- the classification in force for an issuer on a given date
select sic_code, division, observed_on, accession
from issuer_sic_observations
where issuer_id = ? and observed_on <= ?
order by observed_on desc, knowledge_time desc limit 1;
```

**Observations, not labels.** Each row is one filing's statement of the code,
carrying the filing date and the accession that said it. A company's SIC
changes, so "what sector is this?" and "what sector was this in 2008?" are
different questions and only the second matters to a backtest.

**Descriptions are usually empty, and that is correct.** The `.hdr.sgml` header
states `<ASSIGNED-SIC>3571` and nothing more. The older tab-delimited headers
carry the SEC's wording; the modern SGML ones do not. Filling the gap from a
lookup table of our own would be a different claim wearing the same field.

**A registration statement predates classification.** The first fetch took each
issuer's *earliest* filing, on the reasoning that it gives the longest span
over which the observation is the best available answer. For 1,283 issuers it
returned nothing at all — and the cause was not throttling or bad parsing but
the SEC: the earliest filing is an **S-1, SB-2, 10SB12G or REGDEX**, filed
before a SIC code had been assigned. Re-fetching those issuers' *latest*
filings recovered 1,214 of the 1,283.

So: **the earliest filing is the wrong place to ask, for any issuer whose
earliest filing is a registration statement.** If you extend this table, fetch a
periodic report.

**69 issuers have no SIC in any filing checked.** That is a real absence, not a
gap in the fetch, and it should be read as `UNRESOLVED` rather than filled.

### Some bars are priced at zero, and they are not prices

**11,580 raw bars across 248 securities have an open, high, low and close of
exactly 0.000000.** They cluster at the *end* of a security's series — the
vendor emitting placeholder rows after a stock stops trading — and at least one
carries a non-zero volume (110 shares at a price of zero, which nobody traded).

They are 0.0327% of raw bars and heavily skewed late: **324 before 2010, and
over 11,000 after**, with the largest counts in 2017–2020 and 2023–2025.

```sql
select count(*) from security_price_facts
where adjustment_basis = 'raw' and (open<=0 or high<=0 or low<=0 or close<=0);
```

**Why it matters more than the count suggests.** Treating one as a real print
books a **−100% return** on a session nobody traded, and because they sit at
the end of a series they land exactly where a survivorship study is most
sensitive. `OhlcvBar` rejects them — prices must be positive — which is how
they were found, but `price_series` returns a plain dataclass and will hand
them to you unvalidated. `CorpusSessionData` excludes and counts them
separately from its other exclusions.

### A view that was named wrong, and what it means

`v_registrants` was called **`v_dead_registrants`**, and its comment said it
returned *"every registrant EDGAR shows exiting"*. It never filtered to exits.
Measured: **40,818 rows — every issuer carrying a CIK**, which is the whole
table. Anyone who trusted the name would have taken a *coverage* denominator
for a *survivorship* one, and reported a much healthier survivorship picture
than the corpus supports.

`issuers` has no lifecycle column at all — only `issuer_id`, `display_name`,
`note`, `ingested_at` and `source`, and `source` records how a registrant was
*seeded* (`edgar_full_index`, `sec_fsds_sub`), not whether it died. The exit
population is derived from filing evidence and belongs with the denominator
work, not with a view over `issuers`.

**If you want the companies that stopped trading, ask the prices, not the
registrants.** A security whose raw series ends before your window does is a
measured fact and needs no inference:

```sql
select security_id, min(session_date) first, max(session_date) last, count(*) bars
from security_price_facts where adjustment_basis = 'raw' group by security_id;
```

That scan takes a few minutes and is worth caching. Of 14,257 priced
securities, **4,306 were tradeable in January 2000 with at least 250 bars, and
1,737 of those — 40.3% — stopped printing before 2010.**

### `corpus_readme` — the guide book, inside the file

A view fixes the traps expressible as SQL. The rest — that names are
point-in-time, that a fetch "failure" often means the system refused to guess,
that **no result computed here is evidence of profitability** — can only be
*stated*, and a statement in a markdown file beside the database helps nobody
who was handed only the database.

```sql
select topic, applies_to, guidance from corpus_readme order by ordinal;
```

Ten entries, **ordered by how badly the mistake hurts** rather than
alphabetically, so a reader who takes only the first row has taken the one that
matters. It is generated from the same constants in `tradeit.research01.views`
that build the views, dropped and rebuilt on every run, and
`tests/unit/test_research01_readme.py` asserts that every view the module
creates is mentioned in it and that every `applies_to` names an object that
actually exists — a guide pointing at a table that is not there is worse than
no guide.

Rebuild both halves with `scripts/research01_cleanup.py`, or
`prepare_corpus(session)` in code.

### Reading the corpus for a backtest

`tradeit.backtesting.corpus.CorpusSessionData` is the supported path, and three
of its choices are reading rules in their own right.

**Use `raw`, not `total`.** Today's split-adjusted history for a stock that
split last year is not history anybody could have traded: the adjustment factor
comes from a split that had not happened, so every earlier bar carries future
information. A backtester trades the print and changes the share count when a
split arrives — `security_corporate_action_facts` is what tells it to.

**The price index cannot serve a cross-section.** `ix_security_price_pit` leads
with `security_id`, so "one session, every security" — exactly what a
backtester wants — reports `SCAN` over 71 million rows. Declare a universe and
read it per security instead: **62,900 bars for 50 securities over five years
in 0.52 seconds**, measured. This is the same shape as the fundamentals problem
migration `0016` fixed, and it has not been fixed here because a declared
universe is the right way to specify a backtest anyway.

**`knowledge_time` currently equals the session close on every raw bar.**
Measured across 116,262 raw bars in a fourteen-security sample: all of them,
with none learned late. A point-in-time bound therefore drops nothing today. It
still belongs in any backtest read, because the corpus is growing and a
correction backfilled to a 2015 bar must not become tradeable in 2015 the
moment it lands. Note that the 327,924 multi-revision keys mentioned above did
not fall in that sample — do not conclude from it that the corpus has no
revisions.

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
