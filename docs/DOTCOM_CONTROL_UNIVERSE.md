# Dot-com control universe

The securities `research-01` must be able to reconstruct before it may be
called survivorship-safe. **Not a research sample** — a *test fixture* for the
data, in the same way the survivorship controls in `full-01` were.

## 1. Why controls, and why these

`full-01`'s survivorship check failed for a reason worth repeating: the corpus
looked complete because everything in it had survived. The controls below are
chosen so that a survivorship-biased corpus **cannot** pass — every one of them
either disappeared, changed identity, or had its ticker reused.

The strict philosophy carries over unchanged:

> **Absence is not evidence that a company never existed.** A control that
> cannot be retrieved is a FAIL with a recorded reason, never a silent omission.

Each control is stated as a *class of event to reconstruct*, with named
candidates. Names and dates below are from general knowledge and are
**UNVERIFIED**; the first job of the probe is to confirm each one against EDGAR
before it is relied on as a fixture.

## 2. The control classes

| # | class | what it proves | candidates (UNVERIFIED, confirm via EDGAR) |
|---|---|---|---|
| 1 | **major survivor, continuous** | baseline: long history, splits, no identity change | AAPL, MSFT, INTC, CSCO |
| 2 | **dot-com bankruptcy, liquidated** | delisted securities exist at all; `bankrupt_liquidated` | Pets.com (IPO Feb 2000, wound up Nov 2000), eToys, Webvan |
| 3 | **dot-com collapse, survived as a shell / acquired** | delisting reason is distinguished from bankruptcy | theglobe.com, drkoop.com |
| 4 | **acquired at the peak** | `acquired_by` lineage, series *ends* rather than continuing | Broadcast.com (Yahoo, 1999), GeoCities (Yahoo, 1999), Netscape (AOL, 1999) |
| 5 | **mega-merger changing identity** | ticker change + lineage without loss of history | AOL / Time Warner (2001), Compaq → HP (2002) |
| 6 | **telecom/accounting failure** | large-cap can vanish; `bankrupt_liquidated` at size | WorldCom (2002), Global Crossing (2002), Enron (2001) |
| 7 | **financial-crisis failure** | the mechanism is not dot-com-specific | Lehman Brothers (2008), Washington Mutual (2008), Bear Stearns (acquired 2008) |
| 8 | **ticker reuse across issuers** | the alias exclusion constraint actually bites | any ticker held by a class-2/6 name and later reissued — **enumerate from the data, do not assume** |
| 9 | **large split history** | raw-vs-adjusted semantics survive extreme factors | AAPL (2000, 2005, 2014, 2020), NVDA |
| 10 | **reverse split / distress** | reverse splits are not mistaken for price moves | any class-2/3 name that reverse-split before delisting |
| 11 | **recent IPO** | the young end of the universe is handled | any 2024–2026 listing |
| 12 | **known recent reuse** | the case `full-01` actually hit | **BBBY** — the ticker was reused after Bed Bath & Beyond's delisting; this is the one control already *proven* to matter |

Class 12 is not hypothetical. It is the exact failure that made `full-01`'s
survivorship control pass on another company's prices, and it is the single most
important control in the list.

## 3. What each control must yield

For a control to count as reconstructed, `research-01` must supply **all** of:

| | requirement |
|---|---|
| identity | a stable `instrument_id` and, where the issuer filed, a CIK |
| aliases | every ticker it traded under, with `valid_from`/`valid_to` |
| prices | daily bars spanning at least the last 250 sessions before delisting |
| end of life | `delisting_date` **and** a `delisting_reason` that is not `unknown` |
| lineage | for classes 4, 5, 8: the counterparty security, linked |
| corporate actions | every split and reverse split in the window |
| fundamentals | at least one filing with `accession`, `filed_at` and `period_end`, where the issuer filed |
| filing-date fidelity | vendor `filed_at` matching EDGAR's `filed` within the expected lag |

**A control missing any of these is a FAIL with a reason** — `not_offered`,
`refused`, `empty`, or `mismatch` — recorded in the capability index, never
dropped.

## 4. How the controls are used

1. **Before purchase**, in the probe (`PHASE_06_IMPROVEMENT_PLAN.md` §7): a
   subset, to establish whether the vendor can reconstruct them at all.
2. **After import**, as a gate check over `research-01` — a survivorship check
   with real teeth, extending the existing `SurvivorshipStatus` vocabulary
   (`COVERED` / `INSUFFICIENT_HISTORY` / `NOT_FOUND` / … ) which already
   distinguishes "the vendor has no history" from "the security did not exist".
3. **Permanently**, as a regression fixture: any future re-import or vendor
   change re-runs them.

## 5. The trap these controls exist to catch

A corpus can look complete and be systematically wrong in a way no aggregate
reveals:

- prices for a bankrupt company that stop at delisting **but whose ticker later
  belongs to someone else** — spliced, one series, two companies;
- a merged company whose history silently continues into the acquirer's;
- a reverse-split price series read as a collapse;
- fundamentals for a 1999 filing stamped with a *period-end* date, making the
  whole dot-com window quietly non-point-in-time.

Every one of those is invisible in a summary statistic and obvious in a control.
That is why the controls are named in advance, before any vendor is chosen, and
why the acceptance criteria are written down before the data arrives.
