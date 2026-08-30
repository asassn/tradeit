# DATA_REQUIRED — what to supply, and what each file buys

**Audience:** whoever is going to obtain the data. **Purpose:** turn "we need
market data" into a shopping list precise enough to hand a vendor, and to say
plainly what is lost by not supplying each piece.

Generated in part from `tradeit.data.packages.spec`, which is the code the
importer actually uses. If this document and the importer ever disagree, the
importer is right and this file is stale — regenerate it with:

```
tradeit data package-spec --output docs/DATA_REQUIRED.md
```

---

## The short version

If you supply exactly one thing, supply **daily bars with an instrument
master**, unadjusted, including securities that no longer trade. That single
combination unlocks pattern detection, breakout detection, indicators,
multi-timeframe construction and point-in-time replay — the four phases already
built — and its absence is why none of them has been measured on real data.

If you supply a second thing, supply **splits and dividends**. Without them the
corporate-action checks cannot run, and those checks exist to find the
artefacts that make a 10-for-1 split look like a 90% collapse.

If you supply a third, supply **fundamentals with filing timestamps**. Not
fundamentals alone — fundamentals *with the instant each figure became public*.
Fundamentals without that are close to useless for this platform, for the
reason in the next section.

---

## The one requirement that is not negotiable

**A quarter ending 31 March was not knowable on 31 March.**

The company had not closed its books, no filing existed, and nobody outside the
finance department could have acted on the number. Treating a period-end date as
an availability date is the single most common way a backtest invents profit: it
hands the strategy every earnings surprise weeks before the market saw it, and
the resulting equity curve is not merely optimistic — it describes a different
universe.

So the importer will not do it. There is no configuration flag that relaxes it.
Concretely:

- A fundamental row with a filing timestamp **dated within a day of its own
  period end** is refused, because a report cannot be filed before the period it
  reports has closed and been compiled.
- A fundamental row with **no** publication timestamp is **quarantined by
  default**. You may instead accept a filing-deadline estimate — 45 days after
  period end for a quarterly report, 90 for an annual — by passing
  `--estimate-filing-dates`. Every affected row is then marked `ESTIMATED`,
  counted per dataset, and named in every report, because a result computed from
  those rows is partly a measurement of the lag rule rather than of the market.
- The estimated lags are **filing deadlines, not typical filing dates**. Most
  issuers file earlier, so the deadline places facts *later* than they were truly
  knowable. That direction is deliberate: it costs a strategy opportunities it
  might really have had, and never grants it one it did not.

If a vendor's fundamentals product cannot tell you when a figure was published,
that is the product's answer to the only question that matters here.

---

## Formats and layout

A **package** is a directory (or a `.zip` of one) containing exactly one
`manifest.toml` plus data files. Supported file types: `.csv`, `.csv.gz`,
`.tsv`, `.tsv.gz`, and `.parquet` (Parquet additionally requires `pyarrow`,
which is not installed here — CSV.gz is the safer choice, and it streams, which
Parquet does not).

The manifest maps **your** column names onto the field names below. The importer
never guesses: a column called `close` that actually holds adjusted closes would
otherwise be accepted silently and produce a price history nobody could have
seen. Generate a starter manifest with:

```
tradeit data template /path/to/package --name my-export --provider some-vendor
```

and fill in every `PLEASE_SET`. They are deliberately left unparseable so that
an unfilled template cannot be imported by accident.

Three manifest fields deserve attention:

- **`adjustment_policy`** — required, no default. One of `raw_unadjusted`,
  `split_adjusted`, `total_return_adjusted`, `unknown`. Declaring `unknown` is
  permitted and blocks the corporate-action checks; guessing is not permitted.
- **`timezone`** — required. A timestamp column with no declared zone is a
  session boundary nobody can reconstruct.
- **`date_formats`** — needed only if your dates look like `03/04/2021`. That
  string is March 4th in some countries and April 3rd in others, and no amount
  of sampling settles it, so the importer refuses to guess and every such row
  quarantines until you declare the layout.

---

## What the importer guarantees about your files

- **Your files are never modified.** Every file's SHA-256 is verified before a
  single row is read, and the digest is stored with the import.
- **No row is ever discarded.** Every line is either imported or quarantined
  with its file, its line number, the pipeline stage that rejected it, and the
  reason. The counts are asserted to sum.
- **Every change is recorded.** Stripping a currency symbol, dropping a
  thousands separator, localising a naive timestamp — each is stored as a
  correction naming the field, the raw text, the substituted value, the rule and
  why. A row with no corrections attached provably changed nothing.
- **A dry run makes every decision and writes nothing**:
  `tradeit data import /path/to/package --dry-run`.

---

## The datasets

### instruments

One row per security, including securities that no longer trade.

*Why it matters.* Without the delisted and acquired names, every historical result is computed over survivors only — which is the single most flattering mistake a backtest can make.

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | stable surrogate key; never a ticker | — |
| `name` | string | yes | company or fund name | `security_name`, `longname` |
| `primary_exchange` | string | yes | MIC or exchange code | `exchange`, `mic` |
| `asset_class` | string | yes | common_stock / etf / adr / reit / ... | — |
| `country` | string | no | ISO-3166 alpha-2; defaults to US | — |
| `currency` | string | no | ISO-4217; defaults to USD | — |
| `first_trade_date` | date | no | first session the security traded | — |
| `listing_status` | string | no | active / delisted / acquired / ... | — |
| `delisted_date` | date | no | last session; required when not active | — |
| `figi` | string | no | OpenFIGI identifier, if the vendor supplies one | — |
| `cik` | string | no | SEC CIK, needed to join filings | — |

Enables: instrument reference resolution, survivorship-safe universe reads

### symbol_mappings

Ticker-to-instrument bindings over half-open date intervals.

*Why it matters.* Tickers are recycled. A screen keyed on the string will splice two unrelated price histories together and never say so.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | the instrument this ticker referred to | — |
| `ticker` | string | yes | the symbol as printed | `symbol` |
| `valid_from` | date | yes | first session inclusive | `start_date` |
| `valid_to` | date | no | first session it no longer applied, exclusive | — |

Enables: symbol-change handling, ticker-reuse detection

### exchanges

Exchange codes, names and timezones.

*Why it matters.* Session boundaries and half-days depend on which exchange a security trades on.

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `code` | string | yes | MIC or vendor exchange code | — |
| `name` | string | no | human-readable name | — |
| `timezone` | string | no | IANA timezone, e.g. America/New_York | — |

Enables: calendar selection

### daily_bars

Daily OHLCV. The one dataset almost every validation needs.

*Why it matters.* Everything downstream is built on these. The adjustment policy on the manifest is not optional metadata: adjusted closes loaded as raw prices describe a history nobody could have seen.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key, not a ticker | — |
| `session_date` | date | yes | exchange-local trading date | `date`, `day` |
| `open` | decimal | yes | session open | `o`, `open_price` |
| `high` | decimal | yes | session high | `h` |
| `low` | decimal | yes | session low | `l` |
| `close` | decimal | yes | session close | `c`, `close_price` |
| `volume` | decimal | yes | shares traded | `v`, `vol` |
| `vwap` | decimal | no | volume-weighted average price, if supplied | — |
| `trade_count` | int | no | number of trades, if supplied | — |
| `knowledge_time` | datetime | no | when the bar became available; defaults to close + vendor lag | — |

Enables: OHLCV ingestion checks, trading-calendar checks, missing-bar handling, indicator calculations, multi-timeframe construction, volatility regime, pattern detection, breakout detection, point-in-time replay

### intraday_bars

Intraday OHLCV at a stated bar width.

*Why it matters.* The only dataset that can validate the intraday volume projection, which is the place a partial session is most easily mistaken for a complete one.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `timestamp` | datetime | yes | bar start, timezone-aware | `datetime`, `ts` |
| `timeframe` | string | yes | 1m / 5m / 15m / 30m / 1h / 4h | — |
| `open` | decimal | yes | bar open | — |
| `high` | decimal | yes | bar high | — |
| `low` | decimal | yes | bar low | — |
| `close` | decimal | yes | bar close | — |
| `volume` | decimal | yes | shares traded in the bar | — |

Enables: intraday volume-curve construction, intraday relative-volume validation, intraday timeframe aggregation

### splits

Share splits and reverse splits with their ex-dates.

*Why it matters.* A 4-for-1 split looks exactly like a 75% crash to any indicator that has not been told about it.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `ex_date` | date | yes | first session trading at the new count | — |
| `ratio` | decimal | yes | share-count multiplier: 2-for-1 is 2, 1-for-10 is 0.1 | — |
| `numerator` | int | no | new shares, as the vendor stated it: 4 in a 4-for-1 | — |
| `denominator` | int | no | old shares: 1 in a 4-for-1, 10 in a 1-for-10 | — |
| `split_type` | string | no | the vendor's own label, e.g. stock_split | — |
| `source_provider` | string | no | which vendor supplied this record, when it is not the vendor that supplied the prices | — |
| `vendor_factor` | decimal | no | the single number the vendor sent, before normalization | — |
| `vendor_convention` | string | no | how vendor_factor was read: share_count_multiplier, price_adjustment_multiplier, or new_over_old_shares | — |
| `announcement_time` | datetime | no | when the split was announced; normally before the ex-date | — |

Enables: corporate-action artefact detection, split-adjusted price reconstruction, ATR and momentum artefact checks

**`ratio` is always the share-count multiplier** — 4 for a 4-for-1, 0.125 for a
1-for-8 reverse — whatever your vendor's own convention is. This is the field
the reconstruction arithmetic reads, and getting it backwards inverts every
price before the event while leaving a series that looks perfectly plausible.

Vendors disagree about which end to measure from, and **the number cannot settle
it**: `0.25` is the price-adjustment factor of a 4-for-1 forward split and the
share-count multiplier of a 1-for-4 reverse split. So supply `vendor_factor` and
`vendor_convention` alongside the canonical `ratio` if your source gives a
single factor. See `docs/VENDOR_SEMANTICS.md` for the two live cases that made
this necessary.

`numerator` and `denominator` are kept alongside the derived `ratio` rather than
replaced by it, and are the **preferred** form: an explicit pair carries its own
direction, which no single factor does. When somebody later disputes the
direction of a reverse split, the vendor's own pair is what the argument gets
settled against.

`announcement_time` is the field almost nobody can fill. The split endpoints
this project can reach carry an **effective** date only, and an effective date
presented as an announcement would license research the data cannot support.
Leave it empty rather than deriving it.

### dividends

Cash distributions with their ex-dates.

*Why it matters.* A large special dividend produces a gap that is not a market move.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `ex_date` | date | yes | first session trading without the dividend | — |
| `cash_amount` | decimal | yes | per pre-action share, in the listing currency | — |
| `announcement_time` | datetime | no | declaration timestamp | — |

Enables: dividend gap attribution, total-return reconstruction

### corporate_actions

Spin-offs, rights issues, symbol changes and anything else.

*Why it matters.* The events that are neither a split nor a dividend still move the print.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `action_type` | string | yes | spinoff / rights_issue / symbol_change / ... | — |
| `ex_date` | date | yes | first session reflecting the action | — |
| `ratio` | decimal | no | share-count multiplier where applicable | — |
| `cash_amount` | decimal | no | per-share cash where applicable | — |
| `new_ticker` | string | no | required for a symbol change | — |
| `announcement_time` | datetime | no | declaration timestamp | — |

Enables: full corporate-action replay

### delistings

When and why a security stopped trading.

*Why it matters.* A series that simply stops is indistinguishable from a data gap. Without this, the two are conflated and every quality check on missing bars becomes noise.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `delisted_date` | date | yes | last session the security traded | — |
| `reason` | string | no | delisted / acquired / merged / bankrupt / suspended | — |

Enables: delisting-gap discrimination, survivorship-safe universe reads

### sectors

Sector and industry classification over date intervals.

*Why it matters.* Classifications change. Using today's mapping for all history manufactures a sector membership nobody could have known.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `scheme` | string | yes | GICS / ICB / vendor scheme name | — |
| `sector` | string | yes | sector label | — |
| `industry` | string | no | industry label | — |
| `valid_from` | date | yes | first session this classification applied | — |
| `valid_to` | date | no | first session it no longer applied, exclusive | — |

Enables: sector strength, sector participation, sector rotation context

### universe_membership

Which instruments were in a named universe over which intervals.

*Why it matters.* The survivorship control. A screen run as of 2015 must see the companies that were listed in 2015, including the ones that later went to zero.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `universe` | string | yes | universe name, e.g. sp500 | — |
| `instrument_id` | int | yes | surrogate key | — |
| `valid_from` | date | yes | first session of membership | — |
| `valid_to` | date | no | first session after membership, exclusive | — |
| `exit_reason` | string | no | why it left | — |

Enables: cross-sectional ranking, market breadth, market regime breadth terms, survivorship-safe screening

### earnings

Earnings dates, with the timestamp at which each became known.

*Why it matters.* A scheduled date announced in March cannot be visible to a screen run in January. The announcement timestamp is the gate, not the scheduled date.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `scheduled_date` | date | yes | the session the report falls on | — |
| `fiscal_period` | string | yes | Q1 / Q2 / Q3 / Q4 / FY | — |
| `fiscal_year` | int | yes | fiscal year | — |
| `announced_time` | datetime | no | when this date became public; NOT the scheduled date. Absent, the importer assumes the event was knowable only on the day it occurred, which is deliberately conservative: a proximity filter then sees fewer upcoming events than a live system would, never more. | `announcement_time`, `announced_at` |
| `period_end` | date | no | last day of the fiscal period, if supplied | — |
| `session_hint` | string | no | bmo / amc / during | — |
| `is_confirmed` | bool | no | company-confirmed rather than vendor estimate | — |
| `eps_actual` | decimal | no | reported EPS, once reported | — |
| `eps_estimate` | decimal | no | consensus estimate at announcement | — |

Enables: earnings-gap context, earnings proximity flags

### fundamentals

Reported financial facts, one metric per row, as filed.

*Why it matters.* The dataset where a single shortcut invalidates everything built on it: a quarter ending 31 March does not become available on 31 March.

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `metric` | string | yes | line-item name, e.g. revenue, net_income | — |
| `fiscal_period` | string | yes | Q1 / Q2 / Q3 / Q4 / FY / TTM | — |
| `fiscal_year` | int | yes | fiscal year | — |
| `period_end` | date | yes | last day of the fiscal period | — |
| `value` | decimal | no | the reported value; may be null | — |
| `unit` | string | no | USD / shares / ratio; defaults to USD | — |
| `filing_timestamp` | datetime | no | when this fact became public. NOT period_end, and never equal to it. Optional in the column contract but not optional in effect: with no filing timestamp anywhere in the package, the importer's default is to quarantine the row rather than guess when it was knowable. An operator may instead accept a filing-deadline estimate, which marks every affected row ESTIMATED and counts them in the report — see KnowledgeTimePolicy.require_reported_fundamentals. | `filed_at`, `filing_date`, `accepted_at` |
| `is_restatement` | bool | no | true when this supersedes an earlier filing | — |
| `restates_period_end` | date | no | the period this restates, if any | — |
| `accession` | string | no | filing identifier, e.g. SEC accession number | — |

Enables: point-in-time fundamental reads, Phase 6 fundamental scoring, restatement handling

### filings

Filing metadata, when statements and timestamps come from separate sources.

*Why it matters.* If the fundamentals export has no filing timestamp, this dataset is how one is attached. Joined on accession or on (instrument, period_end, form_type).

Requires: instruments

| Column | Type | Required | Meaning | Common vendor names |
| --- | --- | --- | --- | --- |
| `instrument_id` | int | yes | surrogate key | — |
| `accession` | string | no | filing identifier; the preferred join key | — |
| `form_type` | string | yes | 10-K / 10-Q / 8-K / 20-F / ... | — |
| `period_end` | date | no | fiscal period the filing covers | — |
| `filed_at` | datetime | yes | public availability timestamp | — |
| `accepted_at` | datetime | no | acceptance timestamp where distinct | — |

Enables: filing-timestamp linkage, point-in-time fundamental reads

## Point-in-time requirements

```
Knowledge-time policy
  market facts   : session close 16:00:00 America/New_York (no vendor lag added)
  quarterly facts: period_end + 45d when no filing timestamp is supplied (ESTIMATED)
  annual facts   : period_end + 90d when no filing timestamp is supplied (ESTIMATED)
  reported floor : a filing may not be dated within 1d of its own period end
  strict mode    : on — fundamentals without a filing timestamp are quarantined
```

A quarter ending 31 March was not knowable on 31 March. Supply a filing
or publication timestamp for every fundamental fact, or the importer
quarantines them rather than guessing when they became available.

---

## Priority order, if you are buying rather than exporting

1. **Daily bars, unadjusted, with delisted securities** — unlocks four built
   phases. Without the delisted names every rate is computed over survivors,
   which is the most flattering mistake available and grows with lookback.
2. **Instrument master + symbol mappings** — tickers are recycled; without the
   mapping intervals a screen keyed on the string splices two unrelated price
   histories together and never says so.
3. **Splits and dividends** — the corporate-action checks exist to find the
   artefacts adjustment removes, and cannot run on an already-adjusted series.
4. **Fundamentals with filing timestamps** — see the non-negotiable section.
   Fundamentals without them are a much smaller purchase than they appear.
5. **Universe membership intervals** — survivorship-safe screening and market
   breadth. Breadth computed over today's roster for 2008 is not breadth.
6. **Sector classifications with validity intervals** — a company reclassified
   in 2018 was in its old sector in 2017, and a rotation study using today's
   mapping for all history measures a different strategy.
7. **Intraday bars** — only needed for the intraday volume-curve work. The daily
   path does not depend on them.

---

## Coverage: how much history, and which

Longer is better, but *which* years matters more than how many. A ten-year
window ending today contains one drawdown and one regime. The periods that
change conclusions are the ones where the market behaved unlike the recent past:
2000–2002, 2008–2009, 2020, 2022. A package that spans 2004 to the present
covers three distinct regimes and is worth considerably more than fifteen years
of the most recent fifteen.

State the window you actually have in the manifest's `coverage` block. The
importer compares it against the data and reports the difference, because a
manifest claiming 2004–2024 over a file that stops in 2019 is not a rounding
error — it is the difference between a validation that covered a bear market and
one that did not.

---

## What supplying all of this still will not tell you

Nothing in this gate measures whether any of it makes money. No future returns,
no win rate, no expectancy, no CAGR, no Sharpe, no drawdown, no profit. Those
wait for a later phase, run once, against data nobody has been tuning against.

The checks this data unlocks answer a narrower and more useful question first:
*does the platform behave on real markets the way it behaves on the synthetic
series it was built against?* If the answer is no, every performance number
computed later would have been measuring the discrepancy.
