# Offline data package format

The format the importer reads. `DATA_REQUIRED.md` says *what* to supply and why;
this says *how it is laid out* and what each manifest field means.

Version 1. The version is recorded in every manifest, and a manifest declaring a
version newer than the importer understands is refused rather than read
optimistically — reading it would risk misinterpreting fields added after the
importer was written.

---

## 1. Layout

A package is a directory containing exactly one `manifest.toml`, plus data
files:

```
my-export/
  manifest.toml
  instruments.csv
  bars.csv.gz
  splits.csv
  fundamentals.csv
```

A `.zip` of that directory works too, with or without a single top-level folder
— the importer looks for the manifest rather than assuming a layout, so a
package built with `zip -r` and one built by a GUI both work.

**Exactly one manifest.** Two would make the package's provenance ambiguous, and
the importer refuses rather than picking. A ZIP whose entries contain absolute
paths or `..` segments is refused whole rather than partially extracted.

Supported file types: `.csv`, `.csv.gz`, `.tsv`, `.tsv.gz`, `.parquet`.

Prefer **`.csv.gz`**. It streams, so a decade of minute bars does not have to fit
in memory. Parquet is columnar and is materialised whole by pandas — a property
of the format rather than a choice — and additionally needs the optional
`pyarrow` package, which is not installed in this environment. The importer says
so by name rather than failing obscurely inside pandas.

---

## 2. The manifest

```toml
format_version = 1
name = "sharadar-daily"          # stable across re-exports of the same dataset
provider = "nasdaq-data-link"
export_date = 2024-06-01         # when the vendor produced it, not when you import
timezone = "America/New_York"    # required; see §2.2
adjustment_policy = "raw_unadjusted"   # required, no default; see §2.1

known_limitations = [
  "no delisted securities before 2010",
]
licence_note = "personal use only, no redistribution"
vendor_dataset = "SHARADAR/SEP"

# Only if your dates are ambiguous. See §2.3.
date_formats = ["%m/%d/%Y"]
decimal_comma = false

[coverage]
start = 2004-01-02
end = 2024-05-31
instruments = 8412               # optional

# Optional. Only for packages assembled from more than one vendor. See §2.6.
[provenance]
price_provider = "twelve_data"
price_representation = "split_adjusted"
dividend_provider = "twelve_data"
split_provider = "fmp"
reconstruction_performed = true
reconstruction_algorithm = "split-inverse-1"
reconstruction_label = "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"
reconstruction_file = "_acquisition/reconstructed_raw_prices.csv.gz"

[[files]]
path = "bars.csv.gz"             # relative to the package root, never absolute
dataset = "daily_bars"
sha256 = "3f5a...64 hex chars"
rows = 21_000_000                # optional; checked if present
columns = { session_date = "date", instrument_id = "ticker_id", close = "close" }
note = "split-adjusted volume, raw prices"
```

**Every top-level key must appear before the first `[table]` header.** This is
TOML, not an oversight: a key written after `[coverage]` becomes
`coverage.<key>` and fails validation pointing at the wrong field. The generated
template gets this right; a hand-edited one may not.

### 2.1 `adjustment_policy` — required, no default

One of:

| Value | Means |
|---|---|
| `raw_unadjusted` | Exchange prints. What this platform stores (ADR-0005). |
| `split_adjusted` | Adjusted for splits only. |
| `total_return_adjusted` | Adjusted for splits and dividends. |
| `unknown` | The exporter does not know. |

There is deliberately no default. Adjusted closes loaded as raw prices describe
a price history nobody could have seen, and the failure is invisible afterwards
— the series looks perfectly plausible. `unknown` is accepted, is reported as a
problem on every import, and blocks the corporate-action checks; what is not
accepted is a package that silently implies one of the other three.

### 2.2 `timezone` — required

The IANA zone your timestamp columns are expressed in. Applied to *naive*
timestamps only, and every such localisation is recorded as a correction on the
row, so the assumption is visible rather than absorbed. Timestamps that carry
their own offset are converted to UTC and the conversion is likewise recorded.

A naive timestamp with no declared zone has no instant, and the row quarantines.

### 2.3 `date_formats` — only if you need it

The importer parses these layouts without being told:

```
%Y-%m-%d   %Y/%m/%d   %Y%m%d   %d-%b-%Y   %d %b %Y   %b %d, %Y
%d-%B-%Y   %B %d, %Y
```

It **refuses** slash-separated numeric dates such as `03/04/2021`, because that
string is March 4th in some countries and April 3rd in others, a file usually
contains both plausible readings across its rows, and no amount of sampling
settles it. Declare `date_formats = ["%m/%d/%Y"]` and the rows import; leave it
out and they quarantine with a message saying exactly this.

That is more friction than a heuristic. It is also the difference between an
import that is right and an import that is right most of the time.

### 2.4 `columns` — the mapping is the interpretation

Maps **our** field name to **your** column name. Fields you do not list are read
by their own name. Fields the dataset contract does not define are refused at
manifest load, so a typo is an error rather than a silently ignored mapping.

The mapping is stored with the import, because it *is* the interpretation: the
same bytes read with `close` pointed at an adjusted-close column produce a
different history, and without the mapping nobody can tell afterwards which
reading happened.

`common_aliases` in the dataset specification lists names vendors typically use.
They are **hints for whoever writes the manifest and are never applied
automatically** — auto-matching is how a column called `close` that holds
adjusted closes gets silently accepted.

### 2.5 `sha256` — verified before a row is read

Lowercase hex, 64 characters. Checked before the importer reads anything, and
the whole import aborts on a mismatch rather than proceeding with part of the
data. The interesting case is not corruption; it is the file that was
regenerated after the manifest was written, and silently accepting that would
attach one dataset's provenance to another's contents.

Regenerate the digests after changing a file. `tradeit data template --force`
will do it.

### 2.6 `[provenance]` — optional, and only when one vendor is not the answer

`provider` at the top level names the package's *primary* source, and that is
enough while one vendor supplied everything. It stops being enough the moment a
package's prices come from one vendor and its split schedule from another,
because the derived raw-price series is then a value **neither vendor
supplied**, and a reader who saw only `provider = "twelve_data"` would attribute
it to Twelve Data.

| Key | Means |
|---|---|
| `price_provider` | Who supplied the OHLCV in the package's price columns. |
| `price_representation` | What those columns contain; echoes `adjustment_policy`. |
| `dividend_provider` | Who supplied `dividends`. |
| `split_provider` | Who supplied `splits`. |
| `reconstruction_performed` | Whether a raw series was derived by inverting a split adjustment. |
| `reconstruction_algorithm` | Version of that arithmetic. |
| `reconstruction_label` | The label those derived values carry. Never a vendor's name. |
| `reconstruction_file` | Where they live, relative to the package root. |

Every key is optional and empty by default. **An absent key means "not
recorded", never "not applicable"** — a package written before this table
existed says nothing here, and inferring a split provider from the price
provider is exactly the mistake the table exists to prevent.

The reconstruction file is deliberately inside `_acquisition/` and deliberately
**not** a declared `[[files]]` entry. It is DERIVED, and the importer must never
read it as prices.

Written automatically by `tradeit data acquire` and updated by
`tradeit data enrich`. A hand-assembled package may omit it entirely.

---

## 3. The pipeline a row passes through

Four stages, kept as separate objects so that "what did the file actually say
here?" is answerable at any depth:

| Stage | What it is | Failure means |
|---|---|---|
| **raw** | Exactly what the file said, as text. Never modified. | — |
| **normalized** | Columns renamed to the contract, text coerced to types. Every deviation recorded as a correction. | Your date format needs declaring, or a cell is not a number. |
| **validated** | Internal consistency — high above low, volume non-negative, intervals not inverted. | The vendor's own numbers contradict each other. |
| **point_in_time** | A `knowledge_time` attached, with its provenance. | Nothing in the package says when the fact became knowable. |

The stage is stored on every quarantined row, because knowing a row died is not
the same as knowing where to look: three stages, three different fixes, three
different people.

### 3.1 Flag or quarantine?

The dividing question is: *can any correct consumer use this row?*

**Quarantined** (unusable by anyone): a bar whose high is below its low; a
non-positive price; an inverted date interval; a dividend paid before its
ex-date; a duplicate row claiming an identity another row already claims.

**Flagged and imported** (might be true): zero volume — halted stocks, thin
ETFs and holiday half-sessions produce genuine zero-volume bars, and dropping
them creates a gap that looks exactly like a missing file. A four-fold overnight
move — real markets do this, and a rule that discarded them would quietly delete
the most informative sessions in the sample. An open, high, low and close that
are all equal.

"It looks wrong to me" is not on either list. That judgement belongs to whoever
is looking at the numbers.

### 3.2 The abort threshold

An import whose quarantine rate exceeds 5% stops, after a minimum sample of 500
rows. A file where one row in three is unreadable is a mapping mistake, not a
data-quality problem, and grinding through ten million of them to produce a
report nobody reads wastes an afternoon. The threshold is stated in the report
either way.

---

## 4. Knowledge time: how each dataset is placed in time

Three routes, in strict order of preference:

1. **Reported** — the package supplied a filing or publication timestamp. The
   only route that is evidence rather than assumption.
2. **Structural** — the fact's own event defines when it existed. A daily bar is
   knowable at its session close (16:00 America/New_York by default).
3. **Estimated** — a regulatory-deadline lag applied to the period end. **Off by
   default**; every affected row is marked `ESTIMATED` and counted.

Reference datasets — the instrument master, exchanges — carry no date of their
own and are placed at the package's export date. That is honest and it is not
free, and the import report says so once per dataset: an instrument master
exported today lists today's roster with today's classification, so a company
reclassified in 2018 appears under its 2024 sector for its entire history. That
is why `sectors` and `symbol_mappings` are interval-based datasets rather than
columns on the instrument master.

**A fundamental fact is never placed at its own period end, under any
configuration.** See `DATA_REQUIRED.md`.

---

## 5. Commands

```bash
# What to buy or export, as a document.
tradeit data package-spec --output DATA_REQUIRED.md

# Which datasets exist and what each unlocks.
tradeit data datasets

# A manifest to fill in, with digests already computed.
tradeit data template ./my-export --name my-export --provider some-vendor

# Verify digests and describe the package without reading a row.
tradeit data inspect ./my-export

# Execute every stage, decide every quarantine, write nothing.
tradeit data import ./my-export --dry-run
tradeit data import ./my-export --dry-run --jsonl ./out   # inspect the output

# Do it.
tradeit data import ./my-export

# Then:
tradeit validate --snapshot <snapshot-id-printed-above>
```

`tradeit data ...` needs a configured database only for a real import; the
spec, datasets, template, inspect and dry-run paths touch no database. If your
environment has no PostgreSQL configured, invoke them as
`python -m tradeit.cli_data data ...`, which does not load the platform
settings.

Useful flags on `import`:

| Flag | Effect |
|---|---|
| `--dry-run` | Every stage runs; nothing is written. |
| `--jsonl DIR` | With `--dry-run`, write dated records, quarantine and corrections as JSONL. |
| `--limit N` | Stop after N rows per file. **Marks the snapshot partial**, which disqualifies it as evidence. |
| `--only daily_bars ...` | Import a subset of datasets. |
| `--estimate-filing-dates` | Accept deadline-based timestamps for fundamentals with no publication instant. Every row so dated is marked `ESTIMATED` and counted. |
| `--skip-digests` | Do not re-hash. Recorded in the report. Leave it off. |

---

## 6. Snapshot identity

Every import produces a `snapshot_id`, derived from the manifest digest — which
already covers every file's SHA-256 — and the observed per-dataset row counts.
Two imports of the same bytes produce the same id; an import of altered bytes
cannot. A run that aborted or was row-limited carries a `-partial` suffix and is
refused as evidence by the validation harness.

The id is what a validation result cites, and it resolves to a `data_packages`
row carrying the manifest digest, the declared adjustment policy, the declared
coverage next to the coverage actually observed, and the full import report.
That chain is what makes "which bytes produced this number?" a query rather than
an investigation.
