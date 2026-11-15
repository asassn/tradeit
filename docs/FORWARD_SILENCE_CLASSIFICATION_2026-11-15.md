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
comparison is earliest name in the window against latest, and there is a test
pinning it.

## 5. What this does not settle

- **It is a lead, not an adjudication.** `MANUAL_VERIFIED` still means a person
  read the filing, and `tradeit.edgar.acquire` keeps that boundary structural.
  A verdict here tells a person where to look.
- **It does not find the new ticker.** It says MGPI's registrant renamed; it
  does not establish that `CASK` is the successor. The 2026-11-14 adjudication
  left that `UNRESOLVED` on primary evidence and this does not improve on it.
- **It is not yet wired into the nightly job**, and the index is not yet
  refreshed on a schedule. A stale index is exactly as useless as no index for
  forward work, and this one went stale for three months without a word.
