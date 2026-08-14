# Corpus registry

Which derived corpus is which, and what each may be used for. A scan run is an
isolation boundary (`docs/RUN_ISOLATION.md`); this is the human-readable index
of the boundaries that exist.

## `full-01` — FROZEN, machinery-validation only

| | |
|---|---|
| **status** | **frozen 2026-08-14. Permanent Phase 3/4/5 machinery-validation corpus.** |
| snapshot | `twelve_data-daily-e3ddc03209bb25b4` |
| scope | 78 instruments, ~2010-01-04 to 2026-08-07, 1,295 instrument-years |
| contents | 272,537 patterns / 2,521,049 pattern observations / 341,093 breakout events / 2,696,998 breakout observations |
| gate result | 18 passed, 5 warned, 1 failed, 1 blocked, 0 errored, 1 skipped |
| report | `docs/EMPIRICAL_GATE_REPORT.md` |

**What it may be cited for.** That the Phase 3/4/5 implementation behaves on
sixteen years of real prices as its design claims — causally, deterministically,
with stable identity and a legal lifecycle. And as a regression fingerprint: the
ratios in the gate report are what a later build is compared against.

**What it may never be cited for.** Any cross-sectional statistic, any statement
about how often a pattern works, any threshold. The universe is not
survivorship-safe (`data.survivorship_coverage` FAIL), so every cross-sectional
number over it inherits a bias toward securities that survived to be in it.

**Frozen means:** no further scan writes into it, and no new scan re-uses the
scan id. A re-derivation under changed code gets its own run id and its own
corpus, which the run-scoping in `0012_run_scoped_derivation` enforces at the
database rather than by convention. If it is ever cleared, it is cleared
deliberately with `scripts/clear_scan_output.py` and this entry is updated to
say so.

## `research-01` — NOT YET BUILT

The survivorship-safe corpus. **The only corpus a cross-sectional or economic
statistic may cite.** Blocked on the Phase 6 Milestone 1 vendor decision; see
`docs/PHASE_06_VENDOR_MATRIX.md` and `docs/KIBOT_DATA_PROBE.md`.

### Limitations to be declared here when it is built

Recorded now, before construction, so they are not discovered as omissions later.
Each must be stated in this entry with its measured value at build time:

| limitation | status |
|---|---|
| **pre-2009 fundamental *values* absent** — filing dates, accessions and form types are present from 1994 Q3, but the numbers inside pre-XBRL filings are not | deliberate deferral, not a gap (`RESEARCH_01_DATA_CONTRACT.md` §7.2) |
| **OTC / pink-sheet trading out of scope** — a security that delisted from an exchange and continued OTC is treated as ended | declared modelling decision |
| **measured delisted-roster completeness** against the EDGAR Form 25/15 denominator | to be filled in from the probe (`KIBOT_DATA_PROBE.md` §G) |
| **cohort-survival differential**, 1999–2000 vs 2015–2016 listings | to be filled in — the strongest internal survivorship evidence |
| **exchange coverage** (NYSE / AMEX / Nasdaq NMS / Nasdaq SmallCap) | to be filled in |
| **CIK unmapped fraction, 1998–2008** | to be filled in; unmapped stays `NULL`, never guessed |
| **retention determination per source** | required before the first row from any source is written |

**`research-01` may not be described as survivorship-safe until the control
universe passes** (`DOTCOM_CONTROL_UNIVERSE.md`). If it does not, the corpus is
still usable — under its measured label, with the same discipline `full-01`
already receives.

## `intraday-01` — NOT YET BUILT

The 1-minute corpus serving the **Day** mandate and the **Swing** mandate's
trigger layer. Separate from `research-01` in base timeframe, depth, vendor and
survivorship posture; the two are never merged into one statistical population.
See [`MULTI_TIMEFRAME_MANDATES.md`](MULTI_TIMEFRAME_MANDATES.md) §6.3 and
`KIBOT_DATA_PROBE.md` §H.

| | |
|---|---|
| base timeframe | 1-minute, **regular trading hours**; extended hours stored separately, never merged |
| derives | 5m / 15m / 30m / 1h. **Not 4h** — semantics unresolved, see MULTI_TIMEFRAME_MANDATES §3.2 |
| target depth | 2015-01-01 → present; **2018-01-01 minimum** (below which the COVID dislocation is lost) |
| universe | liquidity-scoped, declared. ~1,500 names ≈ 22 GB columnar for 10 years |
| **survivorship** | **expected BIASED.** Intraday history for long-delisted securities is rarely available at any price |

### Declared in advance, before it is built

**`intraday-01` is expected to fail a survivorship test, and that is acceptable
only because it is declared, measured and never hidden.** When built, this entry
must carry the *measured* delisted fraction, not an estimate.

Consequences that follow, and are binding:

1. **Day-mandate KPIs carry a permanent survivorship caveat** that Swing and
   Retirement KPIs do not.
2. **No Day-mandate result may be compared with a Swing-mandate result** as
   though the two came from the same population.
3. The **forward** 1-minute archive TradeIt accumulates itself *is*
   survivorship-safe by construction — it records what existed on each day it
   ran. The purchased window is a head start; the forward archive is the asset.

## Retired

| corpus | fate |
|---|---|
| `diag-01` | cleared — identity keys mis-attributed, `structure_known_through` unrecordable |
| `diag-02` | cleared — superseded by `full-01`; its purpose (7-instrument diagnostic) is complete |
