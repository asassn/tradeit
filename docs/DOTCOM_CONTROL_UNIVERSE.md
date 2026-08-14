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

## 2b. The expanded 1998–2002 control set

The twelve classes above are the *shape* of the test. This section is the
*density*: because 1998–2002 is the window `research-01` exists to cover, and
because it is the window where a vendor's roster is most likely to be thin, the
controls there are enumerated far more finely than elsewhere.

**All names, tickers and dates below are from general knowledge and are
UNVERIFIED.** The first job is to confirm each against EDGAR — company name, CIK,
form 25/15 filing, and dates — before any of it is relied on as a fixture. A
candidate that cannot be confirmed against EDGAR is dropped from the fixture, not
guessed at.

### 2b.1 Short-lived listings — the hardest case, and the most diagnostic

Listed **and** terminated inside 1998–2002. These are the population that
survivorship bias is made of, and the ones a thin roster loses first.

| candidate | listed | ended | why it is the sharpest test |
|---|---|---|---|
| Pets.com (`IPET`) | Feb 2000 | wound up Nov 2000 | ~9 months of sessions. If a corpus has any short-lived control, it should be this one |
| eToys (`ETYS`) | May 1999 | Ch. 11 Mar 2001 | IPO-to-bankruptcy inside the window |
| Webvan (`WBVN`) | Nov 1999 | Ch. 11 Jul 2001 | large raise, total loss |
| theglobe.com (`TGLO`) | Nov 1998 | collapsed 2001 | the record first-day pop; survived as a shell — tests class 3 vs class 2 |
| drkoop.com (`KOOP`) | Jun 1999 | 2001–02 | going-concern warning then absorption |
| Garden.com, MotherNature.com, Value America | 1998–99 | 2000–01 | small, thin, forgettable — exactly the omission risk |
| NetZero (`NZRO`) | Sep 1999 | merged into United Online 2001 | short life ending in a *merger*, not a failure |
| Buy.com (`BUYX`) | Feb 2000 | taken private 2001 | `taken_private` as a distinct delisting reason |

**Acceptance:** at least the first four must be reconstructible. A corpus
returning `NOT_FOUND` for all of the last four while returning WorldCom and Enron
cleanly has kept the headlines and lost the population — that is
`KIBOT_DATA_PROBE.md` §G3.

### 2b.2 Infrastructure and telecom failures, 2001–2002

The larger, longer-lived half of the collapse. Their absence would be
unmissable; their *presence* proves very little on its own, which is why they are
listed separately from 2b.1.

| candidate | ended | reason to expect |
|---|---|---|
| Enron (`ENE`) | Ch. 11 Dec 2001 | `bankrupt_liquidated` at large-cap size |
| WorldCom (`WCOM`) | Ch. 11 Jul 2002 | emerged as MCI — `bankrupt_reorganised`, then a *new* identity |
| Global Crossing (`GX`) | Ch. 11 Jan 2002 | reorganised under new ownership |
| Adelphia (`ADLAC`) | Ch. 11 Jun 2002 | accounting failure, Nasdaq delisting |
| Excite@Home (`ATHM`) | Ch. 11 Sep 2001 | ticker later reused — feeds class 8 |
| PSINet (`PSIX`) | Ch. 11 May 2001 | infrastructure failure |
| Exodus Communications (`EXDS`) | Ch. 11 Sep 2001 | same |
| Winstar (`WCII`), Teligent (`TGNT`), Rhythms NetConnections (`RTHM`), NorthPoint (`NPNT`), Metricom (`MCOM`), 360networks | 2001 | the CLEC/broadband cohort — a *cluster* test, not individual names |
| Kmart (`KM`) | Ch. 11 Jan 2002 | non-tech failure inside the same window; guards against a tech-only roster |

**The CLEC cluster is deliberately listed as a group.** The test is not "is
Winstar present" but "how many of these six are present" — a count, reported as a
count.

### 2b.3 Acquisitions at or near the peak

The series must **end**. A corpus that continues an acquired company's prices
into the acquirer is the second-worst failure after splicing, and it is silent.

| candidate | acquirer | date |
|---|---|---|
| Broadcast.com (`BCST`) | Yahoo | 1999 |
| GeoCities (`GCTY`) | Yahoo | 1999 |
| Netscape (`NSCP`) | AOL | 1999 |
| Infoseek (`SEEK`) | Disney | 1999 |
| Lycos (`LCOS`) | Terra Networks | 2000 |
| MP3.com (`MPPP`) | Vivendi | 2001 |
| Ask Jeeves (`ASKJ`) | IAC | 2005 — just outside the window, retained as a late-collapse control |

### 2b.4 Identity-changing mergers, 1998–2002

Ticker changes and lineage. History must be preserved under **one**
`instrument_id` where the economic entity continued, and split into two where it
did not.

| event | date | what it tests |
|---|---|---|
| AOL + Time Warner (`AOL` / `TWX`) | 2001 | the canonical identity change of the era — **and** `AOL` is later reused (2b.6) |
| Compaq → HP (`CPQ` → `HPQ`) | 2002 | large-cap absorption |
| Bell Atlantic + GTE → Verizon (`BEL` → `VZ`) | 2000 | rename with continuity |
| SBC + Ameritech | 1999 | precursor to the `T` reuse case |
| Exxon + Mobil (`XON` → `XOM`) | 1999 | non-tech, same mechanism |
| Travelers + Citicorp → Citigroup (`CCI` → `C`) | 1998 | ticker `C` reassignment |
| JDS + Uniphase (`JDSU`) | 1999 | merger *then* an extreme reverse split (2b.5) |
| Daimler + Chrysler | 1998 | cross-border, US listing ends |

### 2b.5 Splits and reverse splits in the window

| candidate | action | why |
|---|---|---|
| Qualcomm | 4:1, Dec 1999 | large forward split at the peak |
| Cisco | 2:1 in 1998, 1999, 2000 | three splits inside the window |
| Microsoft | 2:1 in 1998, 1999 | same |
| Sun Microsystems, EMC, Nortel, Yahoo | 2:1 splits 1999–2000 | the cohort |
| JDS Uniphase | **1:8 reverse split, 2006** | the reverse-split control with the largest factor |
| Priceline | **1:6 reverse split, 2003** | survived the collapse *through* a reverse split — the exact case that reads as a collapse if mishandled |
| any 2b.1/2b.2 name that reverse-split before delisting | — | enumerate from the data |

**Priceline is the most valuable entry in this table.** It is a survivor whose
unadjusted chart looks like a catastrophe and whose adjusted chart does not.

### 2b.6 Ticker reuse — enumerate from data, but start from these

Class 8 says "enumerate from the data, do not assume", and that stands. These are
seed candidates to confirm, not a closed list:

| ticker | first holder | later holder | note |
|---|---|---|---|
| `BBBY` | Bed Bath & Beyond | reissued after delisting | **the proven case** — `full-01` failed on it |
| `AOL` | America Online (to 2001) | AOL Inc. (2009–2015) | two legally distinct issuers, same ticker, ~8-year gap |
| `T` | AT&T Corp | AT&T Inc. (formerly SBC, from 2005) | ticker *transferred* between issuers — tests whether transfer is distinguished from continuity |
| `GM` | General Motors Corp (→ Motors Liquidation 2009) | General Motors Company (IPO 2010) | bankruptcy, new entity, same ticker, ~1 year apart — **inside `TICKER_REUSE_TOLERANCE_DAYS`-adjacent territory** |
| `WM` | Washington Mutual (to 2008) | Waste Management | reuse across an unrelated sector |
| `ATHM` | Excite@Home (to 2001) | Autohome (from 2013) | dot-com failure, ticker reused by a foreign issuer |

**The `GM` case is the strictest.** A ~1-year gap between two issuers of the same
ticker is short enough that a naive splice looks plausible, which is exactly what
`TICKER_REUSE_TOLERANCE_DAYS = 92` and the `symbol_aliases` exclusion constraint
exist to make impossible.

### 2b.7 Financial-crisis controls, retained

Kept from class 7 and named so the 2008–2009 termination bulge can be checked as
well as the 2000–2002 one: Lehman Brothers (`LEH`), Washington Mutual (`WM` →
`WAMUQ`), Bear Stearns (`BSC`, acquired), Merrill Lynch (`MER`, acquired),
Wachovia (`WB`, acquired), Countrywide (`CFC`, acquired), IndyMac (`IMB`, failed),
Circuit City (`CC`, liquidated 2009), Fannie Mae / Freddie Mac (`FNM` / `FRE`,
NYSE delisting 2010), General Motors (`GM` → `GMGMQ`).

### 2b.8 Spinoffs, 1999–2002

Adjustment methodology under a spinoff is vendor-specific and frequently wrong.
Controls: Agilent from HP (1999), Delphi from GM (1999), Avaya from Lucent
(2000), Palm from 3Com (2000), Agere from Lucent (2001), Zimmer from
Bristol-Myers Squibb (2001), Edwards Lifesciences from Baxter (2000).

**Whatever the vendor does with a spinoff, it must be documented and reproducible
— not correct by our definition, but *declared*.**

## 2c. The probe fixture — 30 named securities

The classes above are the *shape*; §2b is the *density*; **this is the actual
fixture the probe runs against.** Thirty securities, sized so every control can be
checked by hand if necessary.

**Every ticker, name and date below is UNVERIFIED and must reach
`MANUAL_VERIFIED` state against EDGAR before the probe relies on it**
(`EDGAR_DELISTING_DENOMINATOR.md` §4). A candidate that cannot be confirmed is
**replaced, not guessed at** — and replacement happens *before* vendor data is
seen, never after.

| # | ticker | security | class | what its failure would prove |
|---|---|---|---|---|
| 1 | `AAPL` | Apple | survivor + large splits | baseline; 4 splits incl. 7:1 (2014), 4:1 (2020) |
| 2 | `MSFT` | Microsoft | survivor | splits 1998, 1999, 2003 |
| 3 | `CSCO` | Cisco | survivor through collapse | splits 1998–2000; −85% drawdown that is *not* a delisting |
| 4 | `AMZN` | Amazon | survivor through collapse | 1997 IPO, −90% drawdown, 20:1 split 2022 |
| 5 | `SPY` | SPDR S&P 500 ETF | long-lived ETF | non-equity control; dividends, no splits |
| 6 | `QQQ` | Nasdaq-100 ETF | ETF inside the window | Mar 1999 inception, 2:1 split Mar 2000 — an ETF *born* in the bubble |
| 7 | `IPET` | Pets.com | **short-lived failure** | Feb 2000 IPO → wound up Nov 2000. **~9 months. The single sharpest test in the fixture** |
| 8 | `ETYS` | eToys | short-lived failure | May 1999 IPO → Ch. 11 Mar 2001 |
| 9 | `WBVN` | Webvan | short-lived failure | Nov 1999 IPO → Ch. 11 Jul 2001 |
| 10 | `TGLO` | theglobe.com | collapse, survived as shell | Nov 1998 IPO; distinguishes `bankrupt` from shell survival |
| 11 | `KOOP` | drkoop.com | small failure | going-concern → absorption; a name no vendor markets |
| 12 | `MPPP` | MP3.com | small acquisition | acquired by Vivendi 2001 — acquisition of a *failing* company |
| 13 | `ENE` | Enron | large-cap collapse | Ch. 11 Dec 2001 |
| 14 | `WCOM` | WorldCom | large-cap collapse → reorg | Ch. 11 Jul 2002, emerges as MCI — new identity |
| 15 | `EXDS` | Exodus Communications | infrastructure failure | Ch. 11 Sep 2001 |
| 16 | `PSIX` | PSINet | infrastructure failure | Ch. 11 May 2001 |
| 17 | `GCTY` | GeoCities | peak acquisition | Yahoo 1999 — **series must end, not continue into YHOO** |
| 18 | `BCST` | Broadcast.com | peak acquisition | Yahoo 1999 — same |
| 19 | `CPQ` | Compaq | merger, identity change | → HPQ 2002 |
| 20 | `BEL`→`VZ` | Bell Atlantic → Verizon | rename with continuity | 2000; ticker change, **one** economic security |
| 21 | `BBBY` | Bed Bath & Beyond | **ticker reuse — proven** | the exact case `full-01` failed on. Non-negotiable |
| 22 | `GM` | GM Corp → Motors Liquidation; GM Company | **ticker reuse, ~1yr gap** | 2009 bankruptcy, 2010 IPO reuses `GM`. The strictest reuse case — short enough that a splice looks plausible |
| 23 | `AOL` | America Online; AOL Inc. | **ticker reuse, ~8yr gap** | two legally distinct issuers, same ticker |
| 24 | `JDSU` | JDS Uniphase | reverse split | 1:8 reverse split 2006 — the largest factor in the fixture |
| 25 | `PCLN` | Priceline | **reverse split, survivor** | 1:6 in 2003. Unadjusted chart looks like a catastrophe; adjusted does not. **The most valuable single control** |
| 26 | `QCOM` | Qualcomm | large forward split | 4:1 Dec 1999, at the peak |
| 27 | `LEH` | Lehman Brothers | financial-crisis failure | Sep 2008 |
| 28 | `CC` | Circuit City | financial-crisis liquidation | 2009; non-financial crisis failure |
| 29 | `FRC` | First Republic Bank | **recent delisting** | May 2023 — proves the recent end is maintained, not just the archive |
| 30 | `RDDT` | Reddit | **recent IPO** | Mar 2024 — the young end of the universe |

### Composition, deliberately

| stress | count | entries |
|---|---|---|
| survivors / long-lived | 6 | 1–6 |
| **short-lived failures** (< 3 years listed) | 4 | 7, 8, 9, 11 |
| other dot-com failures | 5 | 10, 12, 15, 16, 13 |
| large-cap collapse | 2 | 13, 14 |
| acquisitions ending a series | 3 | 12, 17, 18 |
| identity-changing mergers | 2 | 19, 20 |
| **ticker reuse** | 3 | 21, 22, 23 |
| reverse splits | 2 | 24, 25 |
| large forward splits | 4 | 1, 6, 26, 4 |
| financial-crisis failures | 2 | 27, 28 |
| recent delisting / recent IPO | 2 | 29, 30 |

*(Entries appear under more than one stress; that is intentional — a control that
tests one thing tests it in isolation, and a control that tests three at once is
the more realistic case.)*

### The six failure modes these were chosen to stress

Each is a failure TradeIt has **already encountered or already guarded against**,
not a hypothetical:

| failure mode | controls | precedent |
|---|---|---|
| **ticker reuse** | 21, 22, 23 | `full-01`'s BBBY survivorship control passed on another company's prices |
| **structural breaks** | 14, 20, 22, 24, 25 | the BBBY 536-session gap that ends an analytical episode |
| **missing histories** | 7–12, 15, 16 | the survivorship FAIL that produced this whole programme |
| **adjustment errors** | 1, 6, 24, 25, 26 | scale-invariance work exists because adjusted-only series are lossy |
| **delisted securities absent from vendor** | 7–18, 27, 28 | the reason a denominator is being built at all |
| **impossible OHLC bars** | all 30 | the existing quarantine machinery, exercised on real data |

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
