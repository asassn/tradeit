# Vendor semantics: what each provider's numbers actually mean

Findings from the first live smoke test against real accounts, and the code that
now depends on them. Everything here was observed by running the tool against a
vendor, not read from documentation — **this build environment's egress policy
blocks every provider host**, so vendor documentation could not be consulted
while writing any of it. Where a conclusion rests on observation alone, it says
so and says how many observations.

Three findings, in order of how much damage the bug would have done.

---

## 1. Split factors: Twelve Data reports the reciprocal of the share count

### What was observed

A live acquisition of AAPL produced these split records from Twelve Data's
`/splits`, alongside FMP's `/stable/splits` for the same two dates:

| Ex-date | Twelve Data | FMP | The actual corporate action |
|---|---|---|---|
| 2014-06-09 | `0.142857142857…` | `numerator 7, denominator 1` | Apple split 7-for-1 |
| 2020-08-31 | `0.25` | `numerator 4, denominator 1` | Apple split 4-for-1 |

The tool reported both as cross-provider conflicts. They are not conflicts.
`0.142857…` is `1/7` and `0.25` is `1/4`: the same two corporate actions,
measured from the opposite end.

### Why this was a real bug and not a cosmetic one

Before this fix, `twelvedata._split_ratio` computed `to_factor / from_factor`
and treated the result as the **share-count multiplier**. Under that reading,
reconstructing raw prices from a Twelve Data split schedule would have
multiplied every pre-2014 adjusted Apple price by `1/28` instead of `28` — a $90
print becoming $3.21 rather than $2,520 — and the resulting series would have
looked like an unremarkable penny stock. Nothing in the data would have revealed
it.

The reason it never reached a package is incidental: Twelve Data's `/splits` is
not on the free plan this project uses, so the schedule that actually drives
reconstruction comes from FMP, whose explicit numerator/denominator pair was
always read correctly. The bug was one plan upgrade away from being live.

### What the evidence supports

Apple's share count went **up** sevenfold in June 2014. No reading of the world
makes `0.142857` the multiplier on its share count. So whatever Twelve Data's
`from_factor`/`to_factor` are named after, the share-count multiplier is
`from / to`, not `to / from`.

Two narratives fit the observation and they are arithmetically identical:

1. The single factor is the **price-adjustment multiplier** — multiply
   historical prices by 0.25 to get the adjusted series.
2. `from_factor`/`to_factor` spell the split out as "4:1", i.e.
   `from_factor=4, to_factor=1`.

Both give share count = `from / to`. Which narrative is right is **unresolved**
and does not need resolving, because the normalization is the same either way.

**Confidence basis:** two split events, one security, one live free-tier
account, cross-checked against FMP's explicit share pair and against
independently known corporate actions. Twelve Data's own documentation was not
readable from here. The declaration lives in one constant,
`twelvedata.SPLIT_FACTOR_CONVENTION`, so correcting it is a one-line change if a
later observation contradicts this one.

### The canonical representation

`SplitEvent` now holds exactly one quantity and derives the rest, so no caller
has to remember which way round a particular use runs:

| Quantity | 4-for-1 | 1-for-8 reverse |
|---|---|---|
| `economic_ratio` (share-count multiplier) — **canonical** | 4 | 0.125 |
| `price_adjustment_multiplier` (raw price → adjusted) | 0.25 | 8 |
| `volume_adjustment_multiplier` (raw volume → adjusted) | 4 | 0.125 |
| `raw_price_reconstruction_multiplier` (adjusted → raw) | 4 | 0.125 |
| `raw_volume_reconstruction_multiplier` (adjusted → raw) | 0.25 | 8 |

Volume moves inversely to price, per the Phase 1 policy, which is what keeps
dollar volume invariant across the adjustment. The reconstruction arithmetic is
unchanged by any of this — only what reaches it is.

### The convention is declared per provider, never inferred per value

This is the load-bearing rule. **`0.25` is the price-adjustment multiplier of a
4-for-1 forward split and the share-count multiplier of a 1-for-4 reverse
split**, and those are opposite events. No amount of looking at the number
settles which it is. So each provider declares:

| Provider | Convention | Basis |
|---|---|---|
| FMP | `new_over_old_shares` | **Fact.** An explicit pair carries its own direction; 4-for-1 and 1-for-4 are different pairs. |
| Twelve Data | `price_adjustment_multiplier` | **Inference from two observations**, as above. |
| Tiingo | `share_count_multiplier` | `splitFactor` is 2.0 on the ex-date of a 2-for-1. Declared so every provider has an answer, not only the two that were audited. |

A provider whose convention is not established is `unknown`, and
`to_share_count_multiplier` **raises** rather than guessing.

### Comparison happens after normalization

`compare_schedules` compares canonical share-count multipliers with a relative
tolerance of `1e-6` — not zero, because a vendor serving `1/7` as a truncated
decimal cannot round-trip exactly; not loose, because `1e-6` is nine orders of
magnitude below the gap between a 7-for-1 and a 6-for-1.

Three outcomes, and the middle one is new:

- **Equal within tolerance** → silent. This is where the reported "conflicts"
  went.
- **Exact reciprocals after normalization** → a **note**, not a conflict, saying
  a provider's declared convention is wrong. It should never fire; if it does,
  the fix is a one-line declaration rather than an argument with a data vendor.
- **Neither** → a conflict, reported and never reconciled. A 7-for-1 against a
  2-for-1, or a forward against a reverse, still stops here.

### What the package keeps

`splits.csv` carries the canonical `ratio` plus `vendor_factor` and
`vendor_convention` — what the vendor actually sent and how it was read. A later
disagreement about the normalization is settled by opening the file rather than
by re-downloading it.

`RECONSTRUCTION_ALGORITHM_VERSION` is bumped to `split-inverse-2`. The
arithmetic did not change; its inputs did. A sidecar stamped `split-inverse-1`
that was built from a Twelve Data split schedule used reciprocal factors, and
that string is how such a file is found.

---

## 2. Twelve Data's `end_date` appears to be exclusive

### What was observed

A live acquisition requested `2010-01-01` through `2025-12-31` and received
`2010-01-04` through `2025-12-30` — for **both** AAPL and NVDA. 2025-12-31 was a
US trading session.

### Causes ruled out

| Candidate | Ruled out because |
|---|---|
| Timezone/date conversion | `_session_date` takes the date part of the vendor's string verbatim and never parses a time or converts a zone. |
| Request construction | The URL carried `end_date=2025-12-31` exactly as requested. |
| The `outputsize` cap | 2010–2025 is roughly 4,020 daily sessions, well under the 5,000 ceiling, and truncation would not remove precisely one day. |
| Provider data availability | Two independent, heavily covered large-cap symbols missing the same single session is not a coverage gap. |
| Normalization / filtering | No filter existed that could drop a trailing session. |

### Remaining explanation

`end_date` is exclusive — or is read as the instant `2025-12-31T00:00:00` and
compared with `<`, which produces the same result for daily bars. This is an
inference from one run over two symbols; the vendor's documentation was not
readable from this environment.

### The fix, and why it is safe without confirmation

The request now asks for `end_date = requested_end + 1 day`
(`END_DATE_PROBE_DAYS`), and normalization **discards every bar dated after the
requested end**.

The trim is what makes this implementable on an inference:

- If `end_date` is exclusive, the probe recovers the missing final session.
- If `end_date` is inclusive, the probe returns one extra bar and the trim
  removes it — byte-identical output to not probing at all.

A test asserts exactly this: the same package is produced against a fake
transport implementing each semantic. Without the trim, this would be a silent
one-day lookahead, which is the failure the rest of the platform exists to
prevent, so the trim is not an optimisation — it is the reason the change is
allowed.

The number of trimmed bars is reported as a finding rather than dropped
quietly, so the probe's behaviour is visible in every run.

### The coverage check was also crying wolf, at both ends

`coverage_findings` previously compared the observed first and last rows against
the **calendar dates** in the request. Both comparisons produce false positives
on well-formed packages:

- *"requested through X but the latest row is Y"* fires on every range that ends
  on a weekend or holiday.
- *"requested from 2010-01-01 but the vendor's earliest row is 2010-01-04.
  Either the security had not listed, or the provider's history begins later"* —
  the message the live run produced for both AAPL and NVDA. **Both explanations
  were wrong.** 1 January is a market holiday, the 2nd and 3rd were a weekend,
  and 2010-01-04 is exactly the first session on or after the requested start.

Both ends now measure against the exchange calendar, using the project's own
`TradingCalendar`:

- expected first = the first trading session **on or after** `start`
- expected last = the last trading session **on or before** `end`

A shortfall is reported with the number of sessions lost — *"N session(s) short
at the start"* — and a complete range produces **no line at all**. That last
detail is not cosmetic: every coverage finding lands in
`AcquisitionReport.findings`, and a non-empty findings list downgrades the
package to `PACKAGE_VALID_WITH_WARNINGS`. An informational "this range is
complete" per symbol would mark a flawless 91-symbol download as warned-about,
which is both noise and a false statement about the package.

---

## 3. "Reconstructed across 5 splits" was true and misleading

### What was observed

For an Apple package spanning 2010–2025 the report said *"reconstructed across 5
split(s)"*, and for NVIDIA *"6 split(s)"*. Both counts are correct as counts of
records supplied. Only two of each fall inside the package's window. The line
reads as though five and six splits were applied.

### The fix

`SplitCensus` replaces the single count with four, because they are four
different facts and none is derivable from the others:

| Count | Apple, 2010–2025 |
|---|---|
| `supplied` — records the provider handed over | 5 |
| `in_coverage` — ex-date inside the package's first-to-last session window | 2 |
| `effective` — changed at least one reconstructed row | 2 |
| `outside_coverage` — the rest, reported by side | 3 (all before the window) |

`in_coverage` and `effective` are **not** the same count. A split **after** the
window's end is outside coverage and affects *every* row; a split **before** the
window's start is outside coverage and affects *none*. Reporting only "outside
coverage" would merge two opposite situations.

`effective` is computed from the same strictly-after comparison the
reconstruction itself uses, so the census and the arithmetic it describes cannot
drift apart. A test asserts that the census's `effective` equals the number of
distinct split dates appearing in the written sidecar.

**The reconstruction arithmetic was not touched.** This is a reporting change
only.

---

---

## 4. A split outside the price window is not a cross-provider conflict

### What was observed

The rendered enrichment report showed:

```
Split-schedule CONFLICTS (3)
  AAPL: 1987-06-16 …
  AAPL: 2000-06-21 …
  AAPL: 2005-02-28 …
```

for a package whose price coverage begins 2010-01-04, while the JSON payload
recorded none. Two problems in one output.

### Why they are not conflicts

Twelve Data's corporate-action endpoints were queried **for the requested
range**. They were never asked about 1987. Reporting "FMP has a 1987 split that
Twelve Data does not" as a conflict demands of one vendor a history nobody
requested from it, and the events change no row in the package either way.

### The classification

Observations are now typed by `ScheduleFindingKind`, and `kind.is_conflict` is
the single place the distinction lives:

| Kind | Conflict? | Means |
|---|---|---|
| `OUTSIDE_COVERAGE` | no | Ex-date outside the price window. Absence from the other source is expected, not contradictory. |
| `REPRESENTATION_MISMATCH` | no | Two *normalized* ratios are reciprocal. A provider's declared convention is wrong; the vendors agree about the world. |
| `CROSS_PROVIDER_CONFLICT` | **yes** | Same date, genuinely different share-count multipliers. Neither equal nor reciprocal. |
| `MISSING_FROM_PRICE_PROVIDER` | **yes** | In-coverage event the split provider has and the price provider lacks — where the price provider does supply other in-coverage splits. |
| `MISSING_FROM_SPLIT_PROVIDER` | **yes** | The mirror case. The schedule about to drive reconstruction is missing an event. |
| `DUPLICATE_RECORD` | **yes** | The same event listed twice. Not merged. |
| `IMPLAUSIBLE_RATIO` | **yes** | Beyond anything a corporate action produces. |
| `NON_POSITIVE_RECONSTRUCTION` | **yes** | The split schedule and the price series contradict each other. |

### The in-window absence rule, stated explicitly

This is the case that needed a decision rather than a lookup, so the rule is
written down rather than left to the code:

> An in-coverage event present in one source and absent from the other is a
> conflict **only if that other source supplied at least one in-coverage split
> for the same symbol.** A provider that supplied some in-window splits is
> asserting a schedule for that window, so a gap in it is a real contradiction.
> A provider that supplied none is *silent*, not contradicting, and treating
> silence as disagreement would turn every plan-restricted package into a wall
> of conflicts about events it was never able to report.

### One classification, two views

The rendered report and the JSON payload now partition the **same** list. The
payload carries `conflict_count`, the `conflicts` messages, and
`schedule_findings` with every observation's `kind` and `is_conflict`. Each
rendered line is prefixed with its kind — `[OUTSIDE_COVERAGE] …` — so the two
views can be matched by eye and by grep. A test asserts the counts agree and
that the report prints no CONFLICTS heading when the payload has none.

After this change the AAPL/NVDA run has **zero** genuine conflicts, with the
seven outside-coverage records reported as informational findings and counted in
the census.

---

## What is still unresolved

- Whether Twelve Data's `from_factor`/`to_factor` are named for the price
  adjustment or for the share pair. Arithmetically irrelevant; recorded because
  "we do not know" is different from "it does not matter".
- Whether `end_date` is documented as exclusive. The implemented fix does not
  depend on the answer.
- Whether Twelve Data's daily bars are adjusted for **splits only**, as its
  documentation is understood to claim. If they were also dividend-adjusted, the
  reconstruction would be confidently wrong and nothing in the data would
  reveal it. Unchanged by this work, and still the largest open assumption under
  the reconstructed series.
- Whether FMP's split history is complete for any given security. A split it
  does not hold leaves every price before it wrong by that split's factor, and
  it is invisible.
