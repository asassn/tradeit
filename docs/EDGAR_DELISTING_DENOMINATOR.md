# The EDGAR delisting denominator

**Free. Public domain. No vendor cooperation. No purchase. Buildable today.**

The instrument that answers the only question that matters about a price
vendor's delisted roster:

> Of the securities that historically disappeared, **how many does the candidate
> vendor actually contain?**

Without a denominator, "the vendor has *N* delisted names" is a number with
nothing to compare it against. This document designs the denominator.

**Status: design only. Not built.** `sec.gov` returns `EGRESS_BLOCKED` in this
session, so every claim below about which forms exist, when, and in what
electronic form is **CORROBORATED at best and must be confirmed against EDGAR as
the first act of building it.** The confidence column is not decoration.

---

## 0. The correction that shapes everything

**Official public EDGAR index availability begins 1994 Q3** — not 1993. Corrected
throughout the documentation set. It does not affect the 1998-01-01 start: four
years of margin remain.

---

## 1. What this is *not*

It is **not** a famous-bankruptcy control list. That already exists
(`DOTCOM_CONTROL_UNIVERSE.md`) and does a different job: it proves a vendor can
retrieve *specific named* securities.

A vendor can pass every named control and still be badly survivor-biased, because
the controls are famous and famous names are the ones every vendor has. The
denominator is a **population estimate** — how many securities disappeared, by
year, by cohort, by exchange where determinable — against which the vendor's
roster is a *fraction*, not a list.

Controls detect specific failures. The denominator detects **systematic
thinness**. Both are needed and neither substitutes for the other.

---

## 2. Signal sources, and their real availability

| signal | form / mechanism | what it evidences | electronic in EDGAR from | confidence |
|---|---|---|---|---|
| **exchange delisting** | Form 25, Form 25-NSE | a class of securities removed from listing | **~2005** — see §2.1 | CORROBORATED |
| **deregistration** | Form 15, 15-12B, 15-12G, 15-15D | registration terminated / reporting duty suspended | 1994 Q3+ | CORROBORATED |
| **listing** | Form 8-A12B (§12(b), exchange), 8-A12G | a class registered for listing — the *birth* event | 1994 Q3+ | CORROBORATED |
| **bankruptcy** | 8-K Item 1.03 (post-Aug 2004); pre-2004 8-K **Item 3** | Chapter 7/11 | item numbering changed 2004 — see §2.2 | CORROBORATED |
| **merger / acquisition** | S-4, 8-K Item 2.01 (post-2004) / **Item 2** (pre-2004), DEFM14A | completion of acquisition or disposition | as above | CORROBORATED |
| **delisting notice** | 8-K Item 3.01 (post-2004) | notice of failure to satisfy a listing rule | 2004+ | CORROBORATED |
| **shell / reverse merger** | 8-K Item 5.06 | shell company transaction — legal continuity, economic discontinuity | 2004+ | CORROBORATED |
| **name change history** | `submissions/CIK##########.json` → `formerNames` | prior registrant names with date ranges | XBRL-era API | CORROBORATED |
| **current ticker map** | `company_tickers.json` | CIK ↔ ticker, **currently listed only** | present-day snapshot | CORROBORATED |
| **filing cessation** | absence of periodic filings after a date | the issuer stopped reporting — **see §2.3** | 1994 Q3+ | derived, not a form |

### 2.1 Form 25 is the obvious signal and it is weakest exactly where we need it

Form 25 is the natural delisting record. But the Rule 12d2-2 regime was amended
in **2005**, and before that Form 25 was filed by the *exchange*, largely on
paper, and is **not reliably present in EDGAR for the 1998–2002 window.**

That is the single most important caveat in this document, because 1998–2002 is
the window `research-01` exists to cover. **A denominator built on Form 25 alone
would be near-empty for the dot-com collapse and would then "prove" that almost
nothing delisted in 2001** — a conclusion so wrong it would invalidate the whole
measurement.

### 2.1b Nasdaq issuers of the era would not file a Form 25 at all

Worse than §2.1, and found while verifying a control rather than by reasoning
about the rule. Pets.com's filing set (CIK 1100683) contains **`8-A12G`**, not
`8-A12B` — its class was registered under section **12(g)**.

That is the expected shape for the period: **Nasdaq was not a registered national
securities exchange until 2006.** Before then it was an NASD-operated quotation
market, its issuers registered under 12(g) rather than 12(b), and Rule 12d2-2 —
the rule Form 25 exists to serve — applies to securities listed on an
*exchange*. So a Nasdaq issuer that stopped being quoted in 2000 or 2001 had no
occasion to file a Form 25, and no exchange filed one for it.

**Status: CORROBORATED, not verified** — `sec.gov` is unreachable from this
environment. But it is consistent with the primary evidence actually in hand: the
`8-A12G` registration, and the total absence of any Form 25 from a 39-filing set
that does include a `15-12G`.

**The consequence for the denominator is large.** §2.1 already said Form 25 is
sparse before 2005 because it was paper-filed. This is a stronger and different
claim: for the *Nasdaq* population — which is most of the dot-com cohort — a Form
25 would not exist even in principle. Any per-year delisting count for 1998–2002
built on Form 25 is therefore not merely incomplete; it is close to empty for
exactly the securities the corpus is about.

**What replaces it:** the **Form 15 family**, which terminates or suspends a
registration regardless of where the security was quoted, and which IPET's set
does contain. That elevates §2.2's `15-12G`/`15-12B` signals from corroboration
to the primary dot-com-era exit evidence — and it re-emphasises the scope
discipline, because a Form 15 ends a *reporting* obligation and still says
nothing directly about when quotation ceased.

### 2.2 8-K item numbering changed in 2004

Modern item numbers (1.03 bankruptcy, 2.01 completion of acquisition, 3.01
delisting notice, 5.06 shell transaction) date from the **August 2004** 8-K
overhaul. Pre-2004 8-Ks used a different, coarser scheme (bankruptcy under Item
3, acquisition/disposition of assets under Item 2). Any parser must branch on
filing date, and the pre-2004 branch is coarser and needs text confirmation.

### 2.3 Filing cessation — a candidate signal, **never** a confirmed death

Given §2.1 and §2.2, cessation is the only signal that behaves the same across
the whole span — and it is also the weakest, so the design turns on refusing to
let it do more work than it can bear.

> **A CIK that filed periodically and then stopped has stopped *reporting*. That
> is all it has done.**

Whether the security was delisted, the class extinguished, or the issuer
liquidated is a separate question the absence of filings cannot answer:

| looks like cessation | actually is |
|---|---|
| acquired — the target stops filing | a real disappearance ✓ |
| went private | a real disappearance ✓ |
| delisted and deregistered | a real disappearance ✓ |
| became a wholly-owned subsidiary still filing debt covenants | *not* a disappearance ✗ |
| fell delinquent, then resumed 18 months later | *not* a disappearance ✗ |
| CIK changed after a reorganisation | *not* a disappearance — double-counts ✗ |

**The rules, enforced in code rather than remembered:**

1. Cessation resolves to **`POSSIBLE_EXIT_FILING_CESSATION`**, an evidence type
   of its own, never to any `CONFIRMED_*` type.
2. **It carries no lifecycle date.** Not an `evidence_date`, not an
   `effective_date`. The last periodic filing is recorded as *context*, labelled
   as context, and is not a death date. `assert_cessation_undated()` raises if
   one is ever attached, and `Denominator` runs it at construction, so a
   denominator that dated a cessation cannot be built at all.
3. A quiet period of **≥ 8 quarters** is required before it is even a candidate,
   and **a resumption retroactively cancels it**.
4. It stays unresolved until corroborating evidence says what happened. Only
   corroboration promotes it.
5. Cessation-only cases are **excluded from every per-year count** — they have no
   year — and are reported separately as `undated_exits`, so a reader can see how
   much of the population could not be placed in time.

### 2.4 Four lifecycles, deliberately not merged

EDGAR observes the **SEC reporting** lifecycle directly. Survivorship research
needs the **exchange listing** and **security class** lifecycles. They are
related and they are not the same:

| scope | what it asks | ends via |
|---|---|---|
| `ISSUER` | does the company exist and operate? | bankruptcy, dissolution |
| `SECURITY_CLASS` | does this security still exist? | merger consideration, extinguishment |
| `EXCHANGE_LISTING` | is it still listed and traded there? | Form 25 / 25-NSE |
| `SEC_REPORTING` | is the registrant still filing? | Form 15 family |

Each is observable through different forms and each ends at a different time. An
issuer can deregister while its shares keep trading over the counter; a class can
be extinguished while the issuer keeps filing; an issuer can be delisted and
continue to file. **A Form 15 is therefore never read as a delisting**, and
`CONFIRMED_SECURITY_EXTINGUISHED` is never read off a single form — it is derived
only from a delisting *and* a registration termination together, and is marked
`FORM_INFERRED` so it can never be mistaken for something a filing asserted.

**TradeIt ultimately needs the security/listing lifecycle, not the issuer's
filing lifecycle.** The reporting lifecycle is what EDGAR gives cheaply; the
listing lifecycle is what survivorship research is about; and the gap between
them is exactly what the evidence types are for.

---

## 3. Construction — implemented in `src/tradeit/edgar/`

```
1  index.py       quarterly full-index 1994 Q3 → present
                  (CIK, company name, form type, filed date, path → accession)
2  evidence.py    per filing: role, lifecycle scope, evidence type, strength.
                  Provenance preserved: CIK, form, filing date, accession,
                  source path, originating index quarter.
3  lifecycle.py   per CIK: timeline → ExitResolution.
                  direct forms win; delisting + deregistration derives
                  extinguishment; cessation is a dated-nothing candidate.
4  identity.py    CIK → ticker in four states. Name matching can never RESOLVE.
5  denominator.py counts by evidence type / strength / scope / year / lifespan;
                  cohort survival; two coverage bounds, never one.
6  pipeline.py    wiring; missing index quarters recorded, not silently zero.
```

Run it:

```
tradeit edgar fetch-recipe                    # shell to populate the index dir
tradeit edgar denominator --index-root DIR --as-of 2026-01-01
tradeit edgar controls                        # the 30-control verification table
```

The curated control identity is supplied to the build by default and its reach
is reported; see §4, *Handing the curated identity to the denominator*.

**Every count is published with its evidence strength attached.** A 2001
termination count of *N* where 80% is `cessation_only` is a different claim from
one where 80% is `form_direct`, and collapsing them into one number is the
mistake this design exists to avoid.

**No HTTP client ships with this.** `sec.gov` is unreachable from the build
environment, and a network fetcher that cannot be exercised is one that is wrong
in ways nobody has found yet. `fetch-recipe` prints the shell an operator runs in
their own environment, which also keeps the raw index files on disk — the right
shape for a corpus that has to be reproducible.

---

## 4. Identity mapping — four states, and `UNRESOLVED` is first-class

**EDGAR is CIK-centric. Historical ticker attribution is genuinely imperfect, and
pretending otherwise would fabricate the very thing being measured.**

| state | meaning | admissible use |
|---|---|---|
| `RESOLVED` | CIK ↔ ticker established from a dated primary source | counts toward the matched denominator |
| `AMBIGUOUS` | ≥ 2 plausible tickers, or a ticker with ≥ 2 plausible CIKs in the window | reported separately; **never** silently assigned |
| `UNRESOLVED` | no defensible mapping found | **counts toward the denominator, excluded from the matched numerator** |
| `MANUAL_VERIFIED` | a human checked primary evidence and recorded it with a citation | highest confidence; used for the control universe |

**`MANUAL_VERIFIED` is regulator-neutral, and there is no SEC/FDIC quality
ordering.** It means *a person read primary evidence and cited it*. An Exchange
Act Form 10-K filed with the FDIC under §12(i) is the same document filed where
the statute directs, not a weaker one, so the evidence hierarchy below ranks
*kinds* of evidence and never the regulator a filing was made to. Which
regulator is **provenance**, carried by the identifier's namespace.

### Evidence hierarchy for a mapping — strongest first

1. `MANUAL_VERIFIED` with a cited filing.
2. `company_tickers.json` — authoritative but **current listings only**, so useless
   for anything that died before today.
3. Form 8-A12B / Form 25 body text, which names the class of securities and the
   exchange.
4. `submissions` JSON `formerNames`, for rename chains.
5. EDGAR full-text search — **2001+ only**, so it does not reach the early window.
6. Company-name matching against the vendor roster. **Weakest. May produce
   `AMBIGUOUS`; may never produce `RESOLVED` on its own.**

### The rules

- **Never fabricate a ticker mapping.** Not for coverage, not for tidiness.
- **An issuer is discriminated by exactly one namespaced identifier, and never
  by its name.** The key is `<namespace>:<value>` — `SEC_CIK`, `FDIC_CERT` or
  `FRB_RSSD` — and the namespace is *part of* the key, so `SEC_CIK:59017` and
  `FDIC_CERT:59017` are two institutions that happen to share a number. Values
  are stored **verbatim** and normalized only for comparison, so `0001132979`
  and `1132979` are one identifier rather than two. A record's legacy `cik` is
  shorthand for a primary `SEC_CIK` and resolves through the same function as
  everything else; **one primary key per issuer, always** — two discriminators
  discriminate nothing. Every asserted identifier needs its own primary-source
  citation, and an identifier claimed by two issuers in one control is refused,
  because one institution recorded twice is a splice.
  **A security identifier is not an issuer identifier.** CUSIP, ISIN and FIGI
  name an *instrument*; one issuer may have several, so admitting one as an
  issuer key would make the uniqueness check compare instruments while claiming
  to compare issuers. They may never enter the issuer-identifier structure.
- **An identifier whose relation to the issuer is not established is recorded as
  unresolved, never as an alias.** It carries a cited finding stating what is and
  is not established, it can never satisfy the primary-key requirement, and it
  changes no count. An alias would be a sameness claim wearing a modest label —
  and shared names, shared addresses and shared officers are not identifiers.
- **`UNRESOLVED` is not a failure of the denominator — it is a measurement.** A
  denominator with 30% unresolved identity is still a valid denominator; it just
  reports two numbers instead of one (§5).
- Unresolved entries are **retained permanently**, not dropped. A later mapping
  upgrades the state; nothing is ever re-derived from scratch and silently
  changed.
- **A control is finished when its mappings are verified *and* its issuer question
  is settled.** These are two conditions, not one. `MANUAL_VERIFIED` describes the
  evidence quality of the mappings a record *happens to carry*; it cannot tell
  whether a control still owes an investigation. A control whose purpose is
  proving two issuers shared a ticker looks complete with one impeccably cited
  mapping — which is the `full-01` failure exactly. `ControlSecurity.required_issuer_investigations`
  states how many issuer identities a control must **settle** (not how many
  existed); the obligation is discharged either by recording enough independently
  evidenced mappings **or** by a cited `adjudication` finding that no further
  issuer was established. **A bare `complete: true` is refused**: "investigated
  and found nothing further" is an affirmative research conclusion and needs a
  finding, a citation and a `verified_on`, exactly as a mapping does.
- **`verified_on` is the operator's local calendar date on which a human reviewed
  the cited evidence, supplied explicitly.** It is never derived from a clock —
  not UTC, not system time, not commit time, not the filing's own date, not the
  acquisition run's date. The field records *when a person did something*, so the
  only authority on the day is that person's calendar. Deriving it would also get
  the ordinary case wrong, where verification precedes recording by days (`BEL`,
  `TGLO` and `ENE` were each read before they were committed). **If the review
  date is unknown, leave it null** and let the status fall below
  `MANUAL_VERIFIED` — a fabricated date is a fabricated claim about a person,
  which is the same failure class as a fabricated CIK. Records written before
  this rule existed are left as they stand; none is defective under a convention
  that did not exist, and rewriting them for cosmetic consistency would edit
  evidence to match a clock.

### Handing the curated identity to the denominator

`tradeit edgar denominator` supplies the control evidence file by default;
`--no-control-mappings` builds without it, which measures the corpus *without*
curated identity and is not the same statement as "no identity is established".
The projection is `security_mappings_by_cik`, and it is lossy in exactly two
ways, both of which are reported rather than absorbed:

- **The denominator is keyed by SEC CIK**, because it is built from EDGAR
  full-index rows. An issuer whose primary key is in another namespace has no
  registrant row to attach to and is returned as *not handed over*, with its
  namespace and that namespace's authority stated. `FRC` is the shipped case:
  it is the best-evidenced control there is, and it is absent from the identity
  section for a reason that is about the corpus and nothing about the evidence.
  **Omitting it silently would make an architectural boundary look like a gap.**
- **A dict holds one mapping per CIK**, so two issuers claiming one CIK would
  leave one silently overwritten — a splice produced by the handoff itself. It
  raises instead.

**Supplying a mapping is not identifying a registrant.** A mapping whose CIK
never appears in the index range being built affects no count; the report states
how many were handed over and how many actually attached, because those are
different numbers and only the second one did anything.

---

## 5. What the denominator reports

Two numerators against one denominator, always both:

```
matched_coverage    = vendor securities matched to RESOLVED denominator entries
                      ────────────────────────────────────────────────────────
                      RESOLVED denominator entries

bounded_coverage    = same numerator
                      ──────────────────────────────────────────────────────
                      RESOLVED + AMBIGUOUS + UNRESOLVED entries
```

`matched_coverage` is the optimistic bound (it assumes unresolved entries would
have matched); `bounded_coverage` is the pessimistic one (it assumes none would).
**The truth is between them, and reporting only the first is the standard way this
measurement is made to look better than it is.**

### Reported cuts

| cut | why |
|---|---|
| **by termination year, 1994–2026** | the shape must show 2000–2002 and 2008–2009 bulges |
| **by listing cohort year** | feeds cohort survival (§6) |
| **by lifespan bucket** (< 1y, 1–3y, 3–10y, > 10y) | short-lived names are the omission risk |
| **by exchange**, where a filing names one | AMEX and Nasdaq SmallCap are where small failures lived |
| **by terminal reason** | bankruptcy vs acquisition vs went-private are different populations |
| **by evidence strength** | a `cessation_only` count is a weaker claim and must look like one |
| **by identity state** | the gap between the two coverage bounds |

---

## 6. Cohort survival — the test that needs no vendor at all

For each listing cohort (CIKs whose birth event falls in year *Y*), the fraction
still filing 3, 5 and 10 years later.

Computed **twice**: once over the EDGAR denominator, once over the vendor's
roster. Then compared.

- The EDGAR curve is the reference: it is what actually happened.
- If the vendor's curve is **systematically flatter** — its cohorts survive better
  than reality — that *is* survivorship bias, quantified, with no need to
  enumerate a single missing name.

**This is the strongest single measurement in the whole probe**, because it is
robust to identity-mapping failure: it compares *shapes*, not memberships, so a
30% unresolved rate degrades it far less than it degrades `matched_coverage`.

---

## 7. Known biases, declared before the numbers exist

1. **Form 25 is largely absent pre-2005** (§2.1) — the dot-com window leans on
   Form 15 and cessation, which are weaker.
2. **8-K item semantics change in 2004** (§2.2).
3. **Non-common securities inflate the denominator** — preferred shares, notes,
   warrants and units each register and deregister. Filter by security class
   where the filing states it; where it does not, report the unfiltered count and
   say so.
4. **Funds and trusts** file on different schedules and should be excluded from an
   equity denominator, which requires classification that is itself imperfect.
5. **Foreign private issuers** use 20-F/40-F and Form 15F; either include them
   deliberately or exclude them deliberately, never accidentally.
6. **Non-filers are invisible.** A security that traded without SEC registration
   is outside EDGAR entirely. For exchange-listed US common stock this is a small
   population; for anything OTC it is not — which is one more reason OTC is
   declared out of scope.
7. **CIK is not a security.** One CIK can carry several listed classes; a
   reorganisation can produce a new CIK for a continuing business. The
   denominator counts *registrant-class events*, and the mapping to *securities*
   is exactly the imperfect step §4 refuses to fake.

**Therefore: the denominator is an estimate with characterised bias, used as a
floor and a shape.** It is not a census, it will never be exact, and it is
overwhelmingly better than the alternative, which is having nothing to compare
the vendor against.

---

## 7b. Limitations discovered while implementing it

Four that were not obvious from the design, and that change what the denominator
can claim.

**1. The full-index carries no 8-K item numbers.** Its five columns are CIK,
company name, form type, filing date and path. An 8-K in the index is just "an
8-K" — bankruptcy (1.03), completed acquisition (2.01), delisting notice (3.01)
and a change of auditor are indistinguishable without fetching and parsing the
document. Consequently **`CONFIRMED_BANKRUPTCY` and `CONFIRMED_ACQUISITION`
cannot be produced from the index at all.** They are implemented, they are
reachable, and they require a document-parsing pass that does not exist yet.
Every such signal is flagged `requires_document_text` and contributes nothing
until then.

**2. The index names no security class, so the counts are per *registrant*, not
per *security*.** A Form 25 removes a class from listing; the index does not say
which class. `security_class_known` is therefore `False` on every index-derived
row. A registrant with common stock, preferred and warrants produces one row
where the truth is three, and the direction of that bias is toward
under-counting securities.

**3. Filing dates are not effective dates.** A Form 25's effective date is set by
rule some days after filing. The index does not carry it, so `effective_date` is
`None` on every index-derived row and per-year counts are keyed on
`evidence_date` — the filing date — and say so. Borrowing the filing date would
have invented precision that nobody could later distinguish from measurement.

**4. A missing index quarter is a coverage gap, not a zero.** The pipeline
records missing quarters and the report prints them first. A denominator built
over a directory that quietly lacked 2001 QTR3 would report a real dip in
terminations, which is exactly the kind of artefact that survives into a
conclusion.

## 7bb. FIRST REAL RUN, 2026-08-29 — and the defect it exposed

**The denominator was built against the real archive for the first time on
2026-08-29**, ~~129 quarterly index files, 1994 Q3 – 2026 Q3~~ — **the range is
wrong as written; see the correction below** — pinned `--as-of 2026-08-29`.
Everything below is measured, not estimated.

| measurement | value |
|---|---|
| index files scanned | ~~129~~ → **128** (corrected below) |
| rows read, and independently re-read | 27,084,668 |
| FORM / CIK / DATE / PATH / ACCESSION mismatches | **0** |
| registrants | 90,548 |
| confirmed exits carrying a date | 40,920 |
| undated exits (cessation + unresolved) | 49,628 |
| control mappings attached to a registrant | 33 of 33 handed over |

The parser-integrity gate **PASSED** on all 27,084,668 rows.

### CORRECTION to this record — the run read 128 files, 1994 Q3 – 2026 Q2

The range line above was written by the session that performed the original run
and is wrong. It is struck rather than rewritten, because what a record said is
part of the record. **What was actually read:**

| | as written | as measured |
|---|---|---|
| index files scanned | 129 | **128** |
| range | 1994 Q3 – 2026 Q3 | **1994 Q3 – 2026 Q2** |

`tradeit edgar denominator` defaults to `--end 2026Q2`, and the published command
line passes no `--end`. There are **129 `form.idx` files on disk** — 1994 Q3 to
2026 Q2 is 128 quarters, and `2026/QTR3/form.idx` (23 MB, present since
2026-08-14) exists but was never opened. The claim of 129 appears to have been
taken from the file count on disk rather than from what the command read.

**Nothing else in the table moves, and this is checked rather than assumed.** A
re-run over the same default range reproduced 90,548 registrants, 40,920 dated
exits and 49,628 undated exits exactly — which is also what establishes that the
*original* run read 128 files, since a 129th quarter of filings could not have
left every count identical. The row total, the parser-integrity result and the
control mapping figures stand as written.

The stale default is a coverage question in its own right and is **not** resolved
by this correction. It is proposed separately in §7bd.

### The defect: 30.7% of dated exits contradict their own evidence

**`resolve_exit` takes the *earliest* confirming filing** — `lifecycle.py:180-181`,
`confirming.sort(...)` then `confirming[0]`. A registrant that deregisters one
class of securities and keeps reporting is therefore recorded as having exited on
the date of that first Form 15.

Measured against the real archive:

| check | count | share |
|---|---|---|
| exit dated **before** the registrant's last periodic report | **12,549** | **30.7%** of dated exits |
| … of those, overlap > 5 years | 1,948 | |
| resolutions resting on more than one confirming filing | 11,239 | 27.5% |
| exit dated *after* the last periodic report (the normal case) | 20,813 | median lag **0.21 years** |

**The worst cases are not obscure.** `INTEL CORP` is recorded as exiting
1994-08-02 while still filing in 2026 — a 31.7-year contradiction. So are
`ADOBE INC.` (1995), `CONAGRA BRANDS` (1995) and `TJX COMPANIES` (1996). Every
one is listed and trading today.

**This is the opposite of survivorship bias**, and worth naming as its own
failure: the corpus does not omit dead companies, it *invents* dead ones. A
vendor measured against this denominator would be asked to supply delisted
history for Intel, and marked down for not having it.

**Two notes on the shape of the fix, which is not yet made.**

* The extinguishment branch eight lines above already uses `confirming[-1]`, the
  *latest*. Same function, opposite choice, no comment explaining why — which
  reads as an oversight rather than a decision.
* Taking the latest is probably right and is **not obviously sufficient**: a
  registrant whose last periodic report post-dates *every* confirming filing has
  not exited at all on this evidence, and the honest resolution may be to refuse
  a date rather than to pick a better one. `evidence_date < last_periodic` is a
  contradiction the type system currently permits and nothing checks.

**Until it is fixed, the per-year curve may not be used to evaluate a vendor.**
The row counts, the registrant total and the undated population are unaffected —
the defect is in *dating* an exit, not in detecting one.

### Two predictions this run falsified, recorded because they were wrong

Both were made before the numbers existed, and both were contradicted:

1. *"2000–2002 and 2008–2009 will show visible bulges; if not, the build is
   broken."* They do not. The peak is 2005–2007 (2,827 / 3,012 / 3,113) and 2009
   (1,318) is lower than 2002 (1,546).
2. *"The peak is the dot-com wave arriving 3–5 years late as paperwork."* The
   median lag between last periodic report and exit filing is **0.21 years**.

**No third explanation was offered at the time**, deliberately: the curve was
built from a dating rule known to be wrong on 30.7% of its inputs, and explaining
a number before correcting the defect underneath it is how a wrong number
acquires a defender. Fix, re-run, then look. That has now been done, and what it
showed is recorded below.

### What the fix showed: the peak was mostly an artefact, and it moved

Measured on the re-run after supersession (§7bc), same pinned `--as-of`, same
`--end 2026Q2` range on both sides:

| year | before | after | change |
|---|---|---|---|
| 2005 | 2,827 | 1,393 | **−51%** |
| 2006 | 3,012 | 1,205 | **−60%** |
| 2007 | 3,113 | 1,417 | **−54%** |
| 2008 | 2,284 | 1,026 | −55% |
| 2003 | 1,777 | 1,198 | −33% |
| 2012 | 1,679 | 1,518 | −10% |
| 2002 | 1,546 | 1,208 | −22% |
| 1999 | 1,435 | 1,111 | −23% |

**Roughly half of the 2005–2007 block was registrants that never exited.** Two
consequences, both measured rather than inferred:

**1. The peak moved.** It is no longer 2005–2007. Ranked after the fix:

| rank | before | after |
|---|---|---|
| 1 | 2007 — 3,113 | **2012 — 1,518** |
| 2 | 2006 — 3,012 | 2007 — 1,417 |
| 3 | 2005 — 2,827 | 2005 — 1,393 |
| 4 | 2008 — 2,284 | 2002 — 1,208 |
| 5 | 2004 — 1,806 | 2006 — 1,205 |

**2. The curve flattened, which is the more important half.** Before, the top
year led the eighth by 2.0×; after, by 1.37× (1,518 against 1,111). The old
"peak" was substantially the defect's own footprint, and what remains does not
have a dominant year so much as a broad 1999–2012 plateau. **A curve with no
sharp peak is a different object to reason about than one with a peak in the
wrong place**, and any reading of it starts from here rather than from §7bb's
ranking.

### The third explanation is a hypothesis, and is not graduating

Two predictions above were falsified. A third is now available and is **recorded
as a hypothesis with a mechanism, not as a finding**, precisely because the first
two were stated confidently and were wrong:

> *Hypothesis.* `ELECTRONIC_FORM_25_FROM` is 2005-04-24 (§2.2). Once Form 25
> became an electronic filing, **surviving** registrants began filing them to
> remove individual classes — a warrant, a preferred series, a tracking stock —
> and the old rule read every one as the registrant's death. That would put the
> artefact's onset in 2005 and concentrate it in the years just after, which is
> where it is.

**What would falsify it**, stated before anyone goes looking, in the manner §7
requires: the superseded population should be disproportionately Form 25 rather
than Form 15, and disproportionately post-2005 in filing date. Neither has been
measured. The `superseded` field added in §7bc carries the filings needed to
check it, and nothing in this document may treat it as established until someone
does.

**The 2012 peak is not explained at all**, and is not speculated about here. It
fell only 10%, so it is largely untouched by the defect and is not an artefact of
it — which makes it a real feature of the corpus that has never been accounted
for. That is a question for whoever reads the corrected curve, and reading it is
work that has not been done.

> **RESOLVED the same day — §7bc.** Everything above is left exactly as it was
> written, because it is the record of what the first run found. Two of its
> forward-looking statements are now out of date and are corrected here rather
> than edited above: the fix *has* been made, and it is **not** the
> `confirming[-1]` the section guessed at — taking the latest filing was
> considered and rejected, because it would still have handed Intel a date. The
> rule adopted is supersession. The measurements, including the fate of the
> 2005–2007 peak this section declined to explain, are in §7bc.

## 7bc. THE PROPOSITION that fixes §7bb — supersession

Denominator methodology may not be altered by a silent patch, so the change is
stated here first, in the terms it will be judged on. It was proposed on
2026-08-29 against the defect measured the same day.

### The question, restated

The obvious repair is `confirming[-1]` instead of `confirming[0]` — take the
latest confirming filing rather than the earliest. **That repair is rejected.**
It answers the wrong question. It would still hand `INTEL CORP` an exit date,
merely a less absurd one, and the thing wrong with Intel's record is not that
the date is early. It is that Intel has not exited.

The real question is the one §2.3 already answers for cessation, asked in the
other direction. There, the rule is that *a resumption retroactively cancels a
cessation candidate*. Here: **a periodic report retroactively cancels the
reading of an earlier filing as the registrant's exit.** A registrant cannot
report after it has exited. Same principle, opposite sign, and it was never
implemented.

### The rule

> **A confirming filing dates an exit only if the registrant filed no periodic
> report after it.** A filing that precedes the registrant's own last periodic
> report is **superseded**: it remains in the record as evidence at its own
> scope, and it is disqualified from supplying the exit date.

Three consequences, each deliberate:

**1. Supersession is a disqualification, not a veto.** A superseded Form 25
still removed a listing — §2.4 says outright that "an issuer can be delisted and
continue to file", so continued reporting does not falsify the filing, only the
reading of it as *the registrant's* exit. Superseded filings keep contributing
their scopes and stay attached to the resolution in a new `superseded` field, so
a reader can see what was refused without going back to the index.

**2. If every confirming filing is superseded, no date is offered at all.** Not
a better date — none. This is the Intel case, and it gets its own evidence type,
`NON_EXIT_REGISTRANT_STILL_REPORTING`, on the same reasoning that gives cessation
its own: the honest content of the record is "something ended, the index cannot
say what, and the registrant is still here." The index names no security class
(§7b limitation 2), so this cannot be sharpened into a class-level fact from the
index alone. It is `FORM_INFERRED` — derived by combining the confirming filings
with the periodic reports that outlive them — and it carries **no date**.

**3. It is not counted as an undated exit.** Folding these into `undated_exits`
would have made the totals reconcile in one line and would have preserved the
original error in a quieter form, because `undated_exits` means *exits we
believe happened and cannot place in time*, and we do not believe these
happened. They are reported on their own line.

### Where the date now comes from

| case | date | why |
|---|---|---|
| no standing filing | **none** | the registrant did not exit |
| single-type exit | **earliest standing** | first uncontradicted direct evidence |
| extinguishment | **latest standing** | the conjunction does not exist until its later half is filed |

**Earliest standing, deliberately, for the ordinary case.** Taking the latest
would re-date the 20,813 registrants §7bb measured as *already correct* — their
confirming filings all post-date their last periodic report, so nothing about
them is in question. A later Form 15 is usually the administrative tail of the
same exit. The fix changes the rows the defect touched and leaves the rest
where they were.

**The extinguishment branch keeps `[-1]`, and it is no longer unexplained.**
§7bb called the asymmetry "an oversight rather than a decision". On inspection
it is defensible and now carries the comment it lacked: the derived claim rests
on a *conjunction* of a delisting and a registration termination, and that
conjunction does not exist until the later of the two is filed. Dating it from
the earlier one would assert the derived event before its own evidence was
complete. The asymmetry with the single-type branch is real and intended — the
two branches are dating different things.

### The assertion

`evidence_date < last_periodic` was, as §7bb noted, "a contradiction the type
system currently permits and nothing checks." It is now
`assert_exit_not_contradicted()`, the exact companion of
`assert_cessation_undated()`, run by `Denominator.__post_init__`. **A
denominator containing a self-contradicting exit cannot be built at all**, and
the failure names the worst offender rather than a count alone.

Two boundary decisions, both narrow:

* **Same-day is not "after".** A registrant filing its last 10-K and its Form 25
  on one day is an ordinary exit. The filter's boundary (`>= last_periodic` is
  standing) is *identical* to the guard's (`< last_periodic` is a contradiction),
  so the two can never disagree about a marginal case. This is why the guard is
  a property on `ExitResolution` rather than an inline comparison.
* **`effective_date` is checked too**, though the index never populates it. The
  document-parsing pass that will populate it is the obvious place for this
  defect to reappear in a new form.

### Measured after the fix — re-run 2026-08-29, same pinned `--as-of`

Both runs used the identical index range, so the columns are comparable.

| measurement | before | after |
|---|---|---|
| registrants | 90,548 | **90,548** |
| dated confirmed exits | 40,920 | **29,180** |
| — `confirmed_exchange_delisting` | 3,940 | 2,359 |
| — `confirmed_registration_termination` | 29,205 | 19,772 |
| — `confirmed_security_extinguished` | 7,775 | 7,049 |
| `non_exit_registrant_still_reporting` | — | **11,740** |
| undated exits (cessation + unresolved) | 49,628 | **49,628** |
| exits contradicting their own evidence | 12,549 | **0** |

**The denominator built, which is the result.** `assert_exit_not_contradicted`
runs at construction over all 90,548 registrants; before the fix it would have
refused. Registrant total, undated population and identity mapping are byte-for-
byte unchanged, as a dating-only change requires.

Supersession reached **22,776 confirming filings across 14,733 registrants**.
Of those registrants, 11,740 had no standing filing left and lost their date;
**2,993 kept a dated exit**, taken from a filing their own later reporting does
not contradict — which is the "disqualification, not veto" rule doing visible
work rather than merely being asserted.

**Every figure closes against §7bb's independently measured 12,549:**

* 40,920 − 29,180 = 11,740 — the drop in dated exits *is* the non-exit
  population, exactly.
* 11,740 + 2,993 = 14,733 — every registrant with superseded evidence is
  accounted for in one of the two outcomes.
* 12,549 − 11,740 = 809 single-type cases that had a later standing filing;
  14,733 − 12,549 = 2,184 extinguishment cases already dated from
  `confirming[-1]` and so never contradicted; 809 + 2,184 = 2,993.

The last line is the useful one: the 2,184 are precisely the registrants the old
extinguishment `[-1]` had already protected by accident. That the two branches
reconcile to the digit is the strongest available evidence that supersession
describes the same population §7bb measured, rather than a different one that
happens to be a similar size.

**All four named cases are fixed.** Resolved against the real archive:

| CIK | registrant | evidence type | date | last periodic |
|---|---|---|---|---|
| 50863 | INTEL CORP | `non_exit_registrant_still_reporting` | **none** | 2026-04-24 |
| 796343 | ADOBE INC. | `non_exit_registrant_still_reporting` | **none** | 2026-06-15 |
| 23217 | CONAGRA BRANDS INC. | `non_exit_registrant_still_reporting` | **none** | 2026-04-01 |
| 109198 | TJX COMPANIES INC /DE/ | `non_exit_registrant_still_reporting` | **none** | 2026-05-29 |

Intel carried four superseded confirming filings and ConAgra six. None of the
four is dated, none contradicts its evidence, and none appears in any per-year
count.

### What the corrected curve looks like — recorded in §7bb

The per-year effect is **not duplicated here**. §7bb is where the curve was first
reported and where its two falsified predictions live, so the before/after
numbers, the peak moving from 2007 to 2012, and the electronic-Form-25 hypothesis
are recorded there, in place, against the predictions they bear on. Keeping one
copy is deliberate: two copies of a table drift, and this document has already
had to correct one number that rotted.

The short form: **roughly half of the 2005–2007 peak was registrants that never
exited**, the curve flattened, and the surviving 2012 peak is unexplained. The
mechanism offered is a hypothesis with a stated falsification test, not a
finding.

### A separate discrepancy, found while re-running and NOT fixed here

Re-running surfaced that the command reads **128** index files, not the 129
§7bb recorded, because `--end` defaults to `2026Q2` and a populated
`2026/QTR3/form.idx` is never opened. §7bb now carries the correction to its own
record; **§7bd proposes what the default should be**, as a decision rather than a
quiet change.

It is deliberately not folded into a dating fix. Both sides of every before/after
number in this section used `--end 2026Q2`, so the comparison holds whichever way
§7bd is decided.

### What this does not fix

Supersession is a *dating* rule and touches nothing else. Row counts, the
registrant total, the parser-integrity gate and the identity mapping are
untouched by construction. It also does not make the per-year curve explicable:
§7bb recorded two falsified predictions and declined to offer a third
explanation until the dating rule was corrected. It is corrected now; the curve
still has to be looked at, and that is a separate piece of work with its own
evidence.

## 7bd. PROPOSITION: what `--end` should default to

**Not implemented.** Stated here for a decision, because changing the default
changes the denominator's coverage, and a coverage change that arrives as a
silently different number is the thing this document exists to prevent.

### The defect, stated plainly

`tradeit edgar denominator --end` defaults to the literal string `2026Q2`
(`cli_edgar.py`), and `BuildOptions.end` defaults to `IndexQuarter(2026, 2)`
(`pipeline.py`). The published command line passes no `--end`. So
`2026/QTR3/form.idx` — present on disk since 2026-08-14 — has never been read by
any run, and §7bb recorded a range it did not use.

**Two failures, and the second is worse than the first.**

1. A hard-coded quarter in a default is a number that rots. It was presumably
   current when written. `tests/unit/test_documented_counts.py` exists because
   three documented counts already rotted this way.
2. **The truncation is silent.** `LocalFullIndexSource` is careful in one
   direction — an absent quarter is appended to `missing` and the report prints
   it first, because "a missing quarter is a coverage gap, not a zero" (§7b
   limitation 4). There is **no counterpart for a quarter that is present and
   never requested.** The report never states the range it read, which is
   exactly why the 129-vs-128 error survived into a published record and had to
   be corrected in §7bb rather than caught.

The asymmetry is the real finding. The code already knows that quarter coverage
is a property of the measurement rather than an implementation detail; it just
enforces it on one side only.

### What is proposed

**Default `--end` to the latest quarter present under `--index-root`, discovered
at run time; keep `--end` as an explicit override for pinning a published
number; and report the range actually read, in both directions.**

| | behaviour |
|---|---|
| no `--end` | read to the newest quarter on disk, and print the range read |
| `--end 2026Q2` given | read to 2026 Q2, and print *"2026 QTR3 present and not read"* |
| quarter absent mid-range | unchanged — recorded in `missing`, printed first |

Concretely: a `latest_present` discovery on `LocalFullIndexSource`, a
`not_requested` list beside the existing `missing` list, and a range line at the
top of the report next to the missing-quarter line.

### Why this rather than the alternatives

| option | rejected because |
|---|---|
| bump the default to `2026Q3` | fixes today and rots tomorrow; it is the same defect with a later date, and would need a human to notice again |
| default to the current calendar quarter | makes coverage depend on the day the command is run, which `BuildOptions.as_of` already warns against for exactly this reason — and it would silently request a quarter that may not have been downloaded, converting a coverage gap into a `missing` entry that is really an operator error |
| require `--end` explicitly, no default | reproducible, and it relocates the rot into the operator, who must keep a quarter number current by hand and will eventually paste a stale one. It also makes the common case fail closed on something that is not actually ambiguous |
| leave it, document it | the range is not printed, so a reader cannot tell what was read. That is how this got into §7bb |

**The reproducibility objection, answered.** Defaulting to disk means the same
command can return different numbers as the archive grows. That is a real cost
and it is accepted, on two grounds: the run **prints the range it read**, so a
number is never again reported without its coverage; and a published figure is
pinned with an explicit `--end` alongside the `--as-of` it already pins. The
alternative — a default that is stable because it is stale — buys reproducibility
by quietly discarding data, which is the worse trade for a survivorship corpus.

### What this does not claim

Reading 2026 Q3 will **change the published numbers**, and the direction is not
predicted here. It adds filings, so registrant and exit counts can only rise or
hold; what it does to the per-year curve for 2026 is a measurement, not a
guess. **The current-year count is partial regardless** — 2026 is an incomplete
year in any range — and the report does not currently say so, which is arguably
a third instance of the same omission and is left for the same decision.

Nothing in §7bb or §7bc is affected: both sides of every before/after comparison
there used `--end 2026Q2`, so the deltas hold whichever way this is decided.

## 7be. READING THE CORRECTED CURVE, 2026-08-30

§7bc corrected the dating rule and said the curve still had to be read. This is
that reading. Everything here is measured over the same `--end 2026Q2` range.

### The finding: this is not one measurement, and its meaning changes along its own x-axis

The per-year total hides the only thing that matters about it — **which
lifecycle each year's exits belong to.**

| year | total | reg. termination | delisting | extinguished | listing-lifecycle share |
|---|---|---|---|---|---|
| 1994 | 30 | 30 | 0 | 0 | **0.0%** |
| 1997 | 714 | 714 | 0 | 0 | **0.0%** |
| 1999 | 1,111 | 1,111 | 0 | 0 | **0.0%** |
| 2000 | 1,034 | 1,034 | 0 | 0 | **0.0%** |
| 2001 | 1,044 | 1,040 | 1 | 3 | 0.4% |
| 2002 | 1,208 | 1,079 | 58 | 71 | 10.7% |
| 2005 | 1,393 | 1,251 | 51 | 91 | 10.2% |
| 2007 | 1,417 | 794 | 128 | 495 | 44.0% |
| 2012 | 1,518 | 1,071 | 163 | 284 | 29.4% |
| 2016 | 1,015 | 575 | 104 | 336 | 43.3% |
| 2020 | 560 | 234 | 64 | 262 | 58.2% |
| 2023 | 885 | 239 | 116 | 530 | **73.0%** |
| 2026 | 429 | 129 | 113 | 187 | 69.9% |

**From 1994 to 2001 the curve contains no exchange-listing evidence whatsoever** —
not "few", zero, with a single delisting in 2001. Every exit in those years is a
Form 15: a *reporting* exit. By 2023 nearly three quarters of each year is
listing-lifecycle evidence.

So the y-axis is not one quantity. **A 1999 count and a 2023 count are different
measurements wearing the same units**, and any statement of the form "exits fell
from X to Y" across that boundary is comparing a Form 15 count to something
mostly made of Form 25s.

### This is §2.1b and §2.2 arriving as data rather than as a caveat

The document predicted the mechanism and did not predict its severity.
`ELECTRONIC_FORM_25_FROM` is 2005-04-24, and Nasdaq issuers of the era would not
file a Form 25 at all (§2.1b). Measured, by the form that actually supplied each
exit date:

| | 1994–2001 | 2006–2012 | 2023–2026 |
|---|---|---|---|
| `25-NSE` | **0** | 730 | 501 |
| `25` | 2 | 201 | 37 |

`25-NSE` — the exchange-filed notification that is the *bulk* of all listing
evidence in this corpus (32,482 filings, more than any other exit form) — **does
not appear even once before 2006.**

### What this does to the two falsified predictions of §7bb

**Prediction 1 stays falsified, and now has a cause.** "2000–2002 and 2008–2009
will show visible bulges." They still do not: 2000 is 1,034 and 2001 is 1,044
against 1,111 in 1999, and 2008–2009 (1,026 / 962) sit *below* 2002 (1,208).
Correcting the dating defect did not resurrect them.

**But the reason is now identifiable, and it is instrumental rather than
historical.** A dot-com *delisting* wave could not appear in this curve however
large it was, because the corpus holds no delisting evidence for those years. The
prediction was not merely wrong; it asked the data a question the data cannot
answer. **The absence of a dot-com bulge is evidence about the instrument, not
about 1999–2002.**

### The 2012 peak, previously unexplained

§7bb left it open. It is **a Form 15-15D event and nothing else**:

| | 2011 | **2012** | 2013 |
|---|---|---|---|
| dated by `15-15D` | 284 | **835** | 295 |
| dated by everything else | 621 | 683 | 629 |
| total | 905 | **1,518** | 924 |

Every other form is flat. The entire excess is suspensions of the duty to file
under Section 15(d).

> *Hypothesis, not a finding.* The JOBS Act of April 2012 raised the holder
> thresholds at which a registrant may suspend reporting. A one-year wave of
> 15-15D filings by newly-eligible registrants would produce exactly this shape.
> **What would falsify it:** the 2012 excess should be concentrated after April
> 2012 rather than spread across the year, and should skew toward small
> registrants and banks. Neither has been measured.

### The §7bb hypothesis, tested: half of it is wrong

§7bb offered the electronic-Form-25 mechanism for the 2005–2007 artefact and
wrote its falsification test in advance — "the superseded population should be
disproportionately Form 25 rather than Form 15, and disproportionately post-2005
in filing date." Both limbs are now measured over all 22,776 superseded filings.

| limb | prediction | measured | verdict |
|---|---|---|---|
| timing | skews post-2005 | 75.6% filed 2005+, peaking 2006–2007 | **holds** |
| form | skews Form 25 | Form 25 family **39.6%**, Form 15 family **60.4%** | **FALSIFIED** |

The single largest contributor is **`15-15D` at 45.8%** — 10,424 filings — more
than both Form 25 variants combined.

**This is the third prediction this document has recorded and then falsified, and
the first that was written by the party proposing the fix.** It is left standing
rather than quietly restated, on the same reasoning as the other two.

> *Replacement candidate, unmeasured.* `15-15D` suspends a **Section 15(d)**
> obligation arising from a registered offering. A registrant that is also listed
> under 12(b) keeps reporting on that separate obligation, so a 15-15D followed
> by years of further 10-Ks is not anomalous but *routine* — which would make it
> the natural dominant source of superseded filings. **What would falsify it:**
> registrants whose superseding filing is a 15-15D should disproportionately hold
> a 12(b) registration (an `8-A12B` birth) at the time of filing. Not measured.

### Two anomalies found while reading, both left open

**1. `15-12B` stops existing after 2022.** Verified against the raw index rather
than inferred from the pipeline:

| | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|
| `15-12B` | 312 | 125 | **0** | **0** | **0** |
| `15-12G` | 139 | 345 | 649 | 469 | 448 |
| `15-15D` | 328 | 777 | 183 | 130 | 147 |

**No replacement string appears** — the 2023+ indexes contain no new `15*` form
type. Termination of a 12(b) registration did not cease to happen in 2023, so
this is a change in the record rather than in the world, and its cause is
unknown. **The 2023–2026 end of the curve should not be read as comparable to
2019–2022 until this is explained.**

**2. `15F-15D` is not classified, and that is an inconsistency rather than a
decision.** `FORM_SIGNALS` carries `15F-12B` and `15F-12G` but not `15F-15D`, the
foreign private issuer analogue of the single most common exit form in the
corpus. **184 filings** are silently dropped as `IRRELEVANT`.

By contrast, the amendment forms — `25-NSE/A` (604), `15-12G/A` (446),
`15-15D/A` (339), `15-12B/A` (111) — are **correctly** excluded: an amendment to
a Form 25 is not a second delisting, and admitting them would double-count.
That distinction is why this was read rather than swept.

### What the curve may now be used for, and what it may not

**May:** compare years *within* the 2006–2022 regime, where composition is
broadly stable; measure reporting exits across the whole span; and serve as the
denominator against which a vendor's delisted roster is measured **for the years
in which listing evidence exists**.

**May not:** be read as a delisting curve before 2006; be used to compare across
the 2002 or 2023 boundaries; or be used to evaluate a vendor's 1998–2002
delisted-price coverage — **the window `research-01` exists to reconstruct is
precisely the window in which this instrument cannot see delistings at all.**

### The consequence for milestone 0c, which is sharper than it first looks

That last point lands directly on 0c, and the vendor correspondence lands on it
from the other side.

EODHD stated in writing on 2026-08-29 that it provides EOD data for active **and
delisted** securities, while **expressly declining to certify** that the complete
US universe, every security from 1998 onward, all delisting reasons or every
lifecycle event is present, absent a paid requirements review
([`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) §1.3, verbatim).

**The refusal predates our asking for it.** The reframe — stop asking a vendor to
certify completeness, ask only for the cheapest access that lets us measure it —
was sent *after* that reply was written. Two parties reached the same boundary
independently, which is corroboration that the boundary is real rather than a
negotiating posture.

**But the reframe rests on an assumption this section has just qualified.** It
works because 0a built an instrument that measures completeness for us. Measured
above, that instrument holds **no exchange-listing evidence before 2006** —
`25-NSE` does not appear once until then, and 1994–2001 is 100% Form 15.

So for **1998–2002**, the window `research-01` exists to reconstruct:

| | can it speak to delisted completeness there? |
|---|---|
| the vendor | **no** — declines to certify without a paid review |
| our denominator | **no** — holds no delisting evidence for those years |

**Neither party can currently answer the question that matters most, and the
reframe does not change that** — it correctly stops us paying for an assurance,
and it does not conjure the measurement it substitutes. Closing this needs the
document-parsing pass (§7b limitation 1) or a non-EDGAR listing source. **It does
not need another vendor question**, and asking one would produce an answer we
still could not check.

## 7c. The parser-integrity gate, and one anomaly left open

The denominator's classification consumes exactly three fields per row — `cik`,
`form_type` and `filed_at`. `path` and `accession` are provenance. A reported
accession discrepancy on a 2002 IPET Holdings 10-K made it necessary to
establish, rather than assume, that none of the five is corrupt anywhere in the
corpus.

`tradeit edgar audit-paths --index-root DIR` is that instrument. For every parsed
row across all 129 quarterly `form.idx` files it re-reads the exact source line —
`FullIndexRow.source_line` makes the alignment exact rather than inferred — and
compares form type, CIK, filing date, File Name and accession against an
**independent** read of that line.

Independent means what it says. The production reader peels a fixed-width row
from the right with `rsplit(maxsplit=3)` and separates the two free-text fields
using the header's column offsets. The auditor never looks at the header and
never splits on token counts; it matches the row's *shape*, with padding runs of
two or more spaces as the delimiters and the CIK, date and File Name each pinned
by their own literal form. Pipe rows are read by field shape too, with the
layout inferred from where the CIK sits rather than from the header's order. An
audit that called the parser would be an audit of nothing.

Three rules keep the result honest:

- **Nothing is repaired, normalised away or skipped.** A repair destroys the
  evidence being sought. The parser's one documented normalisation — it
  upper-cases form type — is compared case-insensitively *and* the case-only
  difference is counted on its own line.
- **Rows the auditor cannot read are reported, not passed.** "Could not check"
  and "checked and agreed" are different facts and are printed as different
  numbers. A row whose company name adjoins its CIK with no delimiter
  (`...GENERAL L P5011`) is readable only by taking the CIK from the path, which
  is the same corroboration the parser uses; those rows are counted under their
  own heading and the verdict names them rather than absorbing them.
- **Duplicate File Names stay as occurrences.** One File Name can legitimately
  appear on more than one index line — the same document listed under two form
  types. An earlier version of this audit collapsed them into a set and compared
  that against a row list, which manufactured a shortfall of 1,523 rows that
  looked like missing coverage and was not.

The verdict is decided in code, not in prose: any mismatch in `cik`, `form_type`
or `filed_at` prints `STOP` and names the denominator; a `path` or `accession`
mismatch alone is reported as provenance damage; unreadable rows withhold the
gate rather than pass it.

### The gate result, and the two rows it turns on

Across all 129 quarters: **27,084,670 raw candidate rows, 27,084,668 accepted by
the parser, 0 FORM / CIK / DATE / PATH / ACCESSION mismatches** against an
independent read of every accepted row. The 1,525 duplicate File Names are
occurrences of one document indexed under two form types, which is a property of
the index; an earlier version of this audit compared a de-duplicated set against
a row list and reported 1,523, the difference being exactly the two rows below.

Exactly **two** rows in 27 million are refused, both malformed the same way —
the company-name column is blank, so there is one free-text field where the
layout requires two, and the parser returns `free_text_split_failure` rather than
invent a name:

| quarter | line | form | CIK | filed | accession |
|---|---|---|---|---|---|
| 1997-QTR1 | 54239 | `SC 13D` | 1036125 | 1997-03-24 | `0000950134-97-002093` |
| 2016-QTR1 | 171819 | `485BPOS` | 1593547 | 2016-02-26 | `0001135428-16-001124` |

Neither can affect any published number, and the reason is structural rather
than lucky. `classify_form` puts both form types at `FormRole.IRRELEVANT` — an
`SC 13D` is a third party's beneficial-ownership report about an issuer, a
`485BPOS` is an investment company's post-effective registration amendment, and
neither is a birth, an exit, a periodic report or a transaction pointer.
`evidence_from_rows` then drops every IRRELEVANT row **before**
`build_timelines` groups anything, so such a row cannot create a registrant,
cannot extend a filing window, cannot contribute an accession and cannot reach a
year bucket. That the two rows' File Names are unique in their quarters is
therefore not load-bearing: uniqueness would matter only for a form type the
methodology reads, and these are not.

The counterfactual is pinned in tests rather than asserted: each row is injected
in memory against seven shapes of prior history — none, periodic-only,
deregistration, delisting, delisting-plus-deregistration, exit-candidate-only and
birth-only — and the resolution is identical in every case, as is the full
denominator report including the registrant count. `tradeit edgar cik-lifecycle
CIK --inject 'FORM|DATE|path'` runs the same counterfactual against the real
corpus, in memory, writing nothing.

**The parser is not changed to admit them.** A row with no readable company name
is a row for which the parser would have to fabricate a field, and making
candidate counts equal accepted counts is not a reason to do that. The two are
recorded here as known malformed-source exceptions, deliberately refused.

On that basis the **EDGAR parser / classification-input / denominator integrity
gate is closed**: the 27,084,668-row denominator stands and requires no
regeneration.

**The IPET anomaly itself remains open.** The recorded status is: *unexplained,
not reproducible on the current or committed implementation; no committed code
change explains it.* `cmd_cik_filings` is byte-identical across the commit that
introduced it and the commit that followed, the path-verbatim invariant added in
between only raises and never assigns, and no earlier implementation of the
command exists. No cause is claimed. It is written down here so that a future
recurrence is recognised as a second occurrence rather than a first.

## 7d. MEASURED 2026-09-05 — 1,875 registrants that kept reporting after the
denominator says they exited

**A finding, not a change.** Methodology is not altered here.

### The first version of this test was too weak, and is recorded as such

The test first run intersected the dated exits with SEC's
`company_tickers.json` and found 368 (1.26%). **That test does not support the
claim made of it.** `company_tickers.json` carries `cik_str`, `ticker` and
`title` and *no exchange field* — it is SEC's CIK-to-ticker map for filers, not
a listing register. A registrant that left an exchange, still trades OTC and
still files keeps its entry, so membership is not evidence of listing. The 368
is left written down because a weak test presented as a strong one is the
error worth remembering, not because the number means what it appeared to.

### The test that does hold

A registrant that files its own periodic report **after** the date it is said
to have exited contradicts the exit directly, and needs no external file: the
EDGAR full index already holds both facts.

Restricted to the 21,116 dated exits whose filing history in this corpus is
complete — issuers seeded from the full index, for which every index row was
inserted — and counting only forms in which the registrant itself reports:

```
dated exits on complete filing data                21,116
...that filed a periodic report after their exit    1,875   (8.88%)
      still reporting 1 year later                    685
      still reporting 2 years later                   506
      still reporting 5 years later                   309
median continuation 0.6 years; longest 29.6 years
```

**The restriction matters and is not cosmetic.** The XBRL-cohort issuers
(`sec_fsds_sub`) have incomplete filing rows — 7,169 of 7,255 show a last
filing earlier than their own exit date, because the index seeder skipped
issuers already held and never inserted their filings. Measuring across all
sources gives a similar percentage for the wrong reason, and would be an
artefact of what was stored rather than of what was filed.

### Why it happens

| family of the contradicting filing | n | |
|---|---:|---|
| domestic (`10-K`, `10-Q`, `8-K`) | 1,037 | cause not yet diagnosed |
| fund (`NPORT-P`, `N-CSR(S)`, `N-CEN`) | 781 | reports under the Investment Company Act |
| foreign (`20-F`, `40-F`, `6-K`) | 162 | annual cadence is `20-F`, not `10-K` |

An exit is dated at the registrant's last filing *of a form the lifecycle logic
weighs*. Where that form set is narrower than the set in which a registrant
actually reports, a live issuer looks dormant. That explains the fund and
foreign families cleanly; the domestic 1,037 are the largest group and the
least understood, and no cause is claimed for them here.

A later `Form 4`, `Schedule 13G/A`, `D` or `EFFECT` is **not** a contradiction —
those are filed by insiders, holders or the registration process rather than by
the registrant reporting — and they are excluded from the count above. 7,451
index-seeded exits have some later filing of that kind, which is expected and
is not a defect.

### Direction of the error

A registrant falsely marked dead **inflates the denominator**, so coverage
looks *worse* than it is and never better. The gate cannot be made to pass by
this defect, only to fail harder than it should.

It is still not material at present scale. Re-dating every one of the 1,875
shrinks the denominator by at most ~6% — and by less, since some of them
genuinely exited later — against a bounded-coverage gap between roughly 4% and
the 25% threshold. **Nothing currently depends on fixing it.**

### What acting on it would require

An explicit scoped proposition, because each option changes what the corpus
claims to be true:

1. **Regulator-neutral cadence.** Weigh `20-F`/`40-F`/`6-K` and
   `N-CSR`/`NPORT-P`/`N-CEN` as reporting, so a registrant is dormant only when
   *its own* form family goes quiet. Fixes the cause. Consistent with the
   regulator-neutral identity architecture FRC forced. **Reaches 943 of 1,875
   (~50%)** and leaves the domestic group untouched.
2. **Re-date from the registrant's own last periodic filing.** Apply the test
   above as a correction pass: where a periodic report post-dates the exit,
   the exit date is wrong and the later filing is the better one. **Reaches all
   1,875 by construction**, including the undiagnosed domestic group, and uses
   only the EDGAR index already held. Re-dates rather than deletes, so a
   registrant that genuinely exited later keeps an exit.
3. **Trailing-window exclusion.** Refuse to date an exit within N months of the
   corpus end. Cheap, and reaches only the recent tail — 127 of the original
   368 at 12 months. Fixes neither family cause.

**Recommended: 2, with 1 as its definition of "periodic report."** They are not
really alternatives — 1 says which forms count, 2 says what to do when one of
them post-dates an exit — and 1 alone leaves the largest group unfixed. 3 is a
band-aid on the smallest cause and is not worth a methodology change on its own.

### Applied 2026-09-05, on the owner's authorisation

**Option 2 with option 1's form set**, as recommended — and implementing it
showed the two halves were less separable than the proposition assumed.

**Option 2's mechanism already existed.** `assert_exit_not_contradicted` and
the supersession rule in `lifecycle.py` already refuse to date an exit before
the registrant's own last periodic report; they were added after the first real
run, when `INTEL CORP` was dated 1994 while filing to this day. Measured
against the *current* `PERIODIC_FORMS`, the number of contradicted exits is
**zero**. The machinery was sound.

**What was missing was only the form set.** `PERIODIC_FORMS` held the Exchange
Act cadence and the foreign one — `20-F` and `40-F` were already there — and
not the Investment Company Act. A fund reporting punctually on `N-CSR` and
`NPORT-P` was invisible to the rule, so its exit could be dated before filings
it had itself made. **645 of the 21,116 index-seeded exits, 3.05%.**

So the change is one set, widened, and the existing supersession rule does the
rest — which is what "option 2 with option 1's form set" turned out to mean
literally.

**Corrections to the numbers above, both mine.** The 368 was measured with a
file that cannot support the claim, as §7d already records. The 1,875 that
replaced it counted `8-K` and `6-K` as registrant reports; they are current
reports, not periodic ones, and the design excludes `8-K` deliberately. Under
the set the code actually uses, the figure is **645**.

**Three exclusions, each a claim rather than an oversight**, and each pinned by
a test:

* `N-8F` and its notice and order variants are a fund's *application to
  deregister*. One filed after an exit date corroborates that exit. Admitting
  it would let a fund's own death certificate supersede its death — 161
  registrants file one after their exit date, and every one of them would have
  been un-dated by the mistake.
* `NT 10-K` / `NT 10-Q` notify the SEC that a report will be late. A promise to
  report is not a report, and counting one would let a delinquent registrant
  look current indefinitely.
* `N-PX` records how a fund voted proxies, not how the fund stands, and can be
  filed while winding down.

`N-Q` and `N-30D`, the retired predecessors of `NPORT-P` and `N-CSR`, are
included: the corpus starts in 1994, when they were what funds filed.

### The rebuild, and the half of the fix that is still open

Rebuilt against the widened set, `build_denominator` **did not raise** — the
supersession rule absorbed every case, which is the evidence that the existing
machinery was the right machinery.

```
timelines   90,548 -> 96,197   (+5,649, almost all investment companies
                                that previously filed nothing the classifier weighed)
exits       29,180 -> 28,553   (-627)
   no longer dated  627      newly dated  0      re-dated  29
```

The 627 are unambiguously the predicted population — First Trust, Invesco,
BNY Mellon Municipal Income, Delaware Investments, Natixis ETF Trust, Northern
Lights Fund Trust II. Investment companies are **1.9% of dated exits**, so the
denominator is not composed of funds and there is no composition problem.

**But 336 of the 627 filed an `N-8F` — the application to deregister an
investment company — so they genuinely exited, and the corpus now carries no
date for them.** The exclusion of `N-8F` from `PERIODIC_FORMS` was right; what
is missing is the other half, because `classify_form("N-8F")` returns
`IRRELEVANT`. The Investment Company Act's exit forms are recognised **nowhere**.

**The direction of this error is the opposite of the one just fixed, and is the
unsafe one.** A dated exit that becomes undated leaves the denominator, and a
smaller denominator makes coverage read *better* than it is. It is small —
627 of 29,180, about 2% — but its sign is wrong, and this project's first
constraint is that the corpus must not overstate itself.

The evidence splits cleanly along the grain the rest of this document already
uses:

| form | what it is | proposed |
|---|---|---|
| `N-8F ORDR` | the SEC's **order granting** deregistration — 305 of the 336 | confirming; `CONFIRMED_REGISTRATION_TERMINATION` at the order date |
| `N-8F`, `N-8F/A` | the **application**, which can be withdrawn — 31 have no order | candidate; no date |
| `N-8F NTC` | notice that an application was filed | candidate; no date |

This is the Investment Company Act analogue of Form 15, and the
application-versus-order distinction is the same one already drawn between a
filing that says something and a filing that asks for something.

### Both halves, applied 2026-09-05

`N-8F ORDR` — the SEC's **order granting** deregistration — is now
`EXIT_CONFIRMING` at `FORM_DIRECT` strength; the application (`N-8F`,
`N-8F/A`, `N-8F NTC`) is a candidate that dates nothing, because 31 of the 336
have no order on record and an application can be withdrawn. The signal table
had already recognised domestic deregistration (`15-12B`, `15-12G`) and foreign
(`15F-12B`, `15F-12G`); investment companies had nothing, and that pre-existing
gap is what the widened periodic set exposed.

| build | exits | timelines |
|---|---:|---:|
| original | 29,180 | 90,548 |
| periodic set widened only | 28,553 | 96,197 |
| **both halves** | **30,646** | **96,822** |

```
no longer dated   345      newly dated  1,811      re-dated  450
```

**The direction inverted, which is the answer to the concern that opened this
section.** Widening the periodic set alone shrank the denominator by 627 and
made coverage read better than it should. Recognising the exit form more than
reverses it: 1,811 fund closures that carried **no date at all** — evidenced
nowhere, because no form the classifier weighed said so — are now counted. The
denominator is 5% larger than it started, so coverage now reads *worse* and
more honestly. The 345 still undated are funds that went quiet without an
order, which is a cessation candidate and correctly dateless.

By decade the newly dated run 2010s 1,181, 2020s 462, 2000s 168 — the shape
`NPORT-P` and `N-CEN` availability predicts.

**Two consequences to carry forward, neither a defect:**

* **None of the 1,811 has identity in `research-01`.** They were never seeded,
  because they were not exits when the seeding ran. Until they are, they are
  unmatched numerator with matched denominator and they depress coverage. They
  are listed, tradeable securities with tickers, so this is work rather than a
  permanent floor.
* **The denominator now spans three regulators on both sides** — reporting and
  exit — for the first time. Every count that follows is a claim about a wider
  population than any count published before it, and the two are not
  comparable without saying so.

## 7e. MEASURED 2026-09-05 — the gate divides by a different denominator than
§5 defines

**A finding, not a change. Nothing here is applied**, and the direction matters:
this correction would make the corpus look **better**, which is the direction
that deserves the most scrutiny.

### The discrepancy

§5 defines the two bounds over **denominator entries partitioned by identity
state**:

```
matched_coverage  = numerator / RESOLVED entries
bounded_coverage  = numerator / (RESOLVED + AMBIGUOUS + UNRESOLVED entries)
```

`scripts/research01_gate.py` instead passes:

```python
resolved_denominator = len(dated_exits)        # every dated exit
full_denominator     = len(denom.resolutions)  # every REGISTRANT
```

`denom.resolutions` holds one entry per CIK, so it counts every registrant
EDGAR has ever seen — including the roughly two thirds that never exited. The
script's own docstring says the opposite of what it does: *"The denominator
counts registrants that EDGAR shows exiting… Dividing the second by the first
measures how much of the dead population we can actually price."*

### Both readings, computed 2026-09-05

From the rebuilt denominator cache and the corpus, using ticker presence as the
proxy for a RESOLVED identity state:

```
dated exits (denominator entries)        30,646
  identity resolved to a ticker           6,999
  priced — the numerator                  4,568
all registrant resolutions               96,822
```

| | matched_coverage | bounded_coverage | vs the 0.25 threshold |
|---|---:|---:|---|
| as the gate computes it | 4,568 / 30,646 = **14.91%** | 4,568 / 96,822 = **4.72%** | 5.3× short |
| as §5 defines it | 4,568 / 6,999 = **65.27%** | 4,568 / 30,646 = **14.91%** | 1.7× short |

The §5 reading also produces a statement that means something: **of the dead
companies whose ticker has been established, 65% are priced.** The current
reading's 4.72% is the fraction of *all registrants ever* that are both dead and
priced, which no threshold was chosen against.

### Why this is not obviously a bug fix to apply unilaterally

It changes the number the survivorship classification is computed from, in the
flattering direction, and `bounded_coverage >= 0.25` is a written threshold.
Three cautions:

* The figures above use **ticker presence** as the RESOLVED proxy. §4 defines
  four identity states with stricter evidence rules, and `NAME_MATCH` alone can
  never resolve. The true RESOLVED count is at most 6,999 and may be lower,
  which would raise `matched_coverage` further and leave `bounded_coverage`
  unchanged.
* `bounded_coverage` is the **pessimistic** bound by design — it assumes no
  unresolved entry would have matched. Under the §5 reading it stays
  pessimistic; under the current reading it is pessimistic about a different
  population.
* Whichever reading stands, the corpus is **still short of the threshold** and
  the standing rule on `research-01` is unaffected today.

### What closes the remaining gap, under the §5 reading

Reaching `bounded_coverage` 0.25 needs 7,662 priced dead registrants against
30,646 entries. The corpus holds 4,568, so roughly **3,100 more**. At the
observed 65% price-per-resolved rate that is about **4,800 further ticker
resolutions** — against 23,647 dated exits that currently have no ticker at
all. The resolver's recent hit rate is 12–15%, so this is a real but not
obviously reachable target from filing text alone.

| step | output | cost |
|---|---|---|
| 1 | ingest full-index 1994 Q3 → present; row per filing | free |
| 2 | birth/death events per CIK; cessation rule with the 8-quarter window | free |
| 3 | termination counts by year × evidence strength — **the first publishable number** | free |
| 4 | cohort survival curves from EDGAR alone (§6) | free |
| 5 | identity mapping pass; four states; `MANUAL_VERIFIED` for the 30 controls | free + human time |
| 6 | *(after vendor access)* match the roster; publish both coverage bounds | — |

**Steps 1–5 need no vendor, no purchase and no permission**, and step 4 produces
a genuinely useful artefact — the real survival curve of US registrants,
1994–2026 — whether or not any vendor is ever bought.

This is Milestone 0a in `PHASE_06_IMPROVEMENT_PLAN.md` and it is the work that
should start first, because it is the instrument every later judgement depends
on.
