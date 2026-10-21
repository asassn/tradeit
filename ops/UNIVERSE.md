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

That is a day of work and it is not blocking. The 48 start accumulating now.
