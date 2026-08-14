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

### 2.2 8-K item numbering changed in 2004

Modern item numbers (1.03 bankruptcy, 2.01 completion of acquisition, 3.01
delisting notice, 5.06 shell transaction) date from the **August 2004** 8-K
overhaul. Pre-2004 8-Ks used a different, coarser scheme (bankruptcy under Item
3, acquisition/disposition of assets under Item 2). Any parser must branch on
filing date, and the pre-2004 branch is coarser and needs text confirmation.

### 2.3 Filing cessation — the pre-2004 workhorse

Given §2.1 and §2.2, the robust pre-2004 signal is not a form at all:

> **A CIK that filed 10-K and 10-Q regularly, whose last periodic filing is in
> period *P*, and which never files again, stopped being a reporting company at
> approximately *P*.**

This is derivable entirely from the free quarterly full-index, needs no form
parsing, is exchange-agnostic, and covers 1994 Q3 → present uniformly. It is the
only signal that behaves the same across the whole span.

**Its ambiguities, stated up front, because they are the reason it is an estimate
and not a census:**

| looks like cessation | actually is |
|---|---|
| acquired — the target stops filing | a real disappearance ✓ |
| went private (Form 15 usually accompanies) | a real disappearance ✓ |
| delisted and deregistered | a real disappearance ✓ |
| became a wholly-owned subsidiary still filing debt covenants | *not* a disappearance ✗ |
| fell delinquent, then resumed 18 months later | *not* a disappearance ✗ |
| CIK changed after a reorganisation | *not* a disappearance — double-counts ✗ |

**Mitigations:** require a quiet period of **≥ 8 quarters** before declaring
cessation; cross-check against Form 15 where present; treat a resumption as
retroactively cancelling the cessation; and report cessation counts with a stated
false-positive band rather than as a hard number.

---

## 3. Construction

```
1  ingest        quarterly full-index 1994 Q3 → present
                 (CIK, company name, form type, filed date, accession path)
2  birth         per CIK: first 8-A12B / 8-A12G, else first periodic filing
3  death         per CIK, the earliest of:
                   Form 25 / 25-NSE          (2005+, strong)
                   Form 15 family            (whole span, strong)
                   8-K bankruptcy item       (branch on 2004)
                   8-K completion-of-merger  (branch on 2004)
                   filing cessation + 8q     (whole span, weak but uniform)
4  classify      terminal_reason ∈ {delisted, deregistered, bankrupt, acquired,
                                    merged, went_private, ceased_reporting,
                                    unknown}
                 evidence_strength ∈ {form_direct, form_inferred, cessation_only}
5  cohort        listing year, termination year, lifespan, exchange (where the
                 filing names one), security class
6  identity      attempt CIK → ticker; record the mapping STATE (§4), never a guess
7  publish       counts by year × cohort × exchange × evidence_strength
```

**Every count is published with its evidence strength attached.** A 2001
termination count of *N* where 80% is `cessation_only` is a different claim from
one where 80% is `form_direct`, and collapsing them into one number is the
mistake this design exists to avoid.

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
- **`UNRESOLVED` is not a failure of the denominator — it is a measurement.** A
  denominator with 30% unresolved identity is still a valid denominator; it just
  reports two numbers instead of one (§5).
- Unresolved entries are **retained permanently**, not dropped. A later mapping
  upgrades the state; nothing is ever re-derived from scratch and silently
  changed.

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
