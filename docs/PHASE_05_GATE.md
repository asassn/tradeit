# Phase 5 Empirical Data Access & Validation Gate — Report

**Status: Outcome B.** Real market data could not be obtained in this
environment. The complete offline infrastructure is built, tested and
documented; no empirical result is claimed, because none was measured.

This gate was authorized to stop the project adding increasingly sophisticated
analytical layers on top of exclusively synthetic market data. It is not a new
numbered phase. Phase 6 is not begun and is not authorized.

---

## 1. The two legitimate outcomes, and which one this is

The authorization named exactly two acceptable endings:

- **Outcome A** — real data is available, so measured results are reported.
- **Outcome B** — access is still blocked, so the offline infrastructure is
  completed, exact instructions are given, and what remains blocked is stated.
  *"Do NOT invent empirical results. Stop and report what remains blocked."*

**This is Outcome B.** Nothing in this document, in the code, or in any
generated report claims a measured property of real markets. The one place a
reader might expect such a claim — the validation harness's own conclusion —
prints `NOT A VALIDATION` and the reason, and exits non-zero.

---

## 2. Data access: what was attempted and what happened

Two hosts were probed once each, at the start of this work and once more before
writing this report:

| Host | Result | Proxy record |
|---|---|---|
| `stooq.com:443` | HTTP `000`, `CONNECT tunnel failed, response 403` | `connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)` |
| `api.tiingo.com:443` | HTTP `000`, `CONNECT tunnel failed, response 403` | same |

The environment's agent proxy records these as policy denials. Its own
documentation is explicit: *"Do not retry or route around it — report the
blocked host."* The gate's item 8 says the same thing in different words: *"Do
not repeatedly probe the same blocked endpoints unless the environment
changes."*

So they were not probed further. No credentials were requested, no alternative
host was tried in the hope that a different name would be permitted, and no
mirror or scraping path was attempted. Two probes, separated by the whole of
this work, with the second confirming nothing had changed.

**What this does and does not mean.** It means *this container* cannot reach
those hosts. It does not mean the data is unobtainable — a person running the
same commands on their own machine, or an environment configured with the hosts
allowed, would proceed straight to Outcome A. The infrastructure below is built
so that becomes a single command rather than a project.

---

## 3. The architectural invariant: breakout state is not trade eligibility

The gate's headline requirement, and the one with the longest reach.

Phase 5's own characterisation established that this is not a theoretical
concern. On the adversarial corpus at n=200, `low_volume_drift` — price drifting
above an arbitrary level on collapsing volume — reaches `CONFIRMED` **96%** of
the time, and `no_prior_resistance` **92%**. Their median breakout quality is 49
and 55 against 93 for a clean break, and confidence on a single-touch unattached
level falls from 83 to 47.

**The evidence separates them cleanly; the state does not separate them at
all.** A downstream stage branching on `state == CONFIRMED` would treat a
textbook base breakout and a directionless drift identically, while believing it
had consulted the analysis.

Three mechanisms now enforce the invariant, because a rule stated only in prose
is a rule that decays. All three are in `ADR-0025`.

**`EvidenceBundle`** is the supported way to consume a breakout. It carries all
thirteen fields the gate names across both layers — pattern quality, pattern
coverage, pattern state, boundary confidence, breakout quality, confirmation
score, breakout coverage, breakout confidence, breakout state, relative
strength, sector, regime — plus explicit `None` slots for the two Phase 5 cannot
fill: `fundamental_score` (Phase 6) and `portfolio_fit_score` (Phase 7). Present
as slots rather than absent, so a consumer reaching for them gets a named gap
and a report can count how many decisions were made without them.

The bundle has **no aggregate**. No total, no rank, no verdict, no
`is_tradable`. `FORBIDDEN_DECISION_TERMS` is walked over the dataclass fields,
the public attributes and the enum members by test, because a convenience method
that summed the fields would become the decision by default.

**`BoundaryKind`** records where a level came from —
`STRUCTURAL_PATTERN_BOUNDARY`, `MANUAL_BOUNDARY`, `EXPERIMENTAL_BOUNDARY`,
`OTHER` — stored on `breakout_events.boundary_kind` with its own index
(migration `0007`). The tag is about provenance, not quality; `OTHER` exists so
that "we do not know where this came from" is expressible. `manual_boundary()`
**refuses to be tagged structural**, and that refusal is the load-bearing part:
a keyword argument is exactly how a research level would otherwise enter the
production population indistinguishably.

**The production monitor accepts only structural boundaries**, and only from
`PRODUCTION_PATTERN_STATES` = {`MATURE`, `NEAR_BREAKOUT`,
`BROKEN_OUT_UNCONFIRMED`}, checked at construction. An operator may monitor
*fewer* states; nobody may add `FORMING`, whose boundary is still resolving,
because once such events are in the dataset every rate computed from it
describes the monitor rather than the market.

**What was rejected**, and why it is worth naming: *a quality floor inside
Phase 5 that suppresses low-quality confirmations.* That is the tempting fix and
it is wrong twice — it puts a Phase 7 threshold in a Phase 5 module, and it makes
`CONFIRMED` mean "good", collapsing layers this architecture separated on
purpose and destroying the dataset Phase 7 needs in order to learn what a
low-quality confirmation is worth.

---

## 4. What was built

### 4.1 The package format (`src/tradeit/data/packages/`)

Fourteen datasets defined **by meaning rather than by any vendor's column
names**, each recording which Phase 3/4/5 validations supplying it unlocks, so a
package owner learns what a missing file costs at import time rather than by
noticing a validation silently reported nothing.

A manifest carries per-file SHA-256, declared coverage, timezone, known
limitations, licence note, and an `adjustment_policy` with **no default** — the
one field whose absence would let adjusted closes load as raw prices and produce
a history nobody could have seen.

### 4.2 The pipeline

Four stages kept as separate types, each containing the previous one, so "what
did the file actually say here?" is answerable at any depth without a join:
**raw → normalized → validated → point-in-time**.

Three behaviours are deliberate and pinned by test:

- **Ambiguous dates are refused, not guessed.** `03/04/2021` reads two ways and
  no amount of sampling settles it; the row quarantines with a message naming
  the manifest field that fixes it.
- **No row is ever discarded.** Every line is imported or quarantined with its
  file, line, stage and reason. The counts are asserted to sum.
- **Every change is recorded.** Each deviation from source text is a correction
  naming field, raw value, substituted value, rule and reason. A row with no
  corrections provably changed nothing.

Raw files are never modified; the digest is verified before a row is read, and
the file on disk plus its recorded hash *is* the raw preservation. Copying every
byte into the database to call it "preserved" would cost storage without adding
a guarantee the digest does not already give.

### 4.3 Point-in-time (`pointintime.py`)

**A quarter ending 31 March does not become knowable on 31 March, under any
configuration.** There is no flag that relaxes it. A filing timestamp dated
within a day of its own period end is refused; a fundamental with no publication
timestamp is quarantined **by default**; the deadline-based estimate is opt-in,
marks every affected row `ESTIMATED`, counts them per dataset, and says in the
report that results resting on them measure the lag rule as well as the market.

The estimated lags are **filing deadlines, not typical filing dates**, so they
place facts later than they were truly knowable — costing a strategy
opportunities it might really have had, never granting one it did not.

Two subtleties the implementation surfaced and now states out loud:

- **Reference datasets** (instrument master, exchanges) have no date of their
  own and are placed at the package export date. The report says so once per
  dataset, because an instrument master exported today lists today's roster with
  today's classification — a real point-in-time hazard living inside a reference
  file, and the reason `sectors` and `symbol_mappings` are interval-based.
- **Earnings dates without an announcement timestamp** are treated as knowable
  only on the session they occur. That is deliberately conservative: proximity
  filters then see nothing coming, which costs caution the strategy could have
  exercised, rather than granting a calendar it never had.

### 4.4 Storage and provenance (migration `0008`)

`data_packages` (snapshot id, manifest digest, adjustment policy, declared
coverage next to observed coverage, the full import report), `data_package_files`
(per-file digest and **column mapping**, because the mapping is the
interpretation), `import_corrections` (every traceable deviation), and four new
columns on `quarantined_rows` giving each rejection its package, stage, file and
line.

`snapshot_id` derives from the manifest digest and the observed row counts, so
two imports of the same bytes share an id and an aborted or row-limited run
cannot be mistaken for a complete one.

### 4.5 The validation harness (`src/tradeit/validation/`)

**18 checks**: 9 data-layer, 3 Phase 3, 2 Phase 4, 4 Phase 5. The data checks
are gates rather than diagnostics — if the point-in-time check fails, no Phase
3/4/5 result over that snapshot means anything.

Two honesty properties, both pinned by test:

- **Blocked is never a pass.** A check that could not run has its own status,
  counted first in every conclusion. A run with any blocked check is not
  evidence, however many others passed. A summary reading "20 passed" over
  eighteen skips is a lie told with true numbers.
- **No check may compute a performance statistic.** `FORBIDDEN_MEASURES` is
  matched against every result the runner collects — its identifiers, its
  evidence keys nested to any depth, and every line of prose it will print —
  and every report ends by naming what it did not measure. The match resolves
  a word's sense before accusing: `edge` is forbidden as a trading edge and
  permitted as a stated graph edge, and an English forbidden word in block
  capitals is read as a ticker. See `docs/EMPIRICAL_SCAN.md` for why, and
  `TestTheTransitionEdgeCollision` for the cases that pin it.

### 4.6 Commands

```
tradeit data package-spec | datasets | template | inspect | import
tradeit validate --snapshot <id>
```

`validate` exits non-zero when the run is not citable, because a CI job that
treats a half-blocked run as success is a CI job that will eventually approve
one.

---

## 5. What the harness found by being run

The infrastructure was exercised end to end against a constructed package
(1,515 rows across three datasets) — not to produce a result, but to find out
whether the machinery works. It found four things, three of them in the
machinery itself.

**The performance guard matched `edge` inside `knowledge_time_ordering`** and
refused the most important check in the suite. Substring matching was too crude;
it now matches on word boundaries. A guard that cries wolf gets deleted, so it
has to be right.

**The warm-up check was written with the polarity backwards.** A series defined
*earlier* than its declared warm-up is conservative and harmless; one still
undefined *after* it is the real defect, because a consumer trusting the registry
gets NaN where a number was promised. Only the second fails now.

**`warmup_periods` does not mean one thing across the indicator registry.** For
`sma_20` it is the count of values consumed, so the first defined index is 19;
for `roc_20` it is the lag, and the first defined index is 20. Neither reading
permits a lookahead, so this is a documentation inconsistency rather than a
defect. The check accepts either and the inconsistency is recorded rather than
papered over: a reader assuming one convention holds everywhere will be off by
one, and off-by-one at a warm-up boundary is how a NaN reaches a comparison.

**Two bugs in the operator-facing commands**, both found by using them rather
than by reading them: `build_manifest_template` emitted `known_limitations` after
`[coverage]`, which TOML reads as `coverage.known_limitations`, so every
generated template failed validation pointing at the wrong field; and
`load_manifest` leaked pydantic and tomllib exceptions as tracebacks instead of
naming the file and the fix.

**And one finding outside the gate's scope that mattered more than any of them.**
`.gitignore` carried an unanchored `data/`, which also matched
`src/tradeit/data/`. The entire data package — providers, registry, quality
checks, validation universe — had never been committed. A fresh clone did not
build. The rule is now anchored to the repository root and the missing source is
committed.

---

## 6. The validation universe

Extended from 85 to **91 instruments**. Everything that was there stresses
*price* handling; nothing in it would have caught a fundamental pipeline that
assumes a fiscal quarter ends on a calendar quarter boundary.

Added: a `fiscal_calendar` category with six non-December filers spread across
the calendar (CRM January, TGT retail 52/53-week, INTU July, ACN August, HPQ
October, MU late August). AAPL, MSFT, NVDA and WMT also have non-December fiscal
years and are already listed under other categories; the file is one row per
instrument, so they are not repeated.

Added and **deliberately left empty**: a `restatement` category. The restatement
case is the one that separates a real point-in-time store from a history table,
and it belongs in the universe. What does not belong is a company and a quarter
asserted from memory — the file's own header says anything without a real,
checkable historical event does not go in it, and EDGAR is not reachable from
here. The category carries instructions for populating it: find the amended
filing, record the periods restated and **both** filing timestamps, because the
contract under test is that a read between them returns the original value.

Until then the restatement contract is exercised by constructed fixtures in
`tests/unit/test_phase6_contract.py`, which is weaker evidence and is labelled
as such.

---

## 7. Phase 6 contract tests

Eighteen tests written **before** Phase 6 exists, so they constrain what may be
built rather than describe what was. Nothing in them scores a fundamental or
ranks a company; a guard test asserts that no module-level helper acquires a
name suggesting either.

They pin four guarantees against the existing repository layer, and all four
hold today:

1. A fact is invisible before its filing instant — including the case where the
   filing lands at 20:15 and a clock at 19:00 the same day must not see it.
2. A restatement does not rewrite the past: a read between the original filing
   and its amendment returns the **original** value, both versions are retained,
   and they share a fiscal identity.
3. `period_end` is stored, not derived. A September-fiscal-year filer's FY2023
   Q1 ends in December 2022, and the filing lag is measured from the real period
   end rather than from the fiscal-year label.
4. An `ESTIMATED` timestamp stays labelled and is distinguishable by query.

**Filing-timestamp linkage design.** When a fundamentals export carries no
publication instant, one of the columns in `REPORTED_TIME_COLUMNS` — supplied
directly, or joined from the separate `FILINGS` dataset on accession or on
(instrument, period_end, form_type) — is what turns an `ESTIMATED` row into a
`REPORTED` one. Canonical contract names are consulted first, because
normalization renames source columns onto them before the list is used.

---

## 8. What is NOT claimed

Stated plainly, because a reader who sees a large amount of green will otherwise
fill the gap with the flattering assumption.

- **No real-market accuracy figure**, for patterns or for breakouts. None was
  measured.
- **No claim that confirmed breakouts are profitable.** Nothing in this gate
  computes a future return, a win rate, an expectancy, a CAGR, a Sharpe ratio, a
  drawdown or a profit. `FORBIDDEN_MEASURES` enforces it mechanically over every
  check result.
- **No threshold was tuned.** No historical search was run for the best relative
  volume threshold, the best confirmation window, or the best anything. The
  characterisation numbers quoted in §3 come from Phase 5's synthetic
  adversarial corpus and describe the engine's behaviour on constructed inputs,
  not on markets.
- **No empirical validation result.** The harness has never run against real
  data. Its output on a constructed package is machinery verification, and this
  document does not present it as anything else.
- **No vendor capability is asserted as verified.** `docs/VENDOR_EVALUATION.md`
  now labels every claim `VERIFIED` / `DOCUMENTATION CLAIM` /
  `NEEDS VERIFICATION` / `UNKNOWN`, and everything in its vendor sections is one
  of the latter three. Pricing is `UNKNOWN` and no figures are quoted: an
  order-of-magnitude number invented from memory becomes a quotation the moment
  it is written down.

---

## 9. Exactly what to do next

For whoever has, or can obtain, real data. Assumes a checkout of this branch and
the project's dependencies installed.

**Step 1 — get the data.** Read `DATA_REQUIRED.md`. If you supply one thing,
supply daily unadjusted bars with an instrument master **including securities
that no longer trade**. That combination unlocks pattern detection, breakout
detection, indicators, multi-timeframe construction and point-in-time replay.

**Step 2 — lay it out.** One directory, your files in it:

```bash
python -m tradeit.cli_data data template ./my-export \
    --name my-export --provider some-vendor
```

Fill in every `PLEASE_SET` — they are left unparseable so an unfilled template
cannot be imported by accident — and map your column names in each `[[files]]`
block. `docs/DATA_PACKAGE_SPEC.md` §2 explains every field.

**Step 3 — look at it before importing it.**

```bash
python -m tradeit.cli_data data inspect ./my-export
```

Verifies every digest and prints which validations your datasets do and do not
unlock, before anyone waits for a ten-million-row read.

**Step 4 — dry run.** Every stage executes, every quarantine decision is made,
nothing is written:

```bash
python -m tradeit.cli_data data import ./my-export --dry-run --jsonl ./out
```

Read the quarantine. A rate above a few percent is a column mapping mistake, not
dirty data, and the report names the columns your file has next to the ones the
manifest maps.

**Step 5 — import.** Needs a configured PostgreSQL (`TRADEIT_DATABASE__DSN`) and
`alembic upgrade head`:

```bash
tradeit data import ./my-export --code-version "$(git rev-parse --short HEAD)"
```

It prints a `snapshot_id`. That id is what every result cites.

**Step 6 — validate.**

```bash
tradeit validate --snapshot <snapshot-id> --json report.json
```

Expect failures. `data.survivorship_coverage` will fail unless your package
contains the delisted names; that is the check working. Exit code is non-zero
until every check ran and none failed.

**Step 7 — then, and only then, report.** Replace §2 and §8 of this document
with what was measured. Keep §8's list of what is still not claimed, because
Steps 1–6 do not produce a performance number and are not meant to.

**Step 8 — request Phase 6 authorization.** Not before.

---

## 10. What remains blocked

| Blocked | Because | Unblocked by |
|---|---|---|
| Every empirical measurement in this gate | No real market data reachable from this environment | A data package (Step 1 above), or an environment allowing the provider hosts |
| `stooq.com`, `api.tiingo.com` | Proxy policy denial, HTTP 403 on CONNECT, confirmed twice | An environment configuration change; not something to route around |
| Parquet package files | `pyarrow` not installed | `pip install pyarrow`, or export CSV.gz — which streams, and Parquet does not |
| Real restatement fixtures | EDGAR unreachable; asserting a company's accounting history from memory is not acceptable evidence | Reading the filing index and populating the `restatement` category as its comment instructs |
| Vendor capability verification | No trial run; §6 of `VENDOR_EVALUATION.md` is the test to run | A trial account and the acceptance test |
| The CLI's database-backed commands in this container | Settings require PostgreSQL; none could be initialised here | Any environment with PostgreSQL. The spec, template, inspect and dry-run paths need no database and were exercised |

---

## 11. State of the code

- **1,863 unit tests pass.** `ruff` and `mypy --strict` clean on `src/tradeit`.
- Migrations `0007` (boundary provenance) and `0008` (data packages, corrections,
  quarantine provenance) added.
- New: `src/tradeit/data/packages/` (11 modules), `src/tradeit/validation/`
  (6 modules), `src/tradeit/breakouts/eligibility.py`, `src/tradeit/cli_data.py`.
- New documents: `DATA_REQUIRED.md`, `docs/DATA_PACKAGE_SPEC.md`, this report,
  `docs/adr/0025-breakout-state-is-not-trade-eligibility.md`.
- `docs/VENDOR_EVALUATION.md` updated with the four-label evidence discipline.

---

## 12. Stop

Phase 6 is not begun. No fundamentals scoring, no portfolio scoring, no position
sizing, no trade execution, no BUY/SELL recommendation exists anywhere in this
codebase, and the `EvidenceBundle` mechanism exists specifically to make adding
one a visible act rather than a convenient one.

Awaiting explicit authorization.
