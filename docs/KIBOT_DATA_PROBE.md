# Kibot data probe — Phase 6 Milestone 1C

**Nothing purchased. Nothing implemented. No probe has been run.** This document
is the *design* of the probe and its acceptance rules, written before any data is
seen, so the rules cannot be adjusted to fit the result.

`kibot.com` returned `EGRESS_BLOCKED` in this session, as did every other vendor
domain and `sec.gov`. **Every Kibot capability statement below is a vendor claim
relayed via the project owner's reading of Kibot's own pages — USER-VERIFIED as
*claim*, UNTESTED as *fact*.**

---

## 0. Why this probe exists, in one paragraph

Kibot is the only candidate whose licence permits permanent retention of
downloaded data after cancellation (`PHASE_06_VENDOR_MATRIX.md` §1.3), which is
the requirement that eliminated Sharadar and EODHD. That makes it the only live
candidate for `research-01`'s price spine. It also makes it the *only* candidate,
which is precisely when confirmation bias is most dangerous. A ~$14/month product
claiming 64 years of history including delisted securities is making a claim no
competitor at any price makes in this matrix. **The probe's job is to try to
break that claim, not to confirm it.**

## 0.1 The standing rule this probe serves

> **Absence is never evidence that a security did not exist.**

Every probe item below reports one of: `obtained` / `not_offered` / `refused` /
`empty` / `mismatch`. There is no "assumed present" outcome.

---

## A. Does the ~$14 EOD tier actually expose the full historical universe?

**The question is not "does Kibot have the data" — it is "does the product we
would actually buy deliver it, in bulk, including delisted securities."**

| # | to establish | how | pass condition |
|---|---|---|---|
| A1 | what the EOD subscription includes at $14 | vendor page + written pre-sales question | delivery of **historical daily bars for the full US equity roster**, not a per-symbol metered allowance |
| A2 | delivery mechanism | vendor page | bulk download or scriptable HTTP; a manual per-symbol web form for ~15,000 securities is a practical `not_offered` |
| A3 | whether delisted securities are inside that tier or a separate paid product | **written question, before purchase** | delisted included, or its price known |
| A4 | roster files themselves obtainable | fetch the active roster, the combined roster, and the delisted-only roster | all three retrievable as files |
| A5 | per-symbol history depth on the actual tier | fetch 20 symbols spanning 1998–2026 | first session ≤ 1998-01-02 for names listed then |
| A6 | throughput and any daily cap | measure | full-universe backfill completable **inside one billing month** |

**A6 is the sleeper.** The whole funding model is "one month, download
everything, cancel, keep it". If the throughput cap makes a full backfill take
three months, the model silently becomes a three-month subscription — still fine
at $14, but it must be *known* rather than discovered on day 28.

**Ask in writing before paying** (A1/A3 especially). The retention question in
`PHASE_06_VENDOR_MATRIX.md` §6 is the template.

---

## B. Exact counts — the census

No adjectives. Numbers, with the query that produced each.

| # | count | why it matters |
|---|---|---|
| B1 | active US common stocks in the roster | baseline; compare to ~4,000–4,500 US-listed common stocks today (UNVERIFIED expectation, used as a tripwire only) |
| B2 | **delisted US common stocks in the roster** | the single most important number in this document |
| B3 | earliest reliable daily session date, per roster and overall | distinguishes "64 years for a handful of names" from "1998 for the universe" |
| B4 | securities whose history **begins before 1998-01-01** | the 1998 start is only real if the universe, not a sample, reaches back |
| B5 | securities whose history **terminates** before today | terminations are the delisting signal in a price-only feed |
| B6 | of B5, how many terminate in each year 1998–2026 | the shape matters: a real corpus shows a 2000–2002 bulge and a 2008–2009 bulge |
| B7 | of B5, how many have < 250 / < 500 sessions of total history | **short-lived listings are the hardest thing to have and the easiest to omit** |
| B8 | ETFs, ADRs, units/warrants/rights, and non-common share classes, separately | scope hygiene — B1/B2 must not be inflated by non-common instruments |
| B9 | exchange breakdown where available (NYSE / AMEX / Nasdaq NMS / Nasdaq SmallCap / OTC) | see item G |

**B2 and B7 together are the survivorship test.** A roster can pass B2 with
famous failures alone and still fail B7 completely.

### Expected magnitudes (UNVERIFIED — tripwires, not truth)

US listings peaked near ~8,000 common stocks in 1997–1998 and stand near
~4,000–4,500 now. Any corpus honestly spanning 1998–2026 should therefore contain
on the order of **12,000–16,000 distinct US common stocks**, of which **well over
half are dead**. These numbers are recollection, not measurement; item G derives
a *measured* denominator from EDGAR instead. They are recorded here only so that
a roster of, say, 6,000 names with 1,500 delisted is recognised as a red flag
rather than accepted as a full universe.

---

## C. Named historical controls

Every control from `DOTCOM_CONTROL_UNIVERSE.md`, and specifically its expanded
1998–2002 section, is fetched by ticker-and-era and checked for:

1. the series exists at all;
2. it **starts** when the security listed (not truncated to some vendor floor);
3. it **ends** at delisting, and does not continue afterwards;
4. it is **not spliced** to a later, different issuer of the same ticker;
5. splits and reverse splits inside the window are reflected in the adjusted
   series and *absent* from the unadjusted one.

Control classes probed, with the failure each is designed to expose:

| class | exposes |
|---|---|
| dot-com liquidations (Pets.com, eToys, Webvan) | delisted securities present at all |
| **short-lived listings** (IPO and liquidation both inside 1999–2001) | thin-failure omission — the hard case |
| peak acquisitions (Broadcast.com, GeoCities, Netscape) | series that must *end*, not continue into the acquirer |
| identity-changing mergers (AOL/Time Warner, Compaq→HP, Bell Atlantic→Verizon) | ticker change without history loss or history invention |
| accounting/telecom failures (Enron, WorldCom, Global Crossing, Adelphia) | large-cap disappearance |
| reverse splits before delisting | a reverse split misread as a collapse |
| financial-crisis failures (Lehman, WaMu, Bear Stearns, Circuit City, Fannie/Freddie) | the mechanism is not dot-com-specific |
| **ticker reuse** (BBBY; `T`; `AOL`; `GM`; `WM`) | the alias exclusion constraint, and splicing |
| recent delistings (2023–2026) | the recent end is maintained, not just the archive |
| large splits (AAPL, NVDA, QCOM 1999, CSCO 1998–2000) | raw-vs-adjusted semantics under extreme factors |

**A control that cannot be retrieved is a FAIL with a recorded reason**, never a
silent omission. Candidate names are UNVERIFIED and are confirmed against EDGAR
first — see `DOTCOM_CONTROL_UNIVERSE.md`.

---

## D. Identity semantics — the question that decides the schema

**Working expectation, stated so the probe can refute it: Kibot is a
ticker-keyed file service with no permanent security identifier.** It is priced
and described like one. If that is right, then Kibot supplies *prices*, and
**TradeIt must build and own the permanent security master itself** — which the
`research-01` contract already assumes (`securities` + `symbol_aliases` +
`security_relationships`).

| # | to establish | pass condition |
|---|---|---|
| D1 | is there any permanent per-security id, stable across ticker changes? | if yes, record it as `vendor_permanent_id`; if no, record `not_offered` and proceed |
| D2 | how is a **ticker change** represented? one file that spans the change, two files, or history only under the current ticker? | must be *determinable*; the dangerous case is history silently re-labelled under the new ticker with no record of the old |
| D3 | how is a **reused ticker** represented? one concatenated file, or two? | **a single concatenated file is a splice and is disqualifying at face value** unless a date-bounded roster lets us cut it |
| D4 | does one economic security receive exactly one history? | tested via D2/D3 controls, not asked |
| D5 | are CIK, CUSIP or FIGI supplied in any roster field? | any of them shortens the EDGAR bridge enormously |
| D6 | do the rosters carry listing/delisting **dates** per ticker? | date-bounded rosters make D3 recoverable even without a permanent id |

### If D1 = none and D5 = none — the realistic case

Then the identity bridge is TradeIt's to build, and it is the hardest remaining
engineering problem in Phase 6:

- **Post-2009:** SEC `company_tickers.json` gives a current CIK↔ticker map;
  combined with EDGAR filing history this resolves most live names.
- **1998–2008:** EDGAR is CIK-centric and old filings do not carry authoritative
  tickers. The bridge is best-effort: name matching against full-index company
  names, Form 8-A/25 exchange filings, and manual curation for the control
  universe.
- **Where it cannot be resolved, `cik` is `NULL` and stays `NULL`.** An unmapped
  security is a security with prices and no filings — recorded honestly, surfaced
  to the operator queue, never guessed.

**This is not a reason to reject Kibot.** No affordable vendor solves 1998
identity for us. It is a reason to size the work correctly and to refuse to
pretend the mapping is better than it is.

---

## E. Corporate actions and adjustment methodology

Kibot claims unadjusted, split-adjusted and fully-adjusted equity data. The claim
is useful only if the relationship between the three is verifiable.

| # | to establish | method | pass condition |
|---|---|---|---|
| E1 | are splits recoverable from the data? | ratio of unadjusted to split-adjusted close, per session, per control | ratio is piecewise-constant and steps exactly on known ex-dates |
| E2 | do the implied ratios match known splits? | compare to EDGAR/8-K-confirmed splits for controls | exact match on ratio and date |
| E3 | reverse splits handled the same way | class-10 controls | ratio steps < 1 correctly |
| E4 | is there an explicit corporate-action **file**, or only implied actions? | roster/product inspection | explicit file preferred; implied-only is workable but must be declared |
| E5 | dividends: present as cash amounts, or only embedded in "fully adjusted"? | fully-adjusted vs split-adjusted difference | cash dividends recoverable, or `not_offered` recorded |
| E6 | spinoffs: how treated in the fully-adjusted series? | class-controls (Agilent/HP 1999, Avaya/Lucent 2000, Palm/3Com 2000) | documented behaviour, whatever it is |
| E7 | is the adjustment methodology **documented**? | vendor docs | if undocumented, we ingest **unadjusted only** and derive everything |

**Standing rule, unchanged: prefer `raw_unadjusted` and derive the rest.** The
existing scale-invariance work exists because adjusted-only series are lossy.
Adjusted series arriving from the vendor are stored as vendor facts with
`adjustment_basis` set to what the vendor *said*, never to what we infer.

E1–E3 double as a **data-integrity test**: they are the cheapest way to detect a
vendor whose "split-adjusted" series was built by a different process than its
"unadjusted" one.

---

## F. Cross-vendor comparison — 10–20 overlapping securities

Kibot's data is checked against **Twelve Data** and **FMP** (for OHLCV
comparison only — this is not a fundamentals use, so the standing FMP exclusion
is not engaged; if that reading is wrong, drop FMP and compare against Twelve
Data alone, which is sufficient).

Sample: 10–20 securities all three cover — large caps with long histories, at
least two with major splits, at least two mid-caps, at least one recent IPO.
Delisted names cannot be compared this way and are handled by items C and G.

| # | comparison | acceptance |
|---|---|---|
| F1 | **raw OHLC agreement** | median absolute relative difference < 0.1% on close; investigate any session > 1% |
| F2 | **split-adjusted agreement** | same, after aligning adjustment bases; systematic drift indicates a split disagreement, not noise |
| F3 | **volume agreement** | expect worse than price — consolidated vs primary-exchange tape differ legitimately. Record the discrepancy; do not "fix" it |
| F4 | **split events** | identical ratios and ex-dates, or a recorded disagreement |
| F5 | **missing sessions** | compare session sets against a US market calendar. Report gaps per vendor. A vendor missing sessions the others have is a coverage defect |
| F6 | **impossible bars** | `high < low`, `close` outside `[low, high]`, non-positive prices, zero-volume sessions with a price range, > 50% single-session moves without a corresponding action |

**F6 feeds the existing quarantine machinery rather than a new one.** Impossible
bars are quarantined, counted, and reported — never silently dropped, never
repaired.

**Disagreement is recorded as two rows, not resolved.** That is already the
`price_facts` contract (`RESEARCH_01_DATA_CONTRACT.md` §4): two sources, two
rows, both with `source` and `source_version`.

---

## G. Survivorship completeness — the item most likely to fail

> **Kibot including delisted companies does NOT automatically prove the universe
> is survivorship-bias-free.**

A delisted roster proves delisted securities *exist* in the product. It says
nothing about what fraction of them exist. The failure mode that matters is
systematic and invisible: a roster built from well-known, well-traded, long-lived
names that happens to omit the thin, short-lived, small-cap failures — which are
exactly the population survivorship bias is *made of*.

### G1. Build an independent denominator from EDGAR — free, authoritative

This is the core of the item, and it needs no purchase and no vendor cooperation:

- **Form 25 / 25-NSE** filings enumerate exchange delistings.
- **Form 15** filings enumerate deregistrations.
- **Form 8-A** filings enumerate registrations of a class of securities.
- All are in EDGAR's quarterly full-index from **1994 Q3**, with filing dates,
  free, in bulk.

Counting them per year yields an **independent, primary-source estimate of how
many US securities stopped being listed in each year 1998–2026.** That is the
denominator Kibot's B2/B5/B6 counts are measured against. Caveats recorded up
front: Form 25 pre-dates 2005 in a different regime (Rule 12d2-2 was amended in
2005), not every delisting produces a Form 25, and form counts include non-common
securities. It is an estimate with known bias, which is infinitely better than no
denominator at all.

### G2. Cohort survival

For each listing cohort (securities whose history begins in year *Y*), compute
the fraction still trading 3, 5 and 10 years later. Compare the 1999–2000 cohorts
against the 2015–2016 cohorts.

**Expected in an honest corpus:** the 1999–2000 cohorts should show markedly worse
survival. **If every cohort survives at a similar high rate, the corpus is
survivorship-biased**, and no amount of famous-bankruptcy controls disproves it.
This test needs no external data at all — it is internal consistency, and it is
the single most informative number the probe can produce.

### G3. Thin-failure representation

Item B7's counts, read as a distribution. A corpus that contains WorldCom and
Enron but almost nothing with under 500 sessions terminating in 2000–2002 has
kept the headlines and lost the population.

### G4. Exchange completeness

Nasdaq SmallCap and AMEX listings of the era are where the small failures lived.
If item B9 shows coverage concentrated in NYSE and Nasdaq NMS, the corpus is
biased toward larger names **even if its delisted count looks healthy**.

**OTC / pink-sheet coverage is explicitly out of scope** for `research-01` — but
it must be *declared* out of scope in the corpus registry, not silently absent.
A security that delisted from an exchange and continued trading OTC has, for our
purposes, ended; that is a recorded modelling decision, not a data gap.

### G5. Acceptance rule — fixed now, before any data is seen

`research-01` may be called **survivorship-safe** only if **all** hold:

1. every control in `DOTCOM_CONTROL_UNIVERSE.md` is either reconstructed or
   FAILs with a recorded, non-`unknown` reason;
2. delisted securities are **> 45%** of the total roster for 1998–2026;
3. the 1999–2000 listing cohorts show materially worse 5-year survival than the
   2015–2016 cohorts (G2);
4. terminations by year show visible 2000–2002 and 2008–2009 bulges (B6);
5. Kibot's per-year termination counts are **within a stated factor of** the
   EDGAR Form 25/15 denominator (G1), with the gap *quantified and explained*
   rather than waved past;
6. securities with < 500 sessions terminating in 1999–2002 are present in
   non-trivial numbers (G3);
7. exchange coverage includes AMEX and Nasdaq SmallCap-tier names (G4), or their
   absence is declared as a scope limit in `CORPUS_REGISTRY.md`.

**Any of these failing means the corpus is labelled with its measured limitation
and may not be cited for cross-sectional or economic claims** — the same
treatment `full-01` already receives. It does not necessarily mean Kibot is
useless; it means the honest label is "US large- and mid-cap listed equities,
1998–, partially survivorship-corrected" rather than "the US equity universe".

**Nothing is called survivorship-safe until the control universe passes.**

---

## 8. How to run this without buying anything first

In order, cheapest first:

1. **Free, now, no vendor contact:** build the EDGAR Form 25/15/8-A denominator
   (G1) and confirm every control name and date against EDGAR (item C). This is
   Milestone 4 of the improvement plan, it is useful under every outcome, and it
   is the instrument that measures the vendor.
2. **Free, written pre-sales questions:** items A1, A3, D1, D5, D6, E4, E7. These
   are answerable in prose by the vendor and decide most of the schema. Keep the
   replies.
3. **Trial or single month, if and only if steps 1–2 are satisfactory:** the
   remaining items. Run the probe **before** the bulk download, on a sample —
   items B, C, F and G2 need only the rosters plus a few hundred symbol files.
4. **Bulk download only after the probe passes**, inside the same billing month
   (A6), with the retention licence text saved alongside the data.

**Decision gate: the probe result comes back for approval before any bulk
download. A failed item G is not overridden by a passed item A.**

---

## 9. What this probe explicitly does not do

- It does not compute returns, expectancy, profitability, or any
  outcome-conditioned statistic. Coverage and integrity only.
- It does not modify `full-01`, which remains frozen.
- It does not implement any part of Phase 6.
- It does not treat any vendor claim as fact. Every row above is `UNTESTED` until
  a measurement replaces it.
