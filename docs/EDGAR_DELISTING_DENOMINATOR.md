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
2026-08-29**, 129 quarterly index files, 1994 Q3 – 2026 Q3, pinned
`--as-of 2026-08-29`. Everything below is measured, not estimated.

| measurement | value |
|---|---|
| index files scanned | 129 |
| rows read, and independently re-read | 27,084,668 |
| FORM / CIK / DATE / PATH / ACCESSION mismatches | **0** |
| registrants | 90,548 |
| confirmed exits carrying a date | 40,920 |
| undated exits (cessation + unresolved) | 49,628 |
| control mappings attached to a registrant | 33 of 33 handed over |

The parser-integrity gate **PASSED** on all 27,084,668 rows.

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

**No third explanation is offered here.** The curve is built from a dating rule
now known to be wrong on 30.7% of its inputs, and explaining a number before
correcting the defect underneath it is how a wrong number acquires a defender.
Fix, re-run, then look.

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

### The 2005–2007 peak was largely an artefact, and §7bb's third prediction is now due

§7bb recorded that the per-year peak was 2005–2007 rather than the expected
dot-com and 2008 bulges, and **deliberately declined to explain it** until the
dating rule was corrected. That was the right call:

| year | before | after | change |
|---|---|---|---|
| 2005 | 2,827 | 1,393 | −51% |
| 2006 | 3,012 | 1,205 | −60% |
| 2007 | 3,113 | 1,417 | −54% |
| 2012 | 1,679 | 1,518 | −10% |
| 2026 | 447 | 429 | −4% |

**More than half of the 2005–2007 peak was registrants that never exited.** The
shape is consistent with `ELECTRONIC_FORM_25_FROM` (2005-04-24, §2.2): once
Form 25 became an electronic filing, surviving registrants began filing them to
remove *individual classes* — a warrant, a preferred series — and the old rule
read every one of those as the registrant's death. That is a hypothesis with a
mechanism, not a finding, and it is recorded as such. **The curve is now built
from a dating rule with no known contradictions and may be looked at properly;
that is separate work and is not done here.**

### A separate discrepancy, found while re-running and NOT fixed here

§7bb records the run as "129 quarterly index files, 1994 Q3 – 2026 Q3". Measured
on 2026-08-29: **129 `form.idx` files exist on disk, and the command reads 128.**
`tradeit edgar denominator` defaults to `--end 2026Q2`, so `2026/QTR3/form.idx`
— 23 MB of real filings, present since 2026-08-14 — is silently excluded. The
before-run reproduced §7bb's registrant and exit counts exactly, which confirms
the original run also read 128; the section's file count and end quarter are
therefore both off by one quarter.

**Left alone deliberately.** Changing the range changes the denominator's
coverage, and §7b limitation 4 is explicit that quarter coverage is a property
of the measurement rather than a detail — this is that same concern inverted, a
*present* quarter silently excluded by a stale default rather than a missing one
read as a zero. It wants its own scoped decision: move the default, require the
flag, or make a stale default an error. It is not folded into a dating fix, and
the numbers in this section are all `--end 2026Q2` on both sides so the
comparison holds regardless of which way that decision goes.

### What this does not fix

Supersession is a *dating* rule and touches nothing else. Row counts, the
registrant total, the parser-integrity gate and the identity mapping are
untouched by construction. It also does not make the per-year curve explicable:
§7bb recorded two falsified predictions and declined to offer a third
explanation until the dating rule was corrected. It is corrected now; the curve
still has to be looked at, and that is a separate piece of work with its own
evidence.

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

## 8. Build order

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
