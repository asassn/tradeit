# The forward universe — 48 names, and why not 195

**Measured 2026-10-20, against the live Tiingo free tier.**

The owner approved a daily capture of the 195 SIC-classified names. **The free
tier cannot do it**, and that is a measurement rather than an estimate: a run
over all 195 attempted 35 distinct symbols and was rate-limited on every one of
them, three times each, before abandoning them. The adapter declares
`rate_limit_per_minute = 50`, and Tiingo's free allowance is in that
neighbourhood per *day* for unique symbols.

**Tiingo is the only option.** `FmpAcquisition` does not exist — FMP is used
here for corporate actions and symbol reference, not price acquisition — and
`EodhdAcquisition` is a deliberate stub whose `fetch` returns `REJECTED`. So
there is no second provider to split the universe across without writing an
adapter first.

## The choice made

**48 names, the first of the same SIC-classified list**, which was itself
ordered by price coverage over 2010–2019. Deterministic and reproducible; a
universe picked by eye is a selection nobody can check.

48 rather than 50 leaves headroom, because the daily allowance is also consumed
by any manual run and a job that exhausts its quota mid-capture records
rate-limit refusals rather than market silences — and **those two must not be
confused.** A refusal is a fact about the vendor; a silence is a fact about the
security.

## What this costs, honestly

**Fewer names is less evidence per day**, and nothing recovers the names not
captured. Against that: starting narrow beats not starting, because a day not
captured cannot be bought later at any price — which is the whole argument for
the forward record.

## What has and has not been proved, as of 2026-10-20

**Proved:** the capture works end to end against the live API — credential,
raw-unadjusted rows, prices persisted under corpus ids, a nonexistent symbol
recorded as silent, re-runs idempotent on prices and additive on observations.
Verified repeatedly on small universes.

**Not proved:** a full 48-name run completing in one pass. **Today's allowance
was consumed by the testing itself** — the 195-name attempt alone touched 35
symbols and burned retries on every one. A 48-name run started afterwards was
throttled from the first request.

That is a fact about today, not about the design: the quota resets, and the
first scheduled run gets a fresh one. It is recorded here rather than left for
somebody to rediscover, because a throttled run and a quiet market produce
different rows and the distinction only matters if somebody knows to look.

**The first scheduled run is therefore also the first full-universe proof.**
Its outcome should be checked rather than assumed.

## What would widen it, without spending

Implementing `EodhdAcquisition` properly. The key exists and the vendor has
deep coverage. **The caveat is on the record and is not small:** EODHD serves
one series per ticker — the most recent holder — so an earlier holder of a
reused ticker is not addressable (`PHASE_06_VENDOR_MATRIX.md`, measured). That
matters far less going forward, where a ticker handover is *observed* as it
happens rather than reconstructed, but it is a reason to keep EODHD's bars
distinguishable from Tiingo's rather than pooled.

**Done 2026-10-21.** `EodhdAcquisition` is implemented: daily prices and
splits, against the URL shapes `research01.eodhd_client` has already used
against the live API for 854,718 bars, with fixtures captured from real
responses and the key stripped at capture time.

**It does not widen the universe by itself.** The adapter exists; pointing the
daily capture at two providers is a separate change, and it needs a decision
this document should not pre-empt: whether a security's bars may come from
whichever vendor answered, or whether each security is assigned to one. The
second is the safer default — EODHD's adjustment basis is `UNKNOWN` and
Tiingo's is `RAW_UNADJUSTED`, and pooling them would mix two bases in one
series, which is the §0.1a defect reintroduced by the back door.

The 48 keep accumulating on Tiingo in the meantime.

## 46 names, not 48 — two were already dead when the universe was written

**Measured 2026-10-22, after the first two scheduled runs.** Both runs reported
exactly two silent symbols, and `ABAX` and `AUTO` had **never returned a bar**.
They are not forward delistings. Resolved per date against the corpus, as
`RESEARCH_01_DATA_DICTIONARY.md` §0.2 requires:

| ticker | last held | `security_id` |
|---|---|---|
| `ABAX` | 2018-08-10 | 3377 |
| `AUTO` | 2022-09-12 | 4535, and 1914 before it to 2013-05-01 |

Independently cross-checked: resolving all 48 per date says 46 hold their
ticker today, and the two that do not are precisely the two the vendor was
silent about. The corpus and Tiingo agree without being asked to.

### Why they were in the list, which is the part worth fixing

The selection above is recorded as *"the first 48 of the same SIC-classified
list, ordered by price coverage over 2010–2019"*, and defended as
deterministic and reproducible. It is both. **It also selects for historical
data richness and says nothing whatever about whether a company still exists**
— a ticker with dense 2010–2019 coverage is, if anything, *more* likely to
belong to something since acquired. The ordering was not wrong; using it
unfiltered for a *forward* universe was.

The two slots are **not refilled.** The source 195-name list is not a committed
artifact, so there is no deterministic next-in-order to promote, and picking
two replacements by eye would violate this document's own rule that a universe
picked by eye is a selection nobody can check. 46 names, honestly 46.

### The read rule for the record

**`ABAX` and `AUTO` observations dated 2026-10-21 and 2026-10-22 are not
exits.** Four rows, `status = empty`, `rows_returned = 0`. They are a true
record of what was asked and what came back, so they stay — this record does
not delete its own history — but a reader counting silences as delistings will
count two companies as having left the market in October 2026, one of which
left in 2018. The rows before 2026-10-22 for those two symbols carry no
survivorship information.

### What stops it recurring

`tradeit.forward.identity.resolve_as_of` resolves per date and returns
`NO_HOLDER_AT_DATE` or `AMBIGUOUS` instead of an id. The capture drops those
from the ask and names them, rather than asking and recording a silence about a
ticker nobody holds. The old resolver,
`scripts/backtest_research01._universe`, has no date filter and returns the
*earliest* holder; 1,471 of the corpus's 17,342 tickers are held by more than
one security, so for 8.5% of tickers it answers a question nobody asked.

**The exclusion is deliberately narrower than "unresolvable".** A symbol that
has already printed a bar is never excluded, whatever the corpus later says
about its ticker. `research-01` is frozen and will not learn about a 2027
delisting — but on a refresh, a ticker whose alias had just been closed would
start resolving to nothing and the capture would stop asking **exactly as the
exit was being observed**, turning the delisting into a gap instead of a
silence. That is the one failure this record exists to prevent, so the rule is:
no bar ever recorded means no baseline and no information; printed-then-silent
is an observation and keeps being asked about.

## Two vendors, one per security — 46 on Tiingo, 10 on EODHD

**Live as of 2026-10-22.** `EodhdAcquisition` works against the real key
(HTTP 200, `adjustment_policy() = unknown`), and the daily job now runs the
capture once per vendor from two committed files:

| file | vendor | names | basis |
|---|---|---|---|
| `forward_universe.txt` | Tiingo | 46 | `RAW_UNADJUSTED` |
| `forward_universe_eodhd.txt` | EODHD | 10 | `UNKNOWN` |

**Each security takes bars from exactly one vendor.** This was the open
question this document declined to pre-empt, and the answer is the one it
already recommended: pooling would make a price whose adjustment state depends
on which vendor happened to answer that day, which is the §0.1a defect
reintroduced through the back door. An empty or absent file turns a vendor off,
and one vendor's refusal does not cancel the other's capture — half a day's
evidence is worth more than none, and a day not captured is gone.

`tests/unit/test_forward_universe.py` keeps the two files disjoint, one line,
upper case and duplicate-free, with no corpus needed so it runs in CI.

### How the ten were chosen

Reproducible, and stated so it can be re-derived: securities holding their
ticker **as of today** per §0.2, with at least 2,000 price rows in the corpus,
excluding the 46 already covered, ordered by row count descending then ticker
ascending, first ten. 394 candidates met the filter.

Ordering by coverage is the same ordering that put two dead tickers in the
original 48 — but the per-date liveness filter is what that selection lacked,
and with it in place, richer history is a genuine advantage: it gives a forward
observation a baseline to be measured against.

**Those row counts are raw rows, not sessions.** §0.1: every trading day is
stored twice, so 18,474 rows is roughly 9,237 sessions. The threshold is a
filter, not a reported quantity.

**Ten is a probe, not a chosen universe.** EODHD's daily allowance on this key
is not documented and has not been measured, and measuring it directly means
deliberately exhausting it. The journal separates a vendor refusal from a
market silence, so capacity will show itself over the next few runs and the
slice can be widened on evidence instead of on a guess.

### The provenance bug this found, which the obvious test could not

The first live EODHD capture wrote 60 bars — **all of them labelled
`tiingo`.** `capture_forward` took `provider: str = "tiingo"` and the only
caller never passed it. A mislabelled bar is not cosmetic: it makes a series
whose adjustment basis is *unknowable* rather than merely unknown, and
ADR-0005 cannot be enforced on it.

Both of the checks that should have caught it passed. The two universe files
really were disjoint, and no instrument really did carry two vendors —
**because every label was the same wrong one.** Two true statements about a
corrupt record.

`provider` now has no default, so a caller cannot omit it;
`test_a_bar_carries_the_vendor_that_was_actually_asked` asserts the label
equals the vendor asked, and fails against a hardcoded one. The existing test
that asserted `bar.source == "tiingo"` was pinning the default rather than the
invariant — it passed no provider at all — and now passes the one it asserts.

The 60 bars were **relabelled, not deleted**: the prices are correct EODHD
data on ten instruments the Tiingo universe does not touch, so only the one
wrong field needed fixing. A copy was taken first. The journal's 20 entries
from that run still say `tiingo`; it is append-only diagnostic output and
rewriting it would be worse than the inaccuracy.

## Every scheduled observation was dated one day late

**Measured 2026-10-22 at 22:13 EDT: bars landed on 2026-10-22, observations on
2026-10-23.** `end` defaulted to `dt.datetime.now(dt.UTC).date()` and the job
fires at 22:10 America/New_York — already tomorrow in UTC. So each run asked
for a window ending tomorrow, received today's bar, and recorded the
observation under tomorrow's date. The comment defending the UTC choice had the
reasoning backwards.

The bars were always right: `_write_bars` takes each row's own `session_date`
from the vendor. Only the observation carried the request's `end` — which is
the one field a delisting is made of.

**A Friday-night run would have stamped Saturday.** The vendor still returns
Friday's bar for a window ending Saturday, so the record would have asserted a
Saturday session that had a price, and on a universe with a genuine exit, a
non-existent session in which a company was silent.

`end` is now the latest trading session at or before the **market-local** date,
taken from `research-01`'s `trading_sessions` (1990-01-02 to 2030-12-31), and
it **fails closed** if the calendar cannot answer. A weekend run walks back to
Friday and says so.

### The read rule

**Observation rows dated 2026-10-22 and 2026-10-23 are one day late.** They
describe sessions 2026-10-21 and 2026-10-22. The 2026-10-21 rows are correct —
that run was manual, at midday, when UTC and New York agreed, which is exactly
why the bug survived its first test. Bars are unaffected at every date.

## 195 names, which is the number originally approved

**EODHD's capacity was measured on 2026-10-23 and the probe was sized against a
guess.** Asking the vendor's own `/user` endpoint rather than inferring a limit
from refusals:

| | |
|---|---|
| `dailyRateLimit` | **100,000** |
| `apiRequests` (that day) | 28 |
| `subscriptionType` | monthly |

So the "undocumented free-tier allowance" the ten-name probe was hedging
against does not exist — the figure matches the 100k/day already recorded in
`PHASE_06_VENDOR_MATRIX.md` §5, now measured rather than stated. **Nothing is
inferred here about licence class**: the quota is a quota, and the use class is
the owner's correction recorded in §1.3.

The owner approved **195 names** on 2026-10-20; Tiingo's free tier forced the
trim to 48. EODHD carries the remainder:

| vendor | names | requests/day |
|---|---|---|
| Tiingo | 46 | 92 |
| EODHD | 149 | 298 |
| | **195** | **390 of 100,000** |

Selected by the rule already recorded above — holding their ticker as of the
run date per §0.2, at least 2,000 price rows, not already covered, ordered by
rows desc then ticker asc. **4,663 candidates met the filter**, so 195 is the
approved number rather than the ceiling; widening past it needs the owner, not
more capacity.

Proved live the same day: 149 symbols, 298 requests, **755 bars on 149
instruments**, no instrument carrying bars from two vendors, and the journal
labelling `eodhd` correctly for the first time.

## `rows_returned` counted the window, not the session — every exit was affected

**Measured 2026-10-23 on the live record**, which is how it was found: every
covered symbol read `rows_returned` of 5 or 6 against a 7-day window. So
*"session 2026-10-21, rows_returned=5"* never meant "this session printed".

`rows = len(outcome.rows)` was the count for the whole `start..end` window,
stored against `session_date = end`. The consequence is the one thing this
record exists to prevent: **a security that stops trading still returns the
previous six sessions**, so it was recorded `ok` rather than silent for six more
days, and the silence a delisting is made of appeared only once the entire
window emptied — dated to that later run's `end` rather than to the session the
security actually stopped.

This affected **every** exit, not an unlucky few. `ABAX` and `AUTO` were caught
only because they had no data anywhere in the window, which is why the defect
survived the two fixes before it.

The count is now rows dated `end`. The window keeps doing its real job —
recovering sessions a sleeping laptop missed — and those bars are written per
row date regardless, so a symbol silent on `end` still has its earlier bars
stored. An early return after counting the silence would have traded a wrong
observation for a missing price.

### The companion rule, without which the fix writes 195 false exits

A session the vendor has not published yet returns no rows for `end` for
**any** symbol. Counting rows-dated-`end` would then mark the whole universe
silent: one false exit per name.

So: **if no symbol printed for the session, no silence is recorded against
it.** A session nothing traded in is a fact about the vendor or the calendar,
not about any security. Fails closed with **no universe-size threshold** —
without cross-sectional evidence that the session happened, a silence is
uninterpretable whether the universe holds one name or a thousand.

Only *successful* zero-row observations are suppressed. `EMPTY` is a success —
the vendor answered and the answer was empty — and that is the row a reader
takes for "this security did not trade". A `REJECTED` or `RATE_LIMITED` outcome
is recorded regardless, because its status already says it is a fact about the
vendor and no reader could mistake it for an exit. Suppressing those would lose
the refusal-versus-silence distinction the whole design turns on.

Exercised live within the hour, mid-session: 149 symbols, 695 bars written for
completed sessions, **0 observations recorded** for the unpublished one.

**And the report had to learn the difference.** That same run printed *"149
requests produced no observation at all, which is a hole in the record"* about
the one outcome that was correct. `CaptureReport.session_unobserved` now
distinguishes "recorded nothing because nothing was owed" from "recorded
nothing by accident" — a report that cries wolf on correct behaviour trains the
operator to ignore the warning that matters.

### The read rule for rows already written — keyed on `asked_at`, not `session_date`

**CORRECTED 2026-10-23.** The first version of this rule said *"observations
dated 2026-10-21, 2026-10-22 and 2026-10-23 carry window counts"*, which keyed
on `session_date` and **became wrong within the hour**: the verification run
wrote 149 correct rows dated 2026-10-22, so that session now holds both
semantics and a rule keyed on the date cannot tell them apart. The
discriminator is `asked_at`, and the two defects have **different cutoffs**.
The whole record is five asks:

| `asked_at` (UTC) | `session_date` | rows | `rows_returned` means | session date |
|---|---|---|---|---|
| 2026-10-21 16:39:16 | 2026-10-21 | 48 | the **window** | correct (manual midday run) |
| 2026-10-22 02:10:01 | 2026-10-22 | 48 | the **window** | **one day late** — describes 2026-10-21 |
| 2026-10-23 02:10:02 | 2026-10-23 | 46 | the **window** | **one day late** — describes 2026-10-22 |
| 2026-10-23 02:17:08 | 2026-10-22 | 10 | the **window** | correct |
| 2026-10-23 17:50:02 | 2026-10-22 | 149 | the **session** | correct |

So, precisely:

* **`rows_returned` is a session count only for asks at or after
  `2026-10-23 17:50 UTC`.** Everything earlier is a window count: a 5 or a 6
  means "the 7-day window held 5 or 6 bars", never "this session printed".
  Those rows are **not** evidence that any symbol traded on the session they
  are dated to.
* **`session_date` is one day late for exactly two asks** — the 22:10 runs
  recorded at `2026-10-22 02:10` and `2026-10-23 02:10`, which describe
  sessions 2026-10-21 and 2026-10-22. The midday manual ask and both later
  asks are correctly dated.
* **Bars are unaffected throughout**, at every date and from both vendors:
  `_write_bars` has always taken each row's own session date from the vendor.

A reader wanting only rows that mean what they say should filter
`asked_at >= '2026-10-23 17:50'`. That is 149 rows on one session, and it is
the honest size of this record's interpretable evidence as of today.
