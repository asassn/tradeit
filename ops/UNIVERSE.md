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
