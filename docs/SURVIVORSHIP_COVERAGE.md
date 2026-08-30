# Survivorship coverage: the eleven controls, and why ten are missing

`data.survivorship_coverage` FAILed on the first real snapshot with *10 of 11
delisted names are absent*. That sentence is true and almost useless: it covers
six different situations with six different remedies, only two of which are the
vendor's.

This is what the check now reports instead, and what the classification is
based on.

---

## 1. The roster

The eleven delisted controls in `config/validation_universe.toml`, against a
request for **2010-01-01 → 2026-08-07**:

| ticker | last trade | in the requested window? | alias candidates |
|---|---|---|---|
| ENRNQ | 2004-01-01 | **no** | ENE, ENRN |
| BSC | 2008-05-30 | **no** | — |
| LEH | 2008-09-17 | **no** | LEHMQ |
| WAMUQ | 2009-03-20 | **no** | WAMU, WM |
| TWTR | 2022-10-27 | yes | — |
| SIVB | 2023-03-09 | yes | SIVBQ |
| FRC | 2023-05-01 | yes | FRCB |
| BBBY | 2023-05-03 | yes | BBBYQ |
| CS | 2023-06-12 | yes | — |
| ATVI | 2023-10-12 | yes | — |
| VMW | 2023-11-21 | yes | — |

**Four of the eleven could never have been returned by that request.** Enron,
Bear Stearns, Lehman and Washington Mutual all stopped trading before
2010-01-01. No vendor, on any plan, can supply a price series for a security
over years in which it did not exist.

That is an **acquisition-plan defect, not a vendor coverage gap**, and the two
call for opposite work: one is fixed by changing a command-line argument, the
other by changing vendors. Reporting the first as the second sends the fix to
the wrong place — and quietly builds a case that "no vendor has these", which
is the argument that ends with the controls being dropped.

The remaining seven fall inside the window and should have been obtainable.

---

## 2. Why the reason was not knowable, and now is

The acquisition run already classifies every symbol it asks for.
`tradeit.acquisition.base.SymbolStatus` distinguishes `not_found`,
`unavailable_historically`, `plan_restricted`, `ambiguous` and `unknown`, and
`FetchStatus` distinguishes the transport outcomes.

**That classification stopped at the manifest boundary.** A package records the
instruments it *contains*; a symbol that produced no rows produces no row
anywhere, so from the package contents alone "requested and refused" and "never
asked for" are the same observation. The validator downstream could only say
"absent".

The manifest now carries an `[acquisition]` section — the requested window and
one `[[acquisition.outcomes]]` entry per **requested** symbol, failures
included — and the importer copies it into the snapshot's package row. Failure
text is redacted at the provider boundary before it gets there.

> While adding this, the Tiingo adapter was found to scrub failure messages
> with `redact_url`, which only matches `param=value`. A vendor echoing a
> rejected key in prose inside a JSON error body slipped through into the
> on-disk journal. Fixed to `redact_text(..., token)`, matching the Twelve Data
> adapter, with a test that fails on a leak.

---

## 3. The classification

| status | meaning | whose fix |
|---|---|---|
| `present` | in the snapshot | — |
| `outside_requested_window` | last trade predates the requested start | **ours** — widen the window |
| `symbol_alias_not_attempted` | the vendor refused this string and the recorded alternatives were never tried | **ours** — identity resolution |
| `not_found_at_provider` | the vendor does not recognise the symbol | vendor |
| `no_history_at_provider` | the vendor knows it and holds no history — *this* is "no delisted coverage" | vendor |
| `not_available_on_plan` | entitlement, not absence | billing |
| `ambiguous_at_provider` | needs an explicit exchange or MIC | ours — re-request |
| `provider_error` | transport failure; evidence of nothing | retry |
| `unresolved` | absent, nothing recorded | re-acquire |

Two properties hold by construction and are asserted in tests:

- **`is_covered` is true only for `present`.** No category can turn a FAIL into
  a PASS. A control that is not in the snapshot is not in the snapshot, whoever
  is at fault, and every rate computed over that snapshot is a rate over
  survivors.
- **`not_available_on_plan` and `provider_error` are not evidence of absence.**
  An entitlement refusal and a timeout say nothing about what a vendor holds,
  and retiring a control on the strength of a billing decision is the failure
  mode this whole file exists to prevent.

### On the existing snapshot

That snapshot predates the `[acquisition]` record, so the check reports what it
can prove and no more:

```
  ticker   last trade   status                       detail
  -------- ------------ ---------------------------- ----------------------------
  LEH      2008-09-17   outside_requested_window     last traded 2008-09-17, before the requested start 2010-01-01
  BSC      2008-05-30   outside_requested_window     last traded 2008-05-30, before the requested start 2010-01-01
  WAMUQ    2009-03-20   outside_requested_window     last traded 2009-03-20, before the requested start 2010-01-01
  ENRNQ    2004-01-01   outside_requested_window     last traded 2004-01-01, before the requested start 2010-01-01
  TWTR     2022-10-27   present                      in the snapshot
  ATVI     2023-10-12   unresolved                   no acquisition outcome recorded for this symbol
  VMW      2023-11-21   unresolved                   no acquisition outcome recorded for this symbol
  SIVB     2023-03-09   unresolved                   no acquisition outcome recorded for this symbol
  FRC      2023-05-01   unresolved                   no acquisition outcome recorded for this symbol
  CS       2023-06-12   unresolved                   no acquisition outcome recorded for this symbol
  BBBY     2023-05-03   unresolved                   no acquisition outcome recorded for this symbol
```

Six `unresolved` rather than six guesses. Without the record, "requested and
refused" and "never requested" are indistinguishable, and inventing a diagnosis
the evidence does not support would be worse than saying so.

**The check still FAILs**, on all ten.

---

## 4. Symbol identity was checked before blaming the vendor

Several of these securities traded under more than one symbol. A bankruptcy
filing typically moves a listing over the counter and appends a `Q`:

| universe ticker | also traded as | note |
|---|---|---|
| LEH | LEHMQ | NYSE → pink sheets, September 2008 |
| WAMUQ | WM, WAMU | **WM is Waste Management today** |
| ENRNQ | ENE, ENRN | NYSE `ENE` until the 2001 filing |
| SIVB | SIVBQ | post-Chapter 11 |
| FRC | FRCB | post-receivership |
| BBBY | BBBYQ | post-Chapter 11 |

These are recorded in the universe as `alias_candidates`, and the file says
plainly what they are: **historical ticker facts, not verified vendor symbols.**
No vendor is claimed to serve any of them, and no endpoint was invented to
reach them. Their only job is to make a provider's "unknown symbol" answer
classifiable as identity resolution we never attempted, rather than as delisted
coverage the vendor does not have. Which of the two it really is gets settled by
asking the vendor, not by this file.

The `WAMUQ`/`WM` row is the reason an alias must never satisfy a control by
string match. A snapshot containing `WM` contains Waste Management, not
Washington Mutual — and a check that accepted it would be manufacturing exactly
the identity failure this instrument exists to provoke. There is a test.

---

## 5. If the vendor genuinely cannot supply them

Not yet established — the four pre-window names were never asked for over a
window in which they traded, so no vendor claim has been tested for them. The
first two steps are ours, in order:

1. **Re-acquire with `--start 2001-01-01`** (early enough for Enron's last
   trade). Costs nothing but credits and settles four of the ten.
2. **Request the alias candidates** for the six names that have them, and keep
   whichever string the vendor resolves.

Only what survives both is a vendor question. The smallest seam for answering it
without inventing anything new:

- `AcquisitionProvider` is already the vendor boundary, and `PackageEnricher`
  already demonstrates the pattern of a **second vendor adding to an existing
  package** — it merges a split schedule from FMP into a Twelve Data price
  package and records both in `[provenance]`.
- A supplemental price source is the same shape: an `AcquisitionProvider` run
  over only the missing tickers, its rows appended to the package's
  `daily_bars` file, with per-instrument attribution recorded in `[provenance]`
  so no reader can attribute a supplemented series to the primary vendor.

No new concepts, no new file format, and no vendor named — naming one before
its coverage has been verified would be the guess this document is trying to
avoid. **The validator continues to FAIL survivorship coverage until the
histories actually exist in the snapshot.**
