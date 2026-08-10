# Downloading real market data — a step-by-step guide

**Who this is for:** you, on your own computer, with an internet connection.
**Why it exists:** the environment Claude builds in cannot reach any market-data
provider, so real data has to be downloaded by you and then handed to the
project. This guide is the whole procedure.

**No Python knowledge assumed.** Every command is written out in full. If
something goes wrong, [Common errors](#common-errors) at the bottom lists what
each message means and what to do about it.

Roughly 20 minutes of your attention, plus 1–2 hours of the computer downloading
while you do something else.

---

## Which provider?

Two price providers work today. **Use Twelve Data for the first empirical
package** — it is the one that has been verified against the live API.

| | **Twelve Data** | **Tiingo** |
|---|---|---|
| Environment variable | `TWELVE_DATA_API_KEY` | `TIINGO_API_KEY` |
| Daily prices | **split-adjusted** | raw exchange prints |
| Splits / dividends | separate endpoints, may need a paid plan | included with the prices |
| Limits | credit-based, per-minute and per-day | requests per hour |
| Multi-day download | yes, resumes after the daily quota resets | not usually needed |

The **split-adjusted** row is the one that matters and is explained in
[The split-adjusted caveat](#7-the-split-adjusted-caveat-twelve-data-only). It
does not make Twelve Data worse; it makes it different, and the tool records the
difference rather than papering over it.

### And one *corporate-action source*: FMP

Twelve Data's `/splits` endpoint is not on the free plan. Without a split
schedule the raw exchange prices cannot be recovered from the split-adjusted
ones, so the tool marks the reconstruction "not attempted" and says why.

**FMP** (Financial Modeling Prep) serves a split history on a free account, so
the missing piece is obtainable from a second vendor.

| | **FMP** |
|---|---|
| Environment variable | `FMP_API_KEY` |
| What it is asked for | **historical stock splits, and nothing else** |
| What it is never asked for | prices, dividends, fundamentals, ratios, earnings, estimates, statements, ownership |
| Endpoint used | `/stable/splits` — the one and only |
| How you use it | `tradeit data enrich`, or `--split-provider fmp` on `acquire` |

FMP **cannot** be selected with `--provider`. That is structural, not a
convention: it implements a different, much smaller interface that has no way to
produce a price bar. A package whose prices quietly came from a different vendor
than its manifest says would not be detectable by inspection afterwards.

## Contents

1. [What you need first](#1-what-you-need-first)
2. [Get an API key](#2-get-an-api-key)
3. [Set the key so the tool can find it](#3-set-the-key-so-the-tool-can-find-it)
4. [Run the smoke test — two symbols](#4-run-the-smoke-test--two-symbols)
5. [Run the full download](#5-run-the-full-download)
6. [What success looks like](#6-what-success-looks-like)
7. [The split-adjusted caveat](#7-the-split-adjusted-caveat-twelve-data-only)
8. [Adding splits from FMP](#8-adding-splits-from-fmp)
9. [What was created, and where](#9-what-was-created-and-where)
10. [Move the package to the project](#10-move-the-package-to-the-project)
11. [Import and validate](#11-import-and-validate)
12. [Common errors](#common-errors)
13. [Resuming and retrying](#resuming-and-retrying)
14. [A note about licensing and git](#a-note-about-licensing-and-git)

---

## 1. What you need first

- **Python 3.11 or newer.** Check by opening a terminal and running:

  ```bash
  python3 --version
  ```

  If that prints `Python 3.11.x` or higher, you are fine. If it says "command
  not found", install Python from [python.org](https://www.python.org/downloads/).

- **A copy of this project.** If you do not have one:

  ```bash
  git clone https://github.com/asassn/tradeit.git
  cd tradeit
  ```

- **The project's dependencies installed.** From inside the project folder:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate        # on Windows: .venv\Scripts\activate
  pip install -e .
  ```

  The `source .venv/bin/activate` line has to be run each time you open a new
  terminal window. You can tell it worked because your prompt gains a `(.venv)`
  prefix.

  After `pip install -e .` you also get a short command, `tradeit-data`, which
  is the same thing as `python -m tradeit.cli_data`. This guide spells out the
  long form everywhere so the commands work even if your shell cannot find the
  short one; use whichever you prefer.

- **About 500 MB of free disk space** for the full download. The tool tells you
  its estimate before it starts, and the real figure when it finishes.

You do **not** need a database for the download itself. A database is only
needed later, for the import step, and that happens wherever the project runs.

---

## 2. Get an API key

### Twelve Data (recommended)

1. Go to [twelvedata.com](https://twelvedata.com/) and create an account.
2. Open your dashboard and find the **API key**.
3. Copy it.

The free plan gives a limited number of **credits** per minute and per day. One
symbol's price history costs one credit, so the ~91-symbol validation universe
costs roughly 270 credits in total (prices, splits and dividends for each). On a
free plan that will very likely take **more than one day**, and that is fine —
the tool stops cleanly when the daily allowance runs out and picks up where it
left off when you run the same command again.

### FMP (recommended alongside Twelve Data — splits only)

1. Go to [financialmodelingprep.com](https://financialmodelingprep.com/) and
   create a free account.
2. Open your dashboard and find the **API key**.
3. Copy it.

The free plan has a **daily** request cap. Enrichment costs one request per
symbol, so the ~91-symbol validation universe is one pass well inside a typical
free daily allowance. There is no documented per-minute figure for the free
tier, so the tool paces itself conservatively at 30 requests/minute by its own
choice — not by quoting a limit it cannot cite — and slows further if FMP ever
returns a 429. Change it with `--split-rate-limit` if you know your plan's real
terms.

### Tiingo (also supported)

1. Go to [tiingo.com](https://www.tiingo.com/) and create an account.
2. Open your account page and find the **API Token** section.
3. Copy the token.

> **Keep the key private.** It is a password. Anyone who has it can use your
> account.
>
> Twelve Data's API takes the key as part of the web address, which means every
> request URL contains a secret. The tool scrubs it out of every file it writes
> — the download log, the cached responses, the error messages — and there is an
> automated test that searches every written file for the key and fails if it
> finds it.

---

## 3. Set the key so the tool can find it

The tool reads the key from an **environment variable** — just a named value
your terminal hands to the programs it runs. The name depends on the provider:
`TWELVE_DATA_API_KEY`, `FMP_API_KEY` or `TIINGO_API_KEY`.

**macOS / Linux** — in the terminal you are going to run the download from:

```bash
export TWELVE_DATA_API_KEY="paste-your-twelve-data-key-here"
export FMP_API_KEY="paste-your-fmp-key-here"
```

(For Tiingo instead, use `export TIINGO_API_KEY="..."`. You can set all three.)

**Windows PowerShell:**

```powershell
$env:TWELVE_DATA_API_KEY = "paste-your-twelve-data-key-here"
$env:FMP_API_KEY = "paste-your-fmp-key-here"
```

**Windows Command Prompt:**

```cmd
set TWELVE_DATA_API_KEY=paste-your-twelve-data-key-here
set FMP_API_KEY=paste-your-fmp-key-here
```

This lasts until you close the terminal window. That is deliberate — a key that
lives only in one terminal session cannot end up in a file you later share.

**Check it worked:**

```bash
python -m tradeit.cli_data data providers
```

You should see:

```
Price providers  (--provider)
  eodhd        stub (see the module docstring)    EODHD_API_KEY=NOT SET
  tiingo       ready                              TIINGO_API_KEY=NOT SET
  twelve_data  ready                              TWELVE_DATA_API_KEY=set

Corporate-action sources  (--split-provider / tradeit data enrich --source)
  fmp          splits only                        FMP_API_KEY=set
```

If your provider or source says `NOT SET`, the `export` did not take effect —
check for a typo, and make sure you are in the same terminal window.

**Do not** put the key in a file inside the project. Do not commit it anywhere.

---

## 4. Run the smoke test — two symbols

Always do this first. It takes about ten seconds and proves the key works, the
network works, and the package format is right — before you spend an hour
downloading.

```bash
python -m tradeit.cli_data data acquire \
    --provider twelve_data \
    --split-provider fmp \
    --symbols AAPL,NVDA \
    --start 2010-01-01 \
    --end 2025-12-31 \
    --output ./empirical-smoke
```

`AAPL` and `NVDA` are chosen deliberately: both split inside that window
(Apple 7-for-1 in 2014 and 4-for-1 in 2020, NVIDIA 4-for-1 in 2021 and 10-for-1
in 2024), so the smoke test exercises the split path rather than only the happy
one. Both also have splits *before* 2010, which must affect nothing.

For Tiingo, swap `--provider twelve_data` for `--provider tiingo` and drop
`--split-provider` — Tiingo's prices are already raw, so there is nothing to
reconstruct.

On Windows PowerShell, use a backtick `` ` `` instead of `\` at the end of each
line, or just put the whole command on one line.

You should see an acquisition summary ending with:

```
Package status       : PACKAGE_VALID
...
Snapshot-ready       : YES
```

immediately followed by an enrichment summary ending with:

```
Status               : ENRICHED
```

If you see both, everything works. **If not**, go to
[Common errors](#common-errors) before continuing — a problem here will only be
bigger on the full download.

**Two lines worth checking by eye**, because they are the ones that would be
wrong silently:

```bash
# 1. Splits came from FMP and kept FMP's own numerator/denominator.
cat ./empirical-smoke/splits.csv

# 2. The manifest says who supplied what.
grep -A 9 '\[provenance\]' ./empirical-smoke/manifest.toml
```

The second should show `price_provider = "twelve_data"`,
`split_provider = "fmp"` and
`reconstruction_label = "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"`.

---

## 5. Run the full download

This gets the whole validation universe — 91 instruments chosen to stress the
system, including delisted names like LEH and BSC that many data sources
silently omit — from 2010 to the last completed trading session.

**First, see how big it will be** (this makes no network requests):

```bash
python -m tradeit.cli_data data acquire \
    --provider twelve_data \
    --start 2010-01-01 \
    --output ./empirical-data \
    --estimate-only
```

**Then run it for real** — drop the `--estimate-only`:

```bash
python -m tradeit.cli_data data acquire \
    --provider twelve_data \
    --start 2010-01-01 \
    --output ./empirical-data
```

Leave it running. Much of the elapsed time is the tool deliberately waiting so
as not to exceed the plan's limits. **You can interrupt it at any time with
Ctrl-C** and re-run the same command later; it picks up where it stopped.

### On a free plan this will probably take more than one day

That is expected and is not a failure. When the daily credit allowance runs out
the tool stops cleanly, writes everything it has, and reports:

```
Package status       : ACQUISITION_INCOMPLETE_QUOTA
```

Wait for the allowance to reset, then **run exactly the same command again**.
Symbols already downloaded are read from disk and cost no credits; only the
missing ones are requested. Repeat until the status is `PACKAGE_VALID` or
`PACKAGE_VALID_WITH_WARNINGS`.

Do not import a package while it is still `ACQUISITION_INCOMPLETE_QUOTA` unless
you specifically want a partial universe — the rows in it are real, but it
covers fewer instruments than you asked for.

### Options you might want

| Option | What it does |
|---|---|
| `--symbols AAPL,MSFT,NVDA` | Download only these instead of the whole universe. |
| `--start 2004-01-01` | Go further back. More history is better if your plan allows it. |
| `--end 2024-12-31` | Stop earlier. Defaults to yesterday. |
| `--rate-limit 500` | Requests or credits per minute, depending on how the provider charges. Raise it only if your plan actually allows more. |
| `--batch-size 4` | Symbols per request (Twelve Data). Fewer means a cheaper retry when something goes wrong; it does **not** reduce credits, which are charged per symbol either way. |
| `--name my-package` | Name recorded in the manifest. Defaults to `tiingo-daily`. |
| `--force-refresh` | Re-download everything, ignoring what is already on disk. |
| `--retry-failed` | Attempt only the instruments that failed last time. |
| `--split-provider fmp` | After acquiring, fetch splits from FMP and reconstruct raw prices. See [section 8](#8-adding-splits-from-fmp). |
| `--split-rate-limit 30` | Requests per minute for `--split-provider`. Self-imposed; raise it only if you know your plan's terms. |

### Why 2010, and why not a "cleaner" list

2010 onward covers materially different market environments — the
post-crisis bull market, 2011's volatility, 2015–16 weakness, the 2018
correction, the 2020 crash and recovery, the 2022 bear market, and what came
after. A shorter window would contain one regime and would make everything look
more consistent than it is. The start date is configurable and was not chosen by
looking at which dates made anything perform well.

The universe deliberately includes awkward names — securities that delisted,
changed ticker, split ten-for-one, or barely trade. Those are the valuable ones:
they exercise the code paths a portfolio of large-cap survivors never touches.
Do not swap them out for a tidier list.

---

## 6. What success looks like

```
ACQUISITION SUMMARY
========================================================================
Package status       : PACKAGE_VALID_WITH_WARNINGS
Provider             : tiingo
Requested instruments: 91
Successful           : 88
Failed               : 3

Daily bars           : 312,482
Splits               : 143
Dividends            : 2,108
Instruments written  : 91

Coverage             : 2010-01-04 through 2026-08-08
Requests fetched     : 182
Requests from cache  : 0

Raw cache            : 214.6 MB in 182 files
Package              : 18.2 MB in 6 files
Manifest             : empirical-data/manifest.toml
Snapshot-ready       : YES
```

*(Illustrative layout. Your numbers will be whatever your download actually
produced — the tool measures them from the files it wrote.)*

**The three possible statuses:**

| Status | Means | Do what? |
|---|---|---|
| `PACKAGE_VALID` | Everything requested arrived. | Continue to step 7. |
| `PACKAGE_VALID_WITH_WARNINGS` | Most arrived; some instruments failed, or some rows looked odd. | **Usually fine — continue.** A few delisted tickers being unavailable costs a few tests, not the package. The failed names are listed. |
| `PACKAGE_INVALID` | Nothing usable was produced. | Do not continue. Read the problems listed and fix them. |

`Snapshot-ready: YES` is the line that matters. It means the package will
import.

---

## 7. The split-adjusted caveat (Twelve Data only)

Worth two minutes, because it is the one thing about this provider that changes
what the data *means*.

**Twelve Data's daily prices are adjusted for stock splits.** When a company
splits 2-for-1, every price before that day is halved so the series looks
continuous. Tiingo's daily prices are the raw exchange prints instead.

Neither is wrong. They are different, and the danger is only in mislabelling
them — a split-adjusted series described as raw looks completely plausible, and
every pattern drawn on it is drawn on a price history nobody could ever have
seen.

So the tool:

- writes `adjustment_policy = "split_adjusted"` into the manifest;
- puts the caveat first in the package's list of known limitations, in capitals;
- and makes the validation harness report a **WARN**, not a pass, when it later
  reads that package.

You do not have to do anything about this. It is recorded, and the rest of the
system knows.

### What "reconstructed raw" is, and why it is kept separate

The raw prices *can* be recovered: multiply each adjusted price by every split
that happened after it. The tool does this and writes the result to

```
empirical-data/_acquisition/reconstructed_raw_prices.csv.gz
```

Every row carries the vendor's original value, the factor applied, which splits
produced it, and the version of the algorithm — so anyone who disagrees with the
method can redo it without downloading anything again.

**These reconstructed values never become the package's prices, and are never
described as vendor data.** They are labelled
`RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED` on every row. The reason for the
caution: the arithmetic is exact, but it depends on the split history being
complete, and a missing split would make every earlier price wrong by that
factor with nothing in the data to reveal it.

If the splits endpoint is not on your plan, the tool does **not** reconstruct
anything and says so. It does not quietly assume "no splits found" means "no
splits happened" — those are different facts, and the summary distinguishes
them. **That is the situation the free Twelve Data plan is actually in**, and
section 8 is how you fix it.

---

## 8. Adding splits from FMP

Twelve Data's `/splits` endpoint answers "not on your plan" for a free account.
Without a split schedule the reconstruction above cannot run, and the tool
correctly refuses to guess.

FMP gives you the schedule. If you passed `--split-provider fmp` to `acquire`,
this already happened and you can skim this section. To run it separately — or
again, later:

```bash
python -m tradeit.cli_data data enrich ./empirical-data --source fmp
```

**It downloads no prices.** It reads the package you already have, asks FMP for
each symbol's split history, writes `splits.csv`, re-derives the reconstructed
raw prices from it, and rewrites the manifest to say who supplied what.

### Why it is a separate command

Because a package on a free plan is usable-but-incomplete for days. The daily
credit allowance runs out, the price download stops cleanly, you resume
tomorrow. The split schedule for the symbols you *already* have is useful right
now, and getting it must not mean re-downloading a single bar. So `enrich` is
the primitive — a pass over an existing package directory, safe to run
repeatedly at any point — and `--split-provider fmp` on `acquire` is a shortcut
that runs exactly the same pass immediately afterwards.

### What it changes in the package

- `splits.csv` gains a row per split, carrying FMP's own `numerator` and
  `denominator` alongside the derived `ratio`, plus `source_provider`,
  `vendor_factor` and `vendor_convention`. The pair is kept because a ratio of
  `0.1` could be 1-for-10 or 2-for-20, and if anyone later disputes the
  direction, the vendor's own numbers are what the argument gets settled
  against.
- `_acquisition/reconstructed_raw_prices.csv.gz` is written, every row labelled
  `RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED` and naming **both** vendors.
- `manifest.toml` gains a `[provenance]` table, and the stale
  "the splits endpoint is not available on this subscription" limitation is
  replaced rather than left to contradict the new one.

### Four things it will not do

- **It will not become the price source.** FMP is asked for `/stable/splits` and
  nothing else — no prices, no fundamentals, no ratios, no earnings, no
  estimates, no statements, no ownership. The top-level `provider` in the
  manifest still says `twelve_data`.
- **It will not call a split date an announcement date.** FMP gives the
  effective (ex-) date. When the split became publicly *knowable* is a different
  fact this endpoint does not carry, so the knowledge timestamp stays
  unavailable and the manifest says announcement-time corporate-action causality
  is not supported by this package. Reconstructing prices does not solve that.
- **It will not resolve a disagreement silently.** If your package already had
  splits and FMP's schedule differs, both facts go in the report and the
  manifest, FMP's schedule is used because you named it on the command line, and
  every difference is listed for you to look at.
- **It will not claim completeness it does not have.** A split FMP does not hold
  leaves every reconstructed price before it wrong by that split's factor, with
  nothing in the data to reveal it. The manifest says so. This is better
  evidence than no split schedule at all; it is not ground truth.

### Options

| Option | What it does |
|---|---|
| `--source fmp` | Which corporate-action source. Only `fmp` today. |
| `--symbols AAPL,NVDA` | Enrich only these. Omit for every symbol in the package. |
| `--split-rate-limit 30` | Requests per minute. Self-imposed, not quoted from FMP. |
| `--force-refresh` | Re-ask FMP even where the cached answer is on disk. |
| `--no-reconstruct` | Fetch and write the split schedule without re-deriving prices. |

Re-running is safe and cheap: answers are cached under `_acquisition/raw/fmp/`,
so a second pass asks FMP nothing at all.

---

## 9. What was created, and where

Inside `./empirical-data` (or whatever you passed to `--output`):

```
empirical-data/
  manifest.toml              <- describes the package; hashes of every file
  daily_bars.csv.gz          <- the price history (the big one)
  instruments.csv            <- one row per security
  symbol_mappings.csv        <- which ticker meant which security, and when
  splits.csv                 <- splits and reverse splits, with source_provider
  dividends.csv              <- cash dividends
  _acquisition/              <- provenance; keep it, do not edit it
    journal.jsonl            <- one line per request made, to any vendor
    acquisition_report.json  <- the full acquisition summary as data
    enrichment_report.json   <- the full enrichment summary as data
    vendor_adjusted_prices.csv.gz     (Tiingo)
    reconstructed_raw_prices.csv.gz   (Twelve Data + FMP — DERIVED, see section 7)
    raw/twelve_data/...      <- every vendor response, exactly as received
    raw/fmp/...              <- likewise, for the split lookups
```

**Keep `_acquisition/`.** It is the evidence behind every number: the raw vendor
responses, and a record of every request. It is also what makes re-running the
command resume instead of restarting.

What the package's price columns contain depends on the provider, and the
manifest says which: **raw exchange prints** from Tiingo, **split-adjusted**
prices from Twelve Data. Either way the other version is kept alongside it —
`vendor_adjusted_prices.csv.gz` or `reconstructed_raw_prices.csv.gz` — so the
project's own adjustment maths can be checked against the vendor's rather than
agreeing with it by construction.

If the package was enriched, the manifest also carries a `[provenance]` table
naming each vendor's contribution separately:

```toml
[provenance]
price_provider = "twelve_data"
price_representation = "split_adjusted"
dividend_provider = "twelve_data"
split_provider = "fmp"
reconstruction_performed = true
reconstruction_algorithm = "split-inverse-1"
reconstruction_label = "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"
reconstruction_file = "_acquisition/reconstructed_raw_prices.csv.gz"
```

It exists because the reconstructed series was computed from one vendor's
adjusted bars and another vendor's split schedule, which means **neither vendor
supplied it**. A reader who saw only `provider = "twelve_data"` would attribute
it to Twelve Data.

---

## 10. Move the package to the project

The whole `empirical-data` folder is the deliverable. Move it however you
normally move files.

**If the project runs on this same computer**, it is already in the right place
and you can skip to step 11.

**To move it elsewhere**, zip it first:

```bash
# macOS / Linux
zip -r empirical-data.zip empirical-data

# Windows PowerShell
Compress-Archive -Path empirical-data -DestinationPath empirical-data.zip
```

Then copy `empirical-data.zip` to the machine where the project runs — scp, a
USB drive, cloud storage you control, whatever suits. The import command accepts
a `.zip` directly, so there is no need to unpack it.

> **Do not upload it to a public GitHub repository.** See
> [A note about licensing and git](#a-note-about-licensing-and-git).

---

## 11. Import and validate

On the machine where the project runs, with its database configured
(`TRADEIT_DATABASE__DSN`) and migrations applied (`alembic upgrade head`):

**First, look at it without importing:**

```bash
tradeit data inspect ./empirical-data
```

This verifies every file's hash and prints what the package can and cannot
support. Nothing is written.

**Then a dry run** — every step happens, nothing is saved:

```bash
tradeit data import ./empirical-data --dry-run
```

**Then the real import:**

```bash
tradeit data import ./empirical-data
```

It ends by printing a **snapshot id** like `tiingo-daily-a1b2c3d4e5f6a7b8`. Copy
it.

**Then run the empirical validation:**

```bash
tradeit validate --snapshot tiingo-daily-a1b2c3d4e5f6a7b8
```

**Expect some checks to fail, and read them rather than being alarmed.** For
example, `data.survivorship_coverage` fails if your package is missing delisted
names — that is the check doing its job and telling you something true about the
data. The report says what each finding means.

This is the first time any of this project's analysis will have run against real
market data.

---

## Common errors

### `TWELVE_DATA_API_KEY=NOT SET` (or `TIINGO_API_KEY=NOT SET`)

The environment variable is not visible to the command. Re-run the `export`
line from [step 3](#3-set-the-key-so-the-tool-can-find-it) **in the same
terminal window**, then try again.

### `no API key: set TWELVE_DATA_API_KEY in your environment`

Same cause. The tool refuses to send a request it knows will be rejected, rather
than letting a 401 look like a network problem.

### `the vendor rejected the request` / HTTP 401 or 403

Two possibilities:

- The key is wrong — check for a stray space or a missing character when you
  pasted it.
- Your plan does not include what was asked for. Free tiers generally cover US
  daily equities; some symbols and some endpoints may be outside them.

The tool does **not** retry these. Repeatedly re-asking a question the vendor has
answered "no" to is how an account gets blocked.

### `rate-limited after 4 attempts`

You hit the plan's request ceiling. Everything already downloaded is kept. Wait
an hour and re-run the same command — it resumes.

### `could not reach api.twelvedata.com` (or `api.tiingo.com`)

No HTTP response at all: no internet, DNS failure, or a firewall/VPN blocking
the connection. Nothing to do with your key. Check you can open the provider's
site in a browser.

### Some instruments failed with `404` / `not_found`

Normal, especially for delisted tickers. They are listed by name in the summary
and recorded in the manifest. If the rest succeeded, the package is usable.

The summary distinguishes *why* each one failed — `not_found`,
`plan_restricted`, `ambiguous`, `unavailable_historically` — because they need
different responses. An `ambiguous` symbol trades on more than one venue and
needs an exchange qualifier; a `plan_restricted` one needs a subscription, not a
retry.

### `PACKAGE_INVALID`

Nothing usable was produced — usually no key, or every request failed. The
`Problems` section says which. Nothing was written that needs cleaning up.

### `You have run out of API credits for the current day` (Twelve Data)

Not an error. The daily allowance is spent. Everything downloaded so far is
saved. Wait for the reset and run the same command again — see
[Resuming and retrying](#resuming-and-retrying).

### `You have run out of API credits for the current minute` (Twelve Data)

The tool handles this itself by pausing and continuing. If you see it in the
summary rather than as a pause, the plan's per-minute allowance is lower than
the tool assumed — pass a smaller `--rate-limit`.

### `/splits is available with the Grow plan and above` (Twelve Data)

Your subscription does not include Twelve Data's corporate-actions endpoints.
This is the normal free-plan situation, and the package is still usable: prices
are unaffected.

Two consequences, both recorded in the manifest rather than hidden:

- there are no splits rows from this provider, and **that absence means "we
  could not ask", not "these securities had no splits"**;
- raw-price reconstruction is skipped, because inverting a split adjustment
  without the split history is guesswork.

**The fix is [section 8](#8-adding-splits-from-fmp):** get the split schedule
from FMP instead.

### `Exclusive Endpoint: This endpoint is not available under your current subscription` (FMP)

FMP's plan does not include `/stable/splits`. Nothing was written and the
package is unchanged. The status will be `NOT_ENRICHED`, and — importantly —
this is recorded as a fact about the plan, never as "these securities had no
splits".

### `Invalid API KEY` (FMP)

The key is wrong, not the plan. Check for a stray space when you pasted it. The
tool deliberately does **not** record this as a plan restriction: putting "not
available on this subscription" in a manifest over a typo would be a lie that
outlives the typo.

### `Limit Reach` (FMP)

The free plan's daily request allowance is spent. Everything already fetched is
kept, the pass stops cleanly, and the status will say so. Wait for the reset and
run `tradeit data enrich` again — cached symbols cost nothing, so only the
remaining ones are requested.

### `Status: NOT_ENRICHED`

FMP answered usefully for no symbol at all — no key, a plan restriction, or the
network. **Nothing was written**; the package is byte-for-byte as it was. The
report's Notes section says which.

### FMP returned an empty split list for a symbol

Recorded, and the finding says exactly what it can and cannot tell you: an empty
list is FMP holding no split records, and it is *also* precisely what an
unrecognised ticker returns. If the ticker is one your package has prices for it
is almost certainly the former, but the tool will not upgrade "no records" into
"never split" on your behalf.

### `Split-schedule CONFLICTS` in the enrichment summary

Two sources disagree about a corporate action, or one holds a record the other
does not. **Nothing is reconciled automatically** — FMP's schedule is used
because you named it, and every difference is listed for you. A split that
simply falls outside your package's date window is *not* listed here; that is
correct behaviour and appears under Findings.

Two sources describing the *same* split as `7` and `0.142857…` is also **not** a
conflict, and is no longer reported as one. Those are reciprocals — one event
measured from opposite ends — and both are normalized to a canonical share-count
multiplier before anything is compared. See `docs/VENDOR_SEMANTICS.md` §1.

### `N bar(s) after the requested end … were discarded`

Expected, and the line proving the tool is behaving. Twelve Data's `end_date`
appears to be exclusive, so the request deliberately asks for one calendar day
beyond your window and throws away anything past it. Without the extra day the
final trading session of your range goes missing; without the trim you would
silently get a session you did not ask for. See `docs/VENDOR_SEMANTICS.md` §2.

### `N session(s) short. This is NOT a weekend or holiday`

Real missing data, measured against the exchange calendar rather than the
calendar date. A range that ends on a Saturday and stops on the Friday is
complete and says so instead; this message only appears when trading sessions
you asked for are genuinely absent.

### `Package status: ACQUISITION_INCOMPLETE_QUOTA`

Expected on a free plan. See
[Resuming and retrying](#resuming-and-retrying).

### The command is very slow

Expected. On the free tier the tool waits between requests to stay inside the
rate limit. Leave it running; interrupt with Ctrl-C whenever you like and re-run
later.

---

## Resuming and retrying

**The tool never re-downloads what it already has.** Every vendor response is
saved under `_acquisition/raw/` and reused. So:

- **Interrupted with Ctrl-C?** Run the exact same command again. Already-fetched
  symbols are read from disk; only the missing ones are requested.
- **Daily credits exhausted (Twelve Data)?** The status will be
  `ACQUISITION_INCOMPLETE_QUOTA`. Nothing is broken. Wait for the reset — a
  Twelve Data day, not necessarily midnight where you are — and run the same
  command again. Repeat until the status reaches `PACKAGE_VALID` or
  `PACKAGE_VALID_WITH_WARNINGS`. Cached symbols cost no credits, so each pass
  makes real progress.
- **Some symbols failed with a network error?** Same thing — re-run the command.
  Or, to attempt *only* the ones that failed:

  ```bash
  python -m tradeit.cli_data data acquire \
      --provider tiingo --start 2010-01-01 \
      --output ./empirical-data --retry-failed
  ```

- **Want genuinely fresh data?** Add `--force-refresh`. This ignores everything
  on disk and re-downloads, which costs your full rate-limit budget again.

Running the command twice never duplicates rows or corrupts the package. The
package is rewritten from scratch each time out of the raw responses.

**To see exactly what happened**, open `_acquisition/journal.jsonl`. One line per
request: what was asked for, what came back, how many rows, which file it went
to, and any error. It contains no credentials, so it is safe to share when
asking for help.

---

## A note about licensing and git

Market-data licences commonly **prohibit redistribution**. As a rule:

- **Do not push downloaded market data to a public repository.** Twelve Data's,
  FMP's and Tiingo's terms are between you and them — read them. The project's
  `.gitignore` already excludes `**/_acquisition/`, `/empirical-data/` and
  `/empirical-smoke/`, so this will not happen by accident from the default
  locations. If you use a different output directory, check `git status` before
  committing.
- Source code, the manifest, small test fixtures and validation reports are
  fine to version-control.
- The raw vendor responses and the bar files are not.

If you need the data on another machine, move it directly rather than through a
public repository.

---

## What this does not do

- **No intraday data.** Daily bars only, deliberately. Daily is enough to
  validate the analytics, daily/weekly pattern recognition and daily breakout
  behaviour. Intraday is a separate acquisition once daily validation works.
- **No fundamentals.** Income statements, balance sheets and filing timestamps
  are a later step with stricter requirements — a fundamental figure without a
  publication timestamp is close to useless here, for reasons explained in
  `DATA_REQUIRED.md`.
- **No performance measurement.** Importing this data does not produce a return,
  a win rate or a profit figure. Nothing in the project computes those yet, on
  purpose. What it produces is an answer to a narrower and more important
  question first: does the platform behave on real markets the way it behaves on
  the synthetic data it was built against?
