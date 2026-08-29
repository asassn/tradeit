# Data retention rights — the register

**No dataset may enter `research-01` until its retention rights are determined
and recorded here.** This is a contract condition at the same level as the
three-date rule, because it has the same failure mode: violate it and the corpus
must be *destroyed* rather than corrected.

The rule exists because TradeIt's whole economic model is a **one-time historical
backfill retained permanently**, plus a forward archive TradeIt accumulates
itself. Both assume the right to keep what was collected after access ends. Two
vendors were eliminated for failing exactly that before the register existed; a third was eliminated by asking.

---

## 1. Classification vocabulary

| classification | meaning | may enter `research-01`? |
|---|---|---|
| `PERMANENT RETENTION ALLOWED` | delivered data, and datasets derived from it, may be kept and used internally indefinitely after termination | **yes** |
| `RETENTION REQUIRES ACTIVE SUBSCRIPTION` | data may be stored while paying, but use ends with access | **no** — for the permanent corpus |
| `DELETION REQUIRED AFTER TERMINATION` | copies (and often derived datasets) must be deleted within a stated window | **no** |
| `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | terms silent, ambiguous, or unreachable | **no** — treated as prohibited until answered |

**`UNCLEAR` is treated as prohibited.** An unresolved retention right discovered
*after* the data is embedded in a research corpus is a far worse problem than one
discovered now.

### 1.1 An adverse answer is actionable at a lower bar than a favourable one

**The two errors are not the same size, so they do not need the same evidence.**

* Believing "we must delete" when in fact retention was permitted costs a vendor
  that could have been used. Recoverable, and cheap to reverse later by asking
  again.
* Believing "we may keep" when in fact deletion was required means the corpus
  must be destroyed, along with everything derived from it. That is the failure
  this whole register exists to prevent.

So a source good enough to **disqualify** a vendor is not automatically good
enough to **qualify** one. A vendor-channel answer that is adverse may be acted
on immediately; the same channel saying "yes, keep it permanently" needs the
clause, because a permanent corpus would then be resting on it. Applied first to
Twelve Data, whose answer was composed by the vendor's AI agent rather than by a
named person — sufficient to exclude, and it would not have been sufficient to
admit.

---

## 2. The register

| source | classification | basis | confidence |
|---|---|---|---|
| **SEC EDGAR** | `PERMANENT RETENTION ALLOWED` | US government work, public domain. No licence, no account, no termination event to trigger deletion | **VERIFIED** — no licence exists to read |
| **Sharadar** (Nasdaq Data Link) | `DELETION REQUIRED AFTER TERMINATION` | Personal Use License: on termination, discontinue use, delete all copies of Services Data within 30 days, **and delete datasets derived from Services Data within 30 days** | **USER-VERIFIED** (primary source, read by the project owner) |
| **EODHD** | `DELETION REQUIRED AFTER TERMINATION` | terms require deletion of stored provider data within one month after termination | **USER-VERIFIED** |
| **Kibot** | `PERMANENT RETENTION ALLOWED` *(claimed)* | licence states delivered data may be kept permanently; cancellation does not require deletion | **USER-VERIFIED** as licence text. **The scope of "delivered data" — raw files vs normalised rows vs derived bars vs a frozen research corpus — is not established.** See §3 Q23–Q26 |
| **Twelve Data** | `DELETION REQUIRED AFTER TERMINATION` | **answered, with a clause.** Terms of Use **§16 — Data retention and deletion**, sub-clause **16.2 Data Deletion**, quoted by the vendor as: *"Upon termination or expiration: All Data must be deleted within 30 days. Certification of deletion may be requested. Audit trail data may be retained for compliance."* Unchanged by data category, free tier or licence tier | **VERIFIED for the clause; the derived-data extension is INTERPRETATION, not clause text.** §16.2 as quoted says "all Data" and does **not** name derived datasets or research corpora — the vendor's agent reached them by reasoning from "all Data", and on the second asking hedged it to "consistent with the wording of the clause". Both replies were composed by "Dooz, Twelve Data's AI Agent"; **no human at the vendor has confirmed anything.** See §2.0 |
| **FMP** | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | **not established.** `site.financialmodelingprep.com` and `financialmodelingprep.com` both return `EGRESS_BLOCKED`; re-measured, still blocked | **UNVERIFIED** |
| **Tiingo** | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | **not established.** Working acquisition adapter (`acquisition/tiingo.py`, not a stub); `app.tiingo.com` returns `EGRESS_BLOCKED`, terms never examined | **UNVERIFIED** |

**The three vendors with a functional adapter were the three unanswered rows.**
That was not a coincidence and it is why milestone 0d exists: every vendor this
system can currently collect from had unread retention terms. One is now
answered, adversely. `EGRESS_BLOCKED` remains a statement about a session's
network, never about a vendor — a blocked fetch establishes that terms were *not
read*, and nothing whatever about what they say.

### 2.0 Twelve Data is now excluded, and this is the answer §2.1 predicted

§2.1 below was written before the question was asked and states the conditional:
*"if Twelve Data's terms require deletion on termination, the forward
accumulation model does not hold for prices."* **They do.** The conditional is
kept exactly as written rather than rewritten into the past tense, because a
prediction that was made before the evidence arrived is worth more on the record
than one edited afterwards to match it.

Three consequences follow immediately:

1. **Twelve Data cannot be the price spine for any permanent corpus**, on the
   same ground as Sharadar and EODHD and independent of data quality. This holds
   under *either* reading of §16.2 below, because the raw prices are "Data" on
   any construction of the word.
2. **`full-01` carries a deletion trigger — and its exact reach is not settled.**
   Its snapshot is `twelve_data-daily-e3ddc03209bb25b4`
   ([`CORPUS_REGISTRY.md`](CORPUS_REGISTRY.md)). Retention is unaffected while
   the subscription is live; on termination the 30-day clock starts. Whether it
   stops at the bars or reaches the 272k patterns and 341k breakout events
   derived from them **depends on how the Terms define "Data"**, which nobody
   has read — see §2.0a. **Plan for the broad reading and do not terminate the
   subscription without first deciding what happens to `full-01`.** "Frozen and
   permanent" was true of this project's intentions and is not true of its
   rights.
3. **The forward survivorship archive needs a different price source.** The
   accumulation model in `FORWARD_SURVIVORSHIP_SYSTEM.md` §8 assumes today's
   bars become permanent history. Under these terms Twelve Data's cannot.

### 2.0a What the clause says, and what was inferred from it

The follow-up asked for two things. **It got one of them.**

**Answered — the clause.** Terms of Use §16, *Data retention and deletion*,
sub-clause **16.2 Data Deletion**, quoted by the vendor:

> Upon termination or expiration: All Data must be deleted within 30 days.
> Certification of deletion may be requested.
> Audit trail data may be retained for compliance.

Two operational details worth having: **deletion may have to be certified**, so
the obligation is not merely nominal, and the only carve-out is audit-trail data
for compliance, which is no use for research.

**Not answered — human confirmation.** Both replies carry the footer *"This
answer was composed by Dooz, Twelve Data's AI Agent."* No person at Twelve Data
has confirmed any of it.

**And the second reply is weaker than the first on the point that matters.** The
first said flatly that the deletion requirement "applies to all forms of the
data, including … derived datasets … research corpora or analytical results".
The second, asked to confirm exactly that, said instead that because the
requirement applies to "all Data", the reading that it includes derived datasets
and research corpora **"is consistent with the wording of the clause"**. That is
an interpretation offered as reasonable, not a statement of what the clause
requires — and **§16.2 as quoted does not mention derived data at all.**

The contrast with Sharadar is the whole point. Sharadar's licence *names* the
category: "delete datasets derived from Services Data within 30 days." Twelve
Data's §16.2 says "all Data", and everything past that turns on **how the Terms
define "Data"** — a definition nobody in this record has read. If "Data" is
defined as what the API delivers, the narrow reading is arguable. If it is
defined to include works derived from it, the broad reading is the clause.

**The remaining question is one definition, and the operator can settle it
better than another email can.** Do not send a third enquiry: open
`twelvedata.com/terms`, read §16 and the definition of "Data" in the definitions
section, and record it here as `USER-VERIFIED` — primary text read by a person,
which outranks anything an agent relays. Two minutes, and it decides whether
`full-01` survives a cancellation.

### 2.1 Why Twelve Data and FMP now matter as much as any historical vendor

They were never checked, because they were thought of as *operating* feeds rather
than archive sources. That distinction does not survive contact with the forward
survivorship design:

> `FORWARD_SURVIVORSHIP_SYSTEM.md` §8 states that after the one-time backfill,
> the recurring need is forward prices from Twelve Data, forward filings from
> EDGAR, and corporate-action latency from FMP — and that **the registry
> accumulates permanently**, so the survivorship-safe universe of 2035 is the
> 1998 archive plus every daily observation since.

Every one of those daily observations is a permanently retained vendor fact. **If
Twelve Data's terms require deletion on termination, the forward accumulation
model does not hold for prices**, and the corpus of 2035 would be encumbered by a
subscription decision made in 2026.

**`full-01` is not at risk today**: it is frozen, machinery-validation only, and
never cited for economic claims. But it *was* built from Twelve Data prices, and
that is the shape of the exposure. — *That exposure is now real rather than
hypothetical; see §2.0. It is still not a present breach, because the obligation
attaches on termination and the subscription is live.*

**Do not assume current API access implies archival rights.** Access and
retention are different grants, and it is common for the first to be generous and
the second to be silent.

---

## 3. The written questions — send verbatim, keep the replies

Ask each vendor. Silence, deflection or "our terms speak for themselves" without
a clause citation is recorded as `UNCLEAR`, which is treated as prohibited.

### 3.1 To Twelve Data, to FMP and to Tiingo (identical text)

> We use your API to collect market data for an internal, non-public research and
> development project. We do not redistribute, resell, publish or expose your data
> to third parties, and we do not display it to any external user.
>
> **1. Retention after termination.** If our subscription or account later
> terminates — by cancellation, non-renewal, or a change in your plans — may we
> **permanently retain and continue to use internally** the data we collected
> while our access was valid? Please cite the governing clause.
>
> **2. Scope of retention.** Specifically, may we retain indefinitely after
> termination:
> (a) raw API responses as delivered;
> (b) normalised rows in our internal database;
> (c) bars and series *derived* from your data (resampled, adjusted, or
>     otherwise transformed);
> (d) a security master and identifier mappings built partly from your reference
>     data;
> (e) frozen internal research corpora and the analytical results computed from
>     them?
>
> **3. Data categories.** Does the answer differ for historical OHLCV, corporate
> actions, splits, dividends, or reference/security data? If any category is
> treated differently, please say which and how.
>
> **4. Free tier.** If any of the above was collected under a free tier or trial
> rather than a paid plan, does that change the answer?
>
> **5. Licensing tier.** Our use is internal research and development for a
> non-public software product. If TradeIt later became a commercial or
> professional product, which licence tier would apply, and would it change the
> retention answer for data already collected?

### 3.2 To Kibot — retention questions, numbered as in the probe

The Kibot licence already states that delivered data may be kept permanently.
These questions establish **how far "delivered data" reaches**, which is the part
that eliminated Sharadar:

> **23.** Please confirm in writing that data already delivered to us may be
> retained and used indefinitely after we cancel our subscription.
>
> **24.** Please confirm that this retention right extends to each of:
> (a) the raw downloaded files exactly as delivered;
> (b) normalised rows loaded into our internal database;
> (c) bars *derived* from your data — resampled to other timeframes, adjusted,
>     or otherwise transformed;
> (d) a security-master and identifier mapping built partly from your roster
>     files;
> (e) frozen internal research corpora and analytical results derived from your
>     data.
>
> **25.** Please confirm that continued **private, internal analysis** of retained
> data after cancellation is permitted, with no redistribution, resale,
> publication or external display.
>
> **26.** Our present use is internal research and development for a non-public
> software product. If TradeIt later became a commercial or professional product,
> would a different licence be required, and would that change the status of data
> already delivered and retained under the current licence?

**Item 24(c) and 24(e) are the decisive ones.** Sharadar's licence permits
retaining nothing *and* requires deleting derived datasets; a licence that
permits keeping the files but is silent on derived data leaves `research-01`
itself in an undetermined state.

---

## 4. Recording the answers

Each reply is filed with: the date, the person and role who answered, the clause
cited (or its absence), and a classification from §1. A reply that answers the
retention question but not the *derived-data* question is classified `UNCLEAR`
until the second half is answered — partial answers do not upgrade a
classification.

**Nothing here is a legal opinion.** It is a record of what vendors have stated in
writing, kept so the basis of a decision is auditable later.

---

## 5. Consequences already in force

1. Sharadar, EODHD **and now Twelve Data** are **excluded from `research-01`** on
   licence grounds alone, independent of data quality. Any of them may be
   reconsidered only under a different written commercial or custom licence that
   explicitly grants post-termination retention.
2. **Four of the six named vendors are out, and two are unanswered.** Sharadar,
   EODHD and Twelve Data on licence; Kibot on price. If FMP and Tiingo answer the
   way Twelve Data did, **no price or corporate-action vendor satisfies the
   permanent-retention rule at any price this project has looked at** — which
   would be a finding about the market rather than about the search, and would
   force a choice between relaxing the rule, paying for an enterprise licence
   that grants retention explicitly, or building the archive only from public
   sources. Name that now rather than discovering it after the two replies.
3. **There is no live candidate for the price spine.** Kibot was the only one,
   on licence grounds, and it is now eliminated on price — the archive is
   $990–$2,400, not the ~$14/month the plan had assumed
   ([`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) §1.3). Its licence
   advantage was real and is preserved as the model for interrogating a
   replacement; its data was never proven (`KIBOT_DATA_PROBE.md`) and now never
   will be here. **So every remaining vendor is either excluded or unread**, and
   that is the state milestone 0d exists to change.
4. **FMP and Tiingo retention must be established before either becomes part of
   a permanent corpus** — Twelve Data's is now established and disqualifying.
   This blocks the forward survivorship archive, not the current operating use.
   Tiingo belongs in this sentence because its adapter works, which is the only
   property that matters here — an adapter that can collect can encumber.
5. **`full-01` may continue to be used and cited exactly as before.** Nothing
   about its gate result, its regression ratios or its machinery-validation role
   changes; what changed is that it now has a termination-triggered deletion
   obligation attached to it (§2.0). Treat it as a corpus with an expiry
   condition rather than a permanent one, and settle its disposition *before*
   any decision to end the Twelve Data subscription rather than after.
6. Every `source` value in `ohlcv_bars`, `fundamental_facts` and
   `corporate_actions` must have a row in §2 before its first fact is written.
   (This rule named `price_facts` and `corporate_action_facts` until the schema
   was fully inventoried; neither table exists, so as written the rule bound
   nothing. See [`DATA_MODEL.md`](DATA_MODEL.md) Domain 2.)
7. **Free work is unaffected.** The EDGAR denominator, the control-universe
   verification and the whole filing spine are public domain and can proceed with
   no retention question at all.
