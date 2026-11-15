# Is a silence an exit? Measured — and the index was three months stale

`ELME_VENUE_BREAK_2026-11-15.md` §2 left one defect open: a vendor re-mapping a
ticker leaves no trace in a price series, because the bars arrive under a symbol
we have stopped asking about. This measures whether it can be detected for free.

**It can, and the blocker was not the algorithm.**

## 1. The defect, stated precisely

A security that stops printing is recorded as silent, and silence is how the
forward record observes a delisting. **A company that renames and changes ticker
also stops printing under the old symbol.** Recording that as an exit puts a
false delisting into the one record built to be survivorship-honest — the
corpus's own defect, reproduced forward, where it was supposed to be impossible.

## 2. It is not hypothetical. It is in the record now

Four symbols are silent in a record 17 sessions old:

| symbol | last print | what EDGAR says | what it is |
|---|---|---|---|
| **ELME** | 2026-11-05 | `25-NSE` 2026-10-26, `15-12G` 2026-11-06 | **a real exit** |
| **MGPI** | 2026-11-10 | no 25, no 15; filer renamed to `SEMPREON, INC`; `4` filed 2026-11-12 | **not an exit** |
| ABAX | never printed | — | dead before the universe was written |
| AUTO | never printed | — | dead before the universe was written |

MGPI renamed and kept trading under another ticker.
`FORWARD_EXIT_ADJUDICATION_2026-11-14.md` reached the same conclusion by hand the
day before — *"do not record a survivorship exit for MGPI"* — after a long
document of manual filing reads. **This measurement agrees with the person who
read the documents**, which is the only check that matters for a classifier
meant to stand in for one.

## 3. Why it could not be automated before — and nothing said so

`tradeit.edgar.exit_cause.classify_exit` has classified exits from EDGAR
evidence for months. Run against the corpus it returned `unresolved` for MGPI
and missed ELME's `15-12G` entirely.

**The local EDGAR index was three months stale.** Measured 2026-11-15:

| | held | |
|---|---|---|
| 2026 QTR3 | 2026-07-01 → **2026-08-13** | downloaded mid-quarter, 2026-08-14 |
| 2026 QTR4 | — | **absent** |
| corpus `filings` | max `filed_at` **2026-08-13** | ingested from the above |

Every forward silence falls in that hole. The classifier was not wrong; it was
answering from missing data, and **reporting "no exit found" when the truth was
"the index predates the event"**. That is the failure this project treats as the
expensive one: the expected answer arrived, and it was not an answer to the
question asked.

Refreshed via the project's own `edgar fetch-recipe` (free; SEC needs only an
identifying User-Agent). QTR3 now complete to 2026-09-30, QTR4 to 2026-11-13.

**15 filings were withdrawn from SEC's own QTR3 index between the two
downloads** — all ownership, 13G and 13F forms, none exit-relevant, so the
denominator is untouched. The superseded file and the withdrawn lines are
preserved under `TradeItData/edgar/superseded/`, because a refresh that
destroyed them silently would have destroyed evidence.

## 4. What was built

`tradeit.forward.silence` classifies a silence against the index, report only:

| verdict | meaning |
|---|---|
| `EXIT_CORROBORATED` | a Form 25 or 15 within 45 days of the stop |
| `RENAMED_AND_STILL_FILING` | no exit form, and the filer name changed across the window |
| `STILL_FILING` | no exit form, filings continue under the same name |
| `UNRESOLVED` | **the index cannot answer**, and the reason says which gap stopped it |

**Index age is checked first and fails closed.** If the newest filing the index
holds predates the silence, the verdict is `UNRESOLVED`. *"The index cannot speak
to this"* and *"the filings show no exit"* are different answers and are never
returned as the same one.

`UNRESOLVED` deliberately does **not** count as evidence the company is alive.
Not knowing is not knowing.

### A bug the first version shipped with

The obvious rename test — *does the filer name after the stop differ from the
name before it* — **misses MGPI**, the case the module was written for. The
registrant began filing as `SEMPREON, INC` on 2026-11-06 and the vendor kept
printing MGPI bars until 2026-11-10, so the new name sat on both sides of the
stop and the straddle test saw no change.

**A ticker follows a rename by days or weeks; it does not move the same
morning.** Any test anchored on the stop misses the ordinary case. The
comparison is earliest name against latest, and there is a test pinning it.

### And the bound that keeps it from firing on everything

The comparison is **restricted to 180 days before the stop**, which is not a
detail. Measured 2026-11-15, ELME's own registrant renamed four years before it
dissolved: CIK 104894 files as `WASHINGTON REAL ESTATE INVESTMENT TRUST` until
2022-10-03 and as `Elme Communities` from 2022-10-17. An unbounded comparison
sees a rename in almost any multi-year window and would read a 2026 dissolution
as a 2022 ticker move.

ELME survives that only because the exit check runs first. **A company that
renamed years ago, kept filing, and went quiet with no Form 25 or 15 would
not** — it would be reported as a probable ticker move on four-year-old
evidence. 180 days is generous for a ticker that lags a rename by weeks, and it
is not a claim that older renames never matter, only that they are not evidence
about *this* stop.

## 5. What this does not settle

- **It is a lead, not an adjudication.** `MANUAL_VERIFIED` still means a person
  read the filing, and `tradeit.edgar.acquire` keeps that boundary structural.
  A verdict here tells a person where to look.
- **It does not find the new ticker.** It says MGPI's registrant renamed; it
  does not establish that `CASK` is the successor. The 2026-11-14 adjudication
  left that `UNRESOLVED` on primary evidence and this does not improve on it.
- **The index is not yet refreshed on a schedule.** It is now read nightly and
  its age is printed nightly, so a repeat of the three-month gap becomes visible
  within a day — but nothing yet *fixes* it. Refreshing is two HTTP requests and
  free; automating it is the obvious next step and is not done here.

## 6. Wired in, 2026-11-15

`forward_daily.py` runs it after each capture, alongside the discontinuity scan.
Report only; it never changes the exit code, because a job that goes red on a
correct observation is a job whose output gets switched off.

```
PYTHONPATH=src .venv/bin/python -m tradeit.cli forward silences
```

**The index's age is printed every run, not only when something fails.** That
line is the whole lesson of this document reduced to its cheapest form: a stale
input produced confident answers for three months and nothing complained.

Live output, 2026-11-15:

```
filing index reaches 2026-11-13 (2 day(s) old)

MGPI  renamed_and_still_filing  ... 'MGP INGREDIENTS INC' to 'SEMPREON, INC' ...
ABAX  unresolved                the symbol never printed, so there is no stop to explain
AUTO  unresolved                no CIK resolves for this symbol
ELME  exit_corroborated         25-NSE 2026-10-26, 15-12G 2026-11-06
```

`AUTO` is worth reading twice. It resolves to **two** securities under two
issuers — a reused ticker — so no CIK is claimed and the verdict is
`UNRESOLVED`. Picking either would attach one company's filings to another's
silence, which is the worst available failure for a check whose entire job is
identity. Fail closed.

Two path bugs were found wiring this up, and both are pinned by tests: reading
the whole 32-year archive instead of the quarters covering the window, and
`sqlite:////research01.sqlite` resolving to the filesystem root rather than the
working directory — an error whose message never names the path it tried.
