# Phase 6 purchase gate

**Written before any vendor data is seen, so the criteria cannot be adjusted to
fit the result.** This is the checklist that decides whether TradeIt spends money
on a historical backfill.

**Current verdict: `NOT YET VERIFIED`.** Nothing purchased. Nothing to purchase
until §1 is satisfied.

---

## 1. What must be known before spending even $1

Six items. All are **free** to establish, and all but the last are answerable
before any account exists.

| # | must know | how | status |
|---|---|---|---|
| 1 | **Post-cancellation retention, in writing, including derived datasets** | Kibot Q23–Q26 (`DATA_RETENTION_RIGHTS.md` §3.2) | licence text says permanent retention is permitted — **USER-VERIFIED**; the *scope* (derived data, research corpora) is **unconfirmed** |
| 2 | **Does the cheap tier actually expose delisted history, and by what mechanism** | Kibot Q1–Q5 | **unknown** |
| 3 | **Total cost of the minimum sufficient product**, including any separate delisted package | Kibot Q2, Q5 | **unknown** — ~$14/mo is the advertised EOD price, not established as sufficient |
| 4 | **Can the full archive be downloaded inside one billing month** | Kibot Q4 | **unknown**. If not, the funding model is *N* months, not one — still cheap, but it must be known on day 1 |
| 5 | **The denominator exists**, so delisted coverage can be *measured* rather than asserted | `EDGAR_DELISTING_DENOMINATOR.md` steps 1–5 | **not built.** Free. This is the blocking work |
| 6 | **The control universe is confirmed against EDGAR** — names, CIKs, dates | `DOTCOM_CONTROL_UNIVERSE.md` §3 | **not done.** Free |

**Items 5 and 6 are the ones that gate everything else**, because without them a
probe cannot distinguish "the vendor has delisted data" from "the vendor has
enough delisted data". They cost nothing but time and depend on no vendor.

> **Do not pay for access before the instrument that measures it exists.**

---

## 2. GO — every one of these must hold

| # | criterion | how measured | threshold |
|---|---|---|---|
| G1 | permanent post-cancellation retention **explicitly** allowed, covering raw files, normalised rows, derived bars, security-master mappings and frozen research corpora | Kibot Q23–Q26, in writing | all five confirmed |
| G2 | daily history reaches **1998-01-01** with universe breadth, not a handful of names | probe B3/B4 | ≥ 80% of securities known to be listed on 1998-01-02 have a bar on or before 1998-01-31 |
| G3 | delisted securities **materially represented** | probe B2 vs the denominator | `bounded_coverage` ≥ 0.45 **and** `matched_coverage` ≥ 0.60 |
| G4 | dot-com failure controls represented | probe C, 30 controls | ≥ 26 of 30 reconstructed; **all 6 short-lived controls present** |
| G5 | **ticker reuse separable** | probe D3, controls BBBY / GM / AOL | zero concatenated series; reuse resolvable by date, permanent id, or a dated roster |
| G6 | raw/unadjusted prices accessible | probe E | unadjusted series delivered as such |
| G7 | adjustment semantics documented **or** recoverable | probe E1–E3 | implied split ratios are piecewise-constant and step on known ex-dates |
| G8 | bulk acquisition practical | probe A2, A6 | scriptable bulk delivery; full backfill completes within the paid window |
| G9 | total cost acceptable | Kibot Q5 | to be judged on the actual figure — a decision, not a threshold |
| G10 | **the denominator is built and the survivorship measurement is real** | `EDGAR_DELISTING_DENOMINATOR.md` | steps 1–5 complete before purchase |

---

## 3. NO-GO — any one of these ends it

| # | condition | why it is fatal |
|---|---|---|
| N1 | retention after cancellation prohibited, **or** silent on derived datasets | the corpus would have to be deleted. This eliminated Sharadar and EODHD |
| N2 | delisted data requires a separate product at an unaffordable price | delisted coverage *is* the problem being solved; without it the purchase buys nothing we do not already have |
| N3 | historical universe **materially survivor-biased** — `bounded_coverage` < 0.25, or vendor cohort-survival curves indistinguishable from a survivors-only roster | the corpus cannot support the research it exists for, and would be actively misleading |
| N4 | ticker reuse cannot be separated — reused tickers delivered as one concatenated series with no date boundary | one series, two companies. `full-01` already failed on exactly this |
| N5 | the archive cannot be downloaded in a reasonable, affordable window | the one-time model collapses into an indefinite subscription |
| N6 | terms prohibit internal research use, or require attribution/disclosure we cannot give | the intended use is not licensed |

**N1 and N4 are absolute.** No amount of data quality compensates for either.

---

## 4. CONDITIONAL — buy, but `research-01` carries a declared limitation

The realistic middle, and the outcome to plan for.

| case | corpus classification | limitation recorded | prohibited conclusions |
|---|---|---|---|
| **C1** — retention confirmed; coverage `bounded` 0.25–0.45 | `partially survivorship-corrected` | measured coverage, by year and lifespan | no unconditional base rates; no cross-era comparison; no failure-tail claims |
| **C2** — coverage good overall but **short-lived names thin** (< 1y lifespan bucket under-covered) | `materially survivorship-corrected` | short-lived deficit quantified | no claims about newly-listed or speculative-cohort behaviour; dot-com conclusions carry the deficit explicitly |
| **C3** — good coverage, but **AMEX / Nasdaq SmallCap thin** | `materially survivorship-corrected` | exchange skew quantified | no small-cap cross-sectional claims; the corpus is large- and mid-cap in substance |
| **C4** — good coverage, but **identity mapping leaves > 30% `UNRESOLVED`** | `materially survivorship-corrected` | both coverage bounds published | the gap between bounds must appear beside any coverage figure |
| **C5** — prices fine, **delisting reasons absent** | no change to the survivorship class | `delisting_reason = unknown` prevalence published | no claims conditioned on *why* a security ended |
| **C6** — 1998 reachable for large caps only, thin before ~2000 | `materially survivorship-corrected`, with a **declared effective start** | per-year breadth published | dot-com *build-up* claims weaken; the 2000–2002 collapse may still be usable |
| **C7** — everything passes but the backfill needs 2–3 months of subscription | none | cost recorded | none — this is a cost decision, not a data one |

**A CONDITIONAL outcome is not a failure.** It is the normal result, and the
discipline is that the limitation is *measured, named and published* rather than
absorbed silently — the same treatment `full-01` already receives.

---

## 5. The four corpus classifications

Assigned from the measurement, not chosen. Defined in
`RESEARCH_01_DATA_CONTRACT.md` §10 with the full prohibited-conclusion table, and
implemented in `tradeit.edgar.denominator.classify_corpus`.

| class | `bounded_coverage` | controls | cohort-survival differential |
|---|---|---|---|
| **`SURVIVORSHIP_SAFE_RESEARCH_GRADE`** | **unset — see below** | 30/30 necessary, not sufficient | present and matching EDGAR shape |
| **`MATERIALLY_SURVIVORSHIP_CORRECTED`** | ≥ 0.45 | ≥ 26/30 | present |
| **`PARTIALLY_SURVIVORSHIP_CORRECTED`** | 0.25 – 0.45 | ≥ 20/30 | weak or partial |
| **`SURVIVOR_BIASED`** | < 0.25 | < 20/30 | absent — cohorts survive like survivors |

**The research-grade threshold is deliberately unassigned.** It is set *after*
the denominator is built and its distribution examined, and *before* any Kibot
result is used for economic testing — never in the same step as evaluating the
vendor. `classify_corpus()` returns `MATERIALLY_SURVIVORSHIP_CORRECTED` with an
explicit reason instead of the top grade until a threshold is passed
deliberately, and a test asserts it.

`SURVIVOR_BIASED` is the classification `full-01` holds today, and its treatment
is already established: **machinery validation only, never cited for a
cross-sectional or economic statistic.**

---

## 6. Decision procedure

```
1  build the EDGAR denominator + verify the controls        free, no vendor
2  send Kibot Q1–Q26 and the TD/FMP retention questions     free, no purchase
3  evaluate answers against §1 and N1/N2/N6                 ── NO-GO exits here
4  one month's access; run the probe on a SAMPLE            ~$14
5  measure §2 G2–G8 and §5 classification                   before any bulk pull
6  ★ report back for approval                               ── the gate
7  bulk download, inside the paid window                    only after approval
8  publish the corpus classification in CORPUS_REGISTRY.md  binding
```

**Step 6 is the gate.** A passed item A never overrides a failed item G, and a
CONDITIONAL result goes back for a decision rather than being resolved
downstream.

---

## 7. What this gate does not decide

- **Intraday.** `intraday-01` is a separate corpus, a separate probe
  (`KIBOT_DATA_PROBE.md` §H) and a separate purchase decision. Non-blocking.
- **Pre-2009 fundamental values.** Deferred by recommendation
  (`RESEARCH_01_DATA_CONTRACT.md` §7.2). No vendor currently qualifies on
  retention, and the standing rule is that none may be accepted if cancellation
  would force deleting `research-01`.
- **Anything about profitability.** Not in scope, not in this phase.
