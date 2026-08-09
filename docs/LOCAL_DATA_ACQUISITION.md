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

## Contents

1. [What you need first](#1-what-you-need-first)
2. [Get a Tiingo API key](#2-get-a-tiingo-api-key)
3. [Set the key so the tool can find it](#3-set-the-key-so-the-tool-can-find-it)
4. [Run the smoke test — two symbols](#4-run-the-smoke-test--two-symbols)
5. [Run the full download](#5-run-the-full-download)
6. [What success looks like](#6-what-success-looks-like)
7. [What was created, and where](#7-what-was-created-and-where)
8. [Move the package to the project](#8-move-the-package-to-the-project)
9. [Import and validate](#9-import-and-validate)
10. [Common errors](#common-errors)
11. [Resuming and retrying](#resuming-and-retrying)
12. [A note about licensing and git](#a-note-about-licensing-and-git)

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

## 2. Get a Tiingo API key

1. Go to [tiingo.com](https://www.tiingo.com/) and create an account.
2. Once signed in, open your account page and find the **API Token** section.
3. Copy the token. It is a long string of letters and numbers.

The free tier is enough to start and is limited in how many symbols per hour it
will serve. The tool respects that limit automatically and will simply take
longer. If you have a paid plan, see `--rate-limit` in
[Run the full download](#5-run-the-full-download).

> **Keep the token private.** It is a password. Anyone who has it can use your
> account. This tool never writes it to a file, never puts it in a URL, and
> never prints it — there is an automated test that checks all three.

---

## 3. Set the key so the tool can find it

The tool reads the key from an **environment variable** called
`TIINGO_API_KEY`. An environment variable is just a named value your terminal
hands to the programs it runs.

**macOS / Linux** — in the terminal you are going to run the download from:

```bash
export TIINGO_API_KEY="paste-your-token-here"
```

**Windows PowerShell:**

```powershell
$env:TIINGO_API_KEY = "paste-your-token-here"
```

**Windows Command Prompt:**

```cmd
set TIINGO_API_KEY=paste-your-token-here
```

This lasts until you close the terminal window. That is deliberate — a key that
lives only in one terminal session cannot end up in a file you later share.

**Check it worked:**

```bash
python -m tradeit.cli_data data providers
```

You should see:

```
eodhd      stub (see the module docstring)    EODHD_API_KEY=NOT SET
tiingo     ready                              TIINGO_API_KEY=set
```

If `TIINGO_API_KEY` says `NOT SET`, the `export` did not take effect — check for
a typo, and make sure you are in the same terminal window.

**Do not** put the key in a file inside the project. Do not commit it anywhere.

---

## 4. Run the smoke test — two symbols

Always do this first. It takes about ten seconds and proves the key works, the
network works, and the package format is right — before you spend an hour
downloading.

```bash
python -m tradeit.cli_data data acquire \
    --provider tiingo \
    --symbols SPY,AAPL \
    --start 2025-01-01 \
    --output ./empirical-smoke
```

On Windows PowerShell, use a backtick `` ` `` instead of `\` at the end of each
line, or just put the whole command on one line.

You should see a summary ending with:

```
Package status       : PACKAGE_VALID
...
Snapshot-ready       : YES
```

If you see that, everything works. **If not**, go to
[Common errors](#common-errors) before continuing — a problem here will only be
bigger on the full download.

---

## 5. Run the full download

This gets the whole validation universe — 91 instruments chosen to stress the
system, including delisted names like LEH and BSC that many data sources
silently omit — from 2010 to the last completed trading session.

**First, see how big it will be** (this makes no network requests):

```bash
python -m tradeit.cli_data data acquire \
    --provider tiingo \
    --start 2010-01-01 \
    --output ./empirical-data \
    --estimate-only
```

**Then run it for real** — drop the `--estimate-only`:

```bash
python -m tradeit.cli_data data acquire \
    --provider tiingo \
    --start 2010-01-01 \
    --output ./empirical-data
```

Leave it running. On the free tier this takes roughly an hour, most of which is
the tool deliberately waiting between requests so as not to exceed the rate
limit. **You can interrupt it at any time with Ctrl-C** and re-run the same
command later; it picks up where it stopped. See
[Resuming and retrying](#resuming-and-retrying).

### Options you might want

| Option | What it does |
|---|---|
| `--symbols AAPL,MSFT,NVDA` | Download only these instead of the whole universe. |
| `--start 2004-01-01` | Go further back. More history is better if your plan allows it. |
| `--end 2024-12-31` | Stop earlier. Defaults to yesterday. |
| `--rate-limit 500` | If you have a paid plan that allows more requests per minute. Leave it alone on the free tier. |
| `--name my-package` | Name recorded in the manifest. Defaults to `tiingo-daily`. |
| `--force-refresh` | Re-download everything, ignoring what is already on disk. |
| `--retry-failed` | Attempt only the instruments that failed last time. |

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

## 7. What was created, and where

Inside `./empirical-data` (or whatever you passed to `--output`):

```
empirical-data/
  manifest.toml              <- describes the package; hashes of every file
  daily_bars.csv.gz          <- the price history (the big one)
  instruments.csv            <- one row per security
  symbol_mappings.csv        <- which ticker meant which security, and when
  splits.csv                 <- splits and reverse splits
  dividends.csv              <- cash dividends
  _acquisition/              <- provenance; keep it, do not edit it
    journal.jsonl            <- one line per request made
    acquisition_report.json  <- the full summary as data
    vendor_adjusted_prices.csv.gz
    raw/tiingo/...           <- every vendor response, exactly as received
```

**Keep `_acquisition/`.** It is the evidence behind every number: the raw vendor
responses, and a record of every request. It is also what makes re-running the
command resume instead of restarting.

The package stores **unadjusted** prices — the prices as the exchange printed
them. The vendor's adjusted prices are kept separately in
`vendor_adjusted_prices.csv.gz` so the project's own adjustment maths can be
checked against the vendor's, rather than agreeing with it by construction.

---

## 8. Move the package to the project

The whole `empirical-data` folder is the deliverable. Move it however you
normally move files.

**If the project runs on this same computer**, it is already in the right place
and you can skip to step 9.

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

## 9. Import and validate

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

### `TIINGO_API_KEY=NOT SET`

The environment variable is not visible to the command. Re-run the `export`
line from [step 3](#3-set-the-key-so-the-tool-can-find-it) **in the same
terminal window**, then try again.

### `no API key: set TIINGO_API_KEY in your environment`

Same cause. The tool refuses to send a request it knows will be rejected, rather
than letting a 403 look like a network problem.

### `the vendor rejected the request` / HTTP 403

Two possibilities:

- The key is wrong — check for a stray space or a missing character when you
  pasted it.
- Your plan does not include what was asked for. The free tier covers US daily
  equities; some symbols may be outside it.

The tool does **not** retry these. Repeatedly re-asking a question the vendor has
answered "no" to is how an account gets blocked.

### `rate-limited after 4 attempts`

You hit the plan's request ceiling. Everything already downloaded is kept. Wait
an hour and re-run the same command — it resumes.

### `could not reach api.tiingo.com`

No HTTP response at all: no internet, DNS failure, or a firewall/VPN blocking
the connection. Nothing to do with your key. Check you can open
`https://api.tiingo.com` in a browser.

### Some instruments failed with `404`

Normal, especially for delisted tickers. They are listed by name in the summary
and recorded in the manifest. If the rest succeeded, the package is usable.

### `PACKAGE_INVALID`

Nothing usable was produced — usually no key, or every request failed. The
`Problems` section says which. Nothing was written that needs cleaning up.

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

Market-data licences commonly **prohibit redistribution**. Tiingo's terms are
between you and Tiingo — read them — but as a rule:

- **Do not push downloaded market data to a public repository.** The project's
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
