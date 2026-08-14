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

## Q. The pre-sales questions — send verbatim, before paying anything

Twenty-six questions. **Free to ask, and they resolve most of the schema and the
whole purchase decision.** Marketing copy is not an answer to any of them; if a
reply restates a product page rather than answering, the item stays `UNVERIFIED`.

Record each answer as `VERIFIED` (stated by the vendor in writing, specific),
`CORROBORATED` (implied by documentation but not directly answered) or
`UNVERIFIED` (unanswered, evasive, or marketing language).

### Product access

> **1.** Does your inexpensive EOD subscription provide access to historical
> **daily** data for **delisted** US stocks, or only for currently active
> securities?
>
> **2.** If delisted history is available, how is it accessed — bulk download,
> API, individual-symbol download, or a separate product or package?
>
> **3.** Is the complete active **and** delisted US equity universe downloadable
> during a standard monthly subscription?
>
> **4.** Are there throughput, bandwidth or daily download limits that would make
> acquiring a full historical archive impossible within one billing month?
>
> **5.** What is the minimum subscription or product combination necessary to
> obtain that archive, and what does it cost in total?

### Historical depth

> **6.** Does your US stock coverage reliably include data from **1998-01-01**
> onward?
>
> **7.** Are **small and short-lived** securities from the 1998–2002 period
> represented — companies that listed and failed within a few years — or is the
> historical universe primarily major surviving names?
>
> **8.** Are bankrupt and delisted securities retained in your historical
> archive indefinitely, or removed once they stop trading?

### Identity and lifecycle

> **9.** Do you provide any permanent identifier for a security that is
> independent of its ticker — for example CIK, CUSIP, FIGI, or a vendor-internal
> permanent ID?
>
> **10.** How are **ticker changes** represented? Does one file span the change,
> or are there two files, or is history available only under the current ticker?
>
> **11.** How is **ticker reuse** handled — when a later, unrelated company
> receives a ticker previously used by a delisted company? Are the two histories
> separated?
>
> **12.** Are listing and delisting **dates** supplied per security?
>
> **13.** Are delisting **reasons** supplied — bankruptcy, acquisition, going
> private, exchange rule?
>
> **14.** Are mergers, acquisitions and security replacements represented in any
> form?

### Corporate actions and price semantics

> **15.** Are raw / **unadjusted** OHLCV bars available?
>
> **16.** Are **split-adjusted** series available?
>
> **17.** Are **dividend-adjusted / fully adjusted** series available?
>
> **18.** Are **split events** available separately, as a corporate-action file
> or feed?
>
> **19.** Are **dividends** available separately?
>
> **20.** How are **reverse splits** represented?
>
> **21.** Are **spin-offs** handled, and if so how are they reflected in the
> adjusted series?
>
> **22.** Are historical **volumes** adjusted under any of the adjusted products,
> or do volumes remain as printed?

### Licensing

Questions 23–26 are held in `DATA_RETENTION_RIGHTS.md` §3.2 alongside the
equivalent Twelve Data and FMP questions, so all retention determinations live in
one register. In summary they ask for written confirmation that data already
delivered may be retained indefinitely after cancellation; that the right extends
to raw files, normalised database rows, **derived bars**, security-master
mappings and **frozen internal research corpora**; that continued private
internal analysis after cancellation is permitted; and whether a future
commercial version of TradeIt would require a different licence.

**24(c) and 24(e) are the decisive ones.** Sharadar's licence permits keeping
nothing *and* requires deleting derived datasets — a licence that permits keeping
the files while staying silent on derived data would leave `research-01` itself
in an undetermined state, which is treated as prohibited.

### Answer register

| Q | topic | answer | grade | date |
|---|---|---|---|---|
| 1–5 | product access | — | `UNVERIFIED` | — |
| 6–8 | historical depth | — | `UNVERIFIED` | — |
| 9–14 | identity & lifecycle | — | `UNVERIFIED` | — |
| 15–22 | corporate actions | — | `UNVERIFIED` | — |
| 23–26 | licensing | licence text permits permanent retention (**USER-VERIFIED**); **scope unconfirmed** | `CORROBORATED` | — |

**Do not infer any of these from marketing language.** Every row above is
`UNVERIFIED` until a written reply fills it in.

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

**The fixture is the 30 named securities in
[`DOTCOM_CONTROL_UNIVERSE.md`](DOTCOM_CONTROL_UNIVERSE.md) §2c** — sized so every
control can be checked by hand, and composed so that six specific failure modes
TradeIt has already encountered are each stressed by at least two entries.

**Every control must reach `MANUAL_VERIFIED` identity state against EDGAR before
the probe runs.** A candidate that cannot be confirmed is replaced *before* vendor
data is seen, never after.

Each is fetched by ticker-and-era and checked for:

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

| # | comparison | measured as | acceptance |
|---|---|---|---|
| F1 | **session coverage** | count of sessions per instrument-year, per vendor | within 1% of the market calendar's session count |
| F2 | **exact missing sessions** | the *set difference* against a US market calendar, listed by date | reported per vendor; a vendor missing sessions the others have is a coverage defect, not noise |
| F3 | **raw OHLC disagreement** | median and 95th-percentile absolute relative difference, per field | median < 0.1% on close; every session > 1% investigated individually |
| F4 | **adjusted OHLC disagreement** | same, after aligning adjustment bases | systematic *drift* indicates a split disagreement, not noise — trace it to a date |
| F5 | **volume disagreement** | median absolute relative difference | expect materially worse than price: consolidated tape vs primary-exchange prints differ legitimately. **Record it; do not "fix" it** |
| F6 | **split schedule** | ratio and ex-date per event, per vendor | identical, or a recorded disagreement |
| F7 | **dividend schedule** | cash amount and ex-date per event | identical, or a recorded disagreement |
| F8 | **impossible OHLC rows** | `high < low`; `open`/`close` outside `[low, high]`; non-positive prices | **zero tolerated** — every instance quarantined and counted |
| F9 | **stale repeats** | runs of ≥ 3 consecutive sessions with identical OHLC **and** identical volume | flagged; legitimate in a halted or near-dead security, suspicious in a liquid one — the distinction is the instrument, not the pattern |
| F10 | **price spikes** | single-session moves > 50% with no corporate action and no corroboration from another vendor | flagged for individual review |
| F11 | **listing / delisting boundaries** | first and last session per instrument, per vendor | boundaries agree within a few sessions, or the disagreement is attributed |

### Discrepancy classification — the rule that keeps this honest

> **Two vendors disagreeing does not make a third vendor correct, and a majority
> is not evidence.** Three vendors sharing one upstream tape agree for reasons
> that have nothing to do with truth.

Every discrepancy is classified and **stored**, never silently resolved:

| class | meaning | resolution |
|---|---|---|
| `ADJUSTMENT_BASIS` | the two series are on different bases | not a discrepancy — a labelling error on our side |
| `CORPORATE_ACTION` | traceable to a split/dividend one vendor applied and the other did not | record both; the action itself becomes the disputed fact |
| `VENUE_SCOPE` | consolidated vs primary-exchange (typically volume, sometimes the close) | expected; recorded as a known systematic difference |
| `SESSION_COVERAGE` | one vendor has a session the other lacks | a coverage fact about the vendor, per instrument-year |
| `IMPOSSIBLE` | violates OHLC arithmetic on one side | that side is quarantined; the other is *not* thereby blessed |
| `UNEXPLAINED` | none of the above | **stays `UNEXPLAINED`**, counted and reported. This bucket's size is itself a quality metric |

**Disagreement is stored as two rows, not resolved.** That is already the
`price_facts` contract (`RESEARCH_01_DATA_CONTRACT.md` §4): two sources, two
rows, each with `source` and `source_version`. Resolution, if it ever happens,
belongs to the versioned normalisation layer where it is reversible.

**F8–F10 feed the existing quarantine machinery rather than a new one.**
Quarantined, counted, reported — never silently dropped, never repaired.

---

## G. Survivorship completeness — the item most likely to fail

> **Kibot including delisted companies does NOT automatically prove the universe
> is survivorship-bias-free.**

A delisted roster proves delisted securities *exist* in the product. It says
nothing about what fraction of them exist. The failure mode that matters is
systematic and invisible: a roster built from well-known, well-traded, long-lived
names that happens to omit the thin, short-lived, small-cap failures — which are
exactly the population survivorship bias is *made of*.

### G1. The independent denominator

Fully designed in **[`EDGAR_DELISTING_DENOMINATOR.md`](EDGAR_DELISTING_DENOMINATOR.md)**
— free, public domain, no vendor cooperation, buildable today, and **built before
purchase** because it is the instrument that measures the vendor.

Two things from that design matter here:

1. **It reports two coverage bounds, never one.** `matched_coverage` (over
   identity-`RESOLVED` denominator entries) is the optimistic bound;
   `bounded_coverage` (over `RESOLVED + AMBIGUOUS + UNRESOLVED`) is the
   pessimistic one. Reporting only the first is the standard way this measurement
   is made to look better than it is.
2. **Form 25 is weakest exactly in the dot-com window** — electronic Form 25
   filing largely postdates the 2005 Rule 12d2-2 amendments, so 1998–2002 leans on
   Form 15 and on *filing cessation*, which is weaker but uniform across the whole
   span. A denominator built on Form 25 alone would report that almost nothing
   delisted in 2001.

### G2. Cohort survival — the measurement most robust to identity failure

For each listing cohort (securities first appearing in year *Y*), the fraction
still present 3, 5 and 10 years later — computed **twice**, once over the EDGAR
denominator and once over the vendor's roster, then compared.

**Expected in an honest corpus:** the 1999–2000 cohorts show markedly worse
survival than the 2015–2016 cohorts, and the vendor's curve tracks EDGAR's shape.
**A vendor curve that is systematically flatter than EDGAR's — cohorts surviving
better than reality — is survivorship bias, quantified**, with no need to
enumerate a single missing name.

This is the strongest single measurement available, because it compares *shapes*
rather than *memberships*: a 30% unresolved identity rate degrades it far less
than it degrades `matched_coverage`.

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

### G5. The metrics this item must produce

Numbers, each with the query that produced it. No adjectives.

| metric | definition |
|---|---|
| expected delisted controls | 30, from `DOTCOM_CONTROL_UNIVERSE.md` §2c |
| covered | controls fully reconstructed per §3 of that document |
| missing | controls absent, with the recorded reason |
| unresolved identity mapping | controls or denominator entries stuck at `AMBIGUOUS`/`UNRESOLVED` |
| `matched_coverage` | vendor ∩ `RESOLVED` denominator ÷ `RESOLVED` denominator |
| `bounded_coverage` | same numerator ÷ (`RESOLVED` + `AMBIGUOUS` + `UNRESOLVED`) |
| coverage by calendar period | both bounds, per termination year 1994–2026 |
| coverage by exchange | where a filing names one (NYSE / AMEX / Nasdaq tiers) |
| coverage by security lifespan | buckets < 1y, 1–3y, 3–10y, > 10y |
| **coverage of short-lived securities** | the < 1y and 1–3y buckets specifically |
| **coverage of 1998–2002 failures** | terminations in that window, both bounds |
| cohort survival ratios | vendor curve vs EDGAR curve, 3/5/10-year, per listing cohort |

### G6. The specific hypothesis to test, not merely to report

> **Are failed and short-lived dot-com companies disproportionately absent?**

This is a *comparison*, not a count. Coverage of the < 1y and 1–3y lifespan
buckets is compared against coverage of the > 10y bucket. If long-lived
securities are 85% covered and sub-3-year securities are 30% covered, the corpus
is biased toward survivors **even though its headline delisted count looks
healthy** — and that is the outcome most likely to be mistaken for success.

**Do not call `research-01` survivorship-safe because the famous bankruptcies are
present.** Enron and WorldCom are in every vendor's archive. Pets.com is the test.

### G7. Acceptance criteria — predefined, before any vendor data is examined

`research-01` is assigned one of four classifications *from the measurement*. It
is not chosen, argued for, or negotiated after the fact.

| class | `bounded_coverage` | controls (of 30) | cohort survival vs EDGAR | short-lived bucket |
|---|---|---|---|---|
| **`SURVIVORSHIP_SAFE_RESEARCH_GRADE`** | **threshold deliberately unset — see below** | 30 (necessary, **not sufficient**) | tracks EDGAR's shape | to be set with the threshold |
| **`MATERIALLY_SURVIVORSHIP_CORRECTED`** | ≥ 0.45 | ≥ 26 | differential present | ≥ 0.40 |
| **`PARTIALLY_SURVIVORSHIP_CORRECTED`** | 0.25 – 0.45 | ≥ 20 | weak or partial | ≥ 0.20 |
| **`SURVIVOR_BIASED`** | < 0.25 | < 20 | absent — cohorts survive like survivors | < 0.20 |

### The top grade is not yet reachable, on purpose

**No threshold is assigned for `SURVIVORSHIP_SAFE_RESEARCH_GRADE`, and none may
be assigned until the denominator has been built and its distribution examined.**
Picking a number first would be choosing the answer before the measurement.

`RESEARCH_GRADE_THRESHOLD` is `None` in `tradeit.edgar.denominator`, and
`classify_corpus()` returns `MATERIALLY_SURVIVORSHIP_CORRECTED` with an explicit
reason rather than the top grade, however good the inputs look. A test asserts
this. Raising the grade requires deliberately passing a threshold, which is a
decision with a name on it rather than a default that drifted.

**Passing all 30 controls is necessary for the top grade and explicitly not
sufficient.** Thirty securities are a stress test against known failure modes,
not evidence about the other fifteen thousand. The recommendation for a
quantitative research-grade threshold comes **after** the denominator exists and
**before** any Kibot result is used for economic testing.

Additional conditions, applying to every class above `survivor-biased`:

1. every control is either reconstructed **or** FAILs with a recorded,
   non-`unknown` reason — a silent omission fails the whole assessment;
2. terminations by year show visible 2000–2002 and 2008–2009 bulges (B6);
3. exchange coverage includes AMEX and Nasdaq SmallCap-tier names (G4), **or**
   their absence is declared as a scope limit in `CORPUS_REGISTRY.md`;
4. both coverage bounds are published together, always.

**A classification below `survivorship-safe` is not a failure and does not
necessarily reject Kibot.** It changes what the corpus may be used to conclude —
the prohibited-conclusion table in `RESEARCH_01_DATA_CONTRACT.md` §10 — and it
must be published in `CORPUS_REGISTRY.md` beside the corpus, permanently.

**Nothing is called survivorship-safe until the control universe passes.**

---

---

## H. Intraday — a separate, later, non-blocking probe

**This item does not gate items A–G and must not delay them.** The EOD corpus
serves the Swing and Retirement mandates and remains the priority; the intraday
question exists because a *second* corpus is now specified
(`MULTI_TIMEFRAME_MANDATES.md` §6.3) and it would be wasteful to ask the same
vendor twice.

Target: **`intraday-01`** — 1-minute base, regular trading hours, ~2015–present
(2018 minimum), from which `5m/15m/30m/1h` are derived by existing machinery.
`4h` is not adopted (`MULTI_TIMEFRAME_MANDATES.md` §3.2).

| # | to establish | why it decides something |
|---|---|---|
| H1 | does historical intraday data exist at all, and at which granularities? | if the minimum granularity is 5m, the derivation chain loses its base and `1m` execution research is impossible |
| H2 | **are raw 1-minute bars available**, or only pre-aggregated products? | 1m is the canonical base; buying 5m and deriving 15m is acceptable, buying 5m and *calling* it 1m is not |
| H3 | earliest intraday history, per instrument and overall | 2015 target, 2018 floor |
| H4 | **are delisted securities included in intraday history?** | expected **no**. This is the answer that determines whether `intraday-01` is labelled survivorship-biased — see §G's philosophy applied to a corpus that will probably fail it |
| H5 | **bulk file delivery, or per-symbol API only?** | ~295,000 REST requests for 1,500 names × 10 years. **Per-symbol REST backfill is not a viable acquisition strategy at any useful universe size** — bulk delivery is a hard requirement, not a preference |
| H6 | download/API limits, and time to acquire the target universe | must complete inside a retention-safe window |
| H7 | **retention rights after cancellation** | the same absolute filter as §1. Permanent retention or the source is ineligible, whatever the data quality |
| H8 | regular-hours vs extended-hours semantics — are they separable? | if the vendor silently merges pre/post-market minutes into the session, every session-anchored bucket boundary is wrong and the data cannot be used as a base |
| H9 | timestamp and time-zone convention — UTC or local? bar stamped at **open** or **close**? | a bar stamped at its open versus its close differs by the bar width. Getting this wrong shifts every signal by one bar and is invisible in aggregate |
| H10 | corporate-action treatment intraday — adjusted, unadjusted, or both? | a split applied to a 1-minute archive retroactively rewrites millions of rows; we need the unadjusted base and our own derivation, as with EOD |
| H11 | expected storage footprint of the actual delivery format | 1,500 × 10y ≈ 1.47 B bars ≈ 22 GB columnar / 133 GB narrow Postgres. Sanity-check against what the vendor actually ships |
| H12 | liquidity/universe scoping options | universe size is the strongest cost lever: 1,500 names is 22 GB, 6,000 is 89 GB, and the Day mandate will not trade illiquid microcaps anyway |

### H's own acceptance posture

**`intraday-01` is expected to fail a survivorship test and that is acceptable,
provided it is measured and declared.** Intraday history for companies that
stopped trading in 2003 is rare at any price. The consequences are recorded in
advance:

1. the delisted fraction of `intraday-01` is **measured and published** in
   `CORPUS_REGISTRY.md`, not estimated;
2. the **Day mandate's** KPIs carry a permanent survivorship caveat that the
   Swing and Retirement mandates' do not;
3. no Day-mandate result may be compared with a Swing-mandate result as though
   they came from the same population;
4. the **forward** 1-minute archive TradeIt accumulates itself *is*
   survivorship-safe by construction, because it records what existed on each day
   it ran. The purchased window is a head start; the forward archive is the
   asset, and it is cheap — ~9 MB/day, ~2 GB/year for 1,500 names.

**Do not purchase intraday data.** Answer H1–H12 in writing first, then decide
whether an economically useful window can be backfilled at all.

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
5. **Item H (intraday) only after step 4 is settled.** It is a separate corpus, a
   separate purchase decision, and it must not delay the EOD one. Its written
   questions (H1–H12) are free and may be asked alongside step 2 to save a round
   trip — but its *answers* change nothing about the EOD decision.

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
