# Vendor questions — ready to send

Four messages, complete and sendable as written. **No purchase, no account, no
commitment is implied by any of them.** Each asks for clause citations rather
than reassurance, because the whole point is a record that can be checked later.

## 0. Sending them — this is an operator action and cannot be delegated

**These have to be sent by a person, from the account holder's own address.**
The assistant has no mail channel — no email tool and no connector — so it
cannot send them, and it should not: a licence enquiry is a communication in the
account holder's name that a vendor will answer, quote back, and treat as the
customer's position. It also cannot read the vendors' published terms from its
own environment; `twelvedata.com`, `support.twelvedata.com`,
`site.financialmodelingprep.com`, `financialmodelingprep.com` and
`app.tiingo.com` all return `EGRESS_BLOCKED`.

| vendor | route | status of this address |
|---|---|---|
| Twelve Data | `support@twelvedata.com`, or the licensing address `api@twelvedata.com` for the retention/attribution question specifically | **UNCONFIRMED** — from a web search, not from the vendor's own page, which is unreachable here. Confirm at `twelvedata.com/contact` before sending |
| FMP | `info@financialmodelingprep.com` | **UNCONFIRMED** — same basis. Confirm at `site.financialmodelingprep.com/contact` |
| Tiingo | the support form linked from `tiingo.com/kb/contact-support/` | **UNCONFIRMED** — no public support address was established; the knowledge base points at a form |

**Do not treat these addresses as verified.** They came from search results
describing the vendors' pages rather than from the pages themselves, which is
the difference between a lead and a source. Open each vendor's own contact page
and use what it says. If a vendor routes support through an in-app ticket form
rather than mail, paste the message body into the form — the text below is
written to work either way, and none of it depends on being an email.

**Sign it yourself.** The bodies deliberately carry no name, company or
signature, because the assistant must not sign a communication as anyone. Add
your own sign-off before sending.

One second-hand signal, recorded as a reason to ask rather than as an answer: a
search summary of FMP's terms describes the licence as granting access "during
the Subscription Period", which is the shape of a right that ends with the
subscription. **That page was not read**, the summary is a secondary source, and
FMP's classification stays `UNCLEAR` exactly as before — but it is the wording
question 1 below is aimed at, so put FMP first if you send them one at a time.

**File every reply** with the date, the responder's name and role, the clause
cited (or its absence), and a grade. Update
[`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §2 and the answer register
in [`KIBOT_DATA_PROBE.md`](KIBOT_DATA_PROBE.md) §Q.

| grade | meaning |
|---|---|
| `VERIFIED` | answered in writing, specifically, with a clause or a direct statement |
| `CORROBORATED` | implied by documentation the vendor pointed at, but not directly answered |
| `UNVERIFIED` | unanswered, evasive, or restated marketing copy |

**Marketing language is not an answer.** A reply that points at a product page
without addressing the question leaves the item `UNVERIFIED`, and for retention
questions `UNVERIFIED` is treated as prohibited.

---

## 1. Kibot — 26 questions

> **Status: NOT SENT, and now MOOT for this vendor.** Kibot was first skipped in
> favour of buying and measuring; that purchase is off, because the archive costs
> $990–$2,400 rather than the ~$14/month assumed
> ([`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) §1.3).
>
> **Keep them as the model for interrogating a replacement.** Question 1 is the
> one that decides any price vendor and was never answered here either: whether
> "delisted" securities are actually included, or only currently active ones.
> Every question below is written to demand a clause or a specific statement
> rather than reassurance, which is the property worth reusing. Sections 2 and 3
> are unaffected and remain outstanding.

**Send to:** Kibot sales/support.
**Subject:** Pre-sales questions — historical US equity EOD data, delisted coverage and retention

> Hello,
>
> We are evaluating Kibot for a one-time historical backfill of US equity daily
> data for an internal, non-public research project. We do not redistribute,
> resell, publish or display data to third parties.
>
> Before subscribing we need clear answers to the following. Where a question is
> answered by a specific clause of your licence or terms, a pointer to that
> clause would be very helpful.
>
> **Product access**
>
> 1. Does your inexpensive EOD subscription provide access to historical **daily**
>    data for **delisted** US stocks, or only for currently active securities?
> 2. If delisted history is available, how is it accessed — bulk download, API,
>    individual-symbol download, or a separate product or package?
> 3. Is the complete active **and** delisted US equity universe downloadable
>    during a standard monthly subscription?
> 4. Are there throughput, bandwidth or daily download limits that would make
>    acquiring a full historical archive impossible within one billing month?
> 5. What is the minimum subscription or product combination necessary to obtain
>    that archive, and what does it cost in total?
>
> **Historical depth**
>
> 6. Does your US stock coverage reliably include data from **1998-01-01** onward?
> 7. Are **small and short-lived** securities from the 1998–2002 period
>    represented — companies that listed and failed within a few years — or is
>    the historical universe primarily major surviving names?
> 8. Are bankrupt and delisted securities retained in your historical archive
>    indefinitely, or removed once they stop trading?
>
> **Identity and lifecycle**
>
> 9. Do you provide any permanent identifier for a security that is independent
>    of its ticker — for example CIK, CUSIP, FIGI, or a vendor-internal permanent
>    ID?
> 10. How are **ticker changes** represented? Does one file span the change, are
>     there two files, or is history available only under the current ticker?
> 11. How is **ticker reuse** handled — when a later, unrelated company receives a
>     ticker previously used by a delisted company? Are the two histories kept
>     separate?
> 12. Are listing and delisting **dates** supplied per security?
> 13. Are delisting **reasons** supplied — bankruptcy, acquisition, going private,
>     exchange rule?
> 14. Are mergers, acquisitions and security replacements represented in any form?
>
> **Corporate actions and price semantics**
>
> 15. Are raw / **unadjusted** OHLCV bars available?
> 16. Are **split-adjusted** series available?
> 17. Are **dividend-adjusted / fully adjusted** series available?
> 18. Are **split events** available separately, as a corporate-action file or
>     feed?
> 19. Are **dividends** available separately?
> 20. How are **reverse splits** represented?
> 21. Are **spin-offs** handled, and if so how are they reflected in the adjusted
>     series?
> 22. Are historical **volumes** adjusted under any of the adjusted products, or
>     do volumes remain as printed?
>
> **Licensing and retention**
>
> 23. Please confirm in writing that data already delivered to us may be retained
>     and used indefinitely after we cancel our subscription.
> 24. Please confirm that this retention right extends to each of:
>     (a) the raw downloaded files exactly as delivered;
>     (b) normalised rows loaded into our internal database;
>     (c) bars **derived** from your data — resampled to other timeframes,
>         adjusted, or otherwise transformed;
>     (d) a security master and identifier mappings built partly from your roster
>         files;
>     (e) frozen internal research corpora and analytical results derived from
>         your data.
> 25. Please confirm that continued **private, internal analysis** of retained
>     data after cancellation is permitted, with no redistribution, resale,
>     publication or external display.
> 26. Our present use is internal research and development for a non-public
>     software product. If it later became a commercial or professional product,
>     would a different licence be required, and would that change the status of
>     data already delivered and retained under the current licence?
>
> Thank you.

**Why 24(c) and 24(e) are the decisive ones.** Another vendor's licence permits
retaining nothing *and* requires deleting datasets derived from its data within
30 days of termination — which would oblige us to delete our research corpus, not
merely the files. A licence that permits keeping the files while staying silent
on derived data leaves the corpus in an undetermined state, and undetermined is
treated as prohibited.

---

## 2. Twelve Data — retention

**Send to:** Twelve Data support.
**Subject:** Data retention rights after subscription termination

> Hello,
>
> We use your API to collect market data for an internal, non-public research and
> development project. We do not redistribute, resell, publish or expose your
> data to third parties, and we do not display it to any external user.
>
> We need to understand our retention rights before we build a permanent archive.
> Where possible, please cite the governing clause.
>
> **1. Retention after termination.** If our subscription or account later
> terminates — by cancellation, non-renewal, or a change to your plans — may we
> **permanently retain and continue to use internally** the data we collected
> while our access was valid?
>
> **2. Scope of retention.** Specifically, may we retain indefinitely after
> termination:
> (a) raw API responses as delivered;
> (b) normalised rows in our internal database;
> (c) bars and series **derived** from your data (resampled, adjusted, or
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
> **4. Free tier.** If any data was collected under a free tier or trial rather
> than a paid plan, does that change the answer?
>
> **5. Licensing tier.** Our use is internal research and development for a
> non-public software product. If it later became a commercial or professional
> product, which licence tier would apply, and would it change the retention
> answer for data already collected?
>
> Thank you.

**Why this matters more than it looks.** The forward survivorship design assumes
today's bars become permanent history — the corpus of 2035 is the historical
archive plus every daily observation since. Every one of those observations is a
permanently retained vendor fact. If retention is not granted, the accumulation
model does not hold for prices and the design has to change.

`full-01` was built from Twelve Data prices. It is frozen, machinery-validation
only, and never cited for economic claims, so nothing published is at risk today
— but that is the shape of the exposure, and it is why this question is no longer
optional.

---

## 3. Financial Modeling Prep — retention

**Send to:** FMP support.
**Subject:** Data retention rights after subscription termination

> Hello,
>
> We use your API for corporate-action and symbol-reference data in an internal,
> non-public research and development project. We do not redistribute, resell,
> publish or expose your data to third parties.
>
> We need to understand our retention rights before we build a permanent archive.
> Where possible, please cite the governing clause.
>
> **1. Retention after termination.** If our subscription or account later
> terminates, may we **permanently retain and continue to use internally** the
> data we collected while our access was valid?
>
> **2. Scope of retention.** Specifically, may we retain indefinitely after
> termination:
> (a) raw API responses as delivered;
> (b) normalised rows in our internal database;
> (c) records **derived** from your data;
> (d) a security master and identifier mappings built partly from your reference
>     data;
> (e) frozen internal research corpora and analytical results computed from them?
>
> **3. Data categories.** Does the answer differ for corporate actions, splits,
> dividends, symbol-change notifications, or reference/security data?
>
> **4. Free tier.** If any data was collected under a free tier, does that change
> the answer?
>
> **5. Licensing tier.** Our use is internal research and development for a
> non-public software product. If it later became commercial, which licence tier
> would apply, and would it change the retention answer for data already
> collected?
>
> Thank you.

---

## 4. Tiingo — retention

**Send to:** Tiingo support (see §0 — this may be a form rather than an address).
**Subject:** Data retention rights after subscription termination

> Hello,
>
> We use your API to collect market data for an internal, non-public research and
> development project. We do not redistribute, resell, publish or expose your
> data to third parties, and we do not display it to any external user.
>
> We need to understand our retention rights before we build a permanent archive.
> Where possible, please cite the governing clause.
>
> **1. Retention after termination.** If our subscription or account later
> terminates — by cancellation, non-renewal, or a change to your plans — may we
> **permanently retain and continue to use internally** the data we collected
> while our access was valid?
>
> **2. Scope of retention.** Specifically, may we retain indefinitely after
> termination:
> (a) raw API responses as delivered;
> (b) normalised rows in our internal database;
> (c) bars and series **derived** from your data (resampled, adjusted, or
>     otherwise transformed);
> (d) a security master and identifier mappings built partly from your reference
>     data;
> (e) frozen internal research corpora and the analytical results computed from
>     them?
>
> **3. Data categories.** Does the answer differ for historical end-of-day
> prices, the `splitFactor` and `divCash` corporate-action fields delivered on
> those rows, or reference/security data? If any category is treated
> differently, please say which and how.
>
> **4. Free tier.** If any data was collected under a free tier or trial rather
> than a paid plan, does that change the answer?
>
> **5. Licensing tier.** Our use is internal research and development for a
> non-public software product. If it later became a commercial or professional
> product, which licence tier would apply, and would it change the retention
> answer for data already collected?
>
> Thank you.

**Why Tiingo is on this list at all.** It was not in milestone 0d's original
wording, and that was an omission rather than a decision: its acquisition
adapter is functional — `acquisition/tiingo.py`, not a stub like EODHD — so it
can write vendor facts into a permanent corpus today under terms nobody has
read. Question 3 is narrowed to the fields this adapter actually takes: Tiingo
delivers splits and dividends inline on the daily price rows as `splitFactor`
and `divCash`, and those are corporate-action facts whatever table they arrive
in.

---

## 5. Recording the replies

**A reply is evidence only once it is filed.** Copy this row into
[`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §2 as each answer lands,
and keep the original message — a summary of a licence answer is not a licence
answer.

| field | what goes in it |
|---|---|
| date | the day the reply arrived, operator-local |
| responder | name **and role**; "support" alone does not establish who committed the vendor |
| channel | email, ticket, or chat transcript — and where the original is kept |
| clause cited | the clause reference, or **explicitly** that none was given |
| answer to Q1 | retention after termination: yes / no / not addressed |
| answer to Q2(c) and Q2(e) | derived data and research corpora, **separately** |
| grade | `VERIFIED`, `CORROBORATED` or `UNVERIFIED` per the table above |
| classification | one of the four in `DATA_RETENTION_RIGHTS.md` §1 |

### 5.1 Filed replies

**Twelve Data — answered, adverse.** All three enquiries were sent on
2026-08-29; Twelve Data replied within minutes.

| field | value |
|---|---|
| date | 2026-08-29, 11:36 local — *taken from the reply as displayed; confirm the date if it matters to a later audit* |
| responder | **"Dooz, Twelve Data's AI Agent"**, disclosed in the message footer. Not a named person, and no human at the vendor has confirmed it |
| channel | email reply to the §2 enquiry; original in the account holder's mailbox |
| clause cited | none in the first reply — a bare footnote marker `[1]`. **Supplied on the follow-up**: Terms of Use §16, sub-clause **16.2 Data Deletion** (see §5.3) |
| Q1 — retention after termination | **No.** "all access rights end immediately and all Data must be deleted within 30 days. Permanent internal retention after termination is not permitted" |
| Q2(c) — derived data | **Covered by the deletion requirement** — "derived datasets (bars, series, transformations)" listed explicitly |
| Q2(e) — research corpora | **Covered** — "research corpora or analytical results built from the data" listed explicitly |
| Q3 — categories | No distinction between OHLCV, corporate actions or reference data |
| Q4 — free tier | Same rules on free and paid |
| Q5 — licence tier | Retention rule does not change with tier or future commercialisation |
| grade | `VERIFIED` **as an answer** — in writing, specific, question by question. The responder field carries the caveat that it was machine-composed |
| classification | `DELETION REQUIRED AFTER TERMINATION` |

**Why an AI-composed answer was accepted here.** Because it is adverse, and
[`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §1.1 sets a lower bar for
disqualifying a vendor than for qualifying one. The identical message saying
"yes, retain permanently" would **not** have been enough to admit Twelve Data to
a permanent corpus, because a corpus would then be resting on a chatbot's
reading of a licence. Worth keeping straight: this is not a rule that AI answers
count, it is a rule about which direction of error is recoverable.

**The follow-up in §5.2 was sent, and its outcome is filed at §5.3.** It got the
clause and did not get the human confirmation, and the answer it returned on the
derived-data question is weaker than the one it was asked to confirm.

### 5.2 Follow-up to Twelve Data — clause reference

**Send to:** reply directly on the existing thread.
**Subject:** Re: Data retention rights after subscription termination

> Thank you — that is clear and answers what we asked.
>
> Two short follow-ups so we can file this properly.
>
> **1.** Your answer to question 1 carries a footnote marker. Could you give the
> clause reference itself — the section number or heading in the terms that
> states the 30-day deletion requirement — so we can cite the source rather than
> the summary?
>
> **2.** We note the reply was composed by your AI agent. Could a member of your
> team confirm it, particularly the point that the deletion requirement extends
> to datasets *derived* from your data and to internal research corpora built
> partly from it? That specific point determines whether we can use your data in
> a permanent internal archive, so we would rather have it confirmed than
> assumed.
>
> No action is needed beyond that, and nothing here is a complaint — the answer
> was fast and directly responsive.
>
> Thank you.

### 5.3 Twelve Data follow-up — the clause, and a walked-back interpretation

**Sent and answered 2026-08-29, 11:53 local.** Responder again "Dooz, Twelve
Data's AI Agent"; no human confirmation was given, which was half of what the
follow-up asked for.

**Question 1 — the clause. Answered.** Terms of Use §16, *Data retention and
deletion*, sub-clause 16.2 *Data Deletion*, quoted as:

> Upon termination or expiration: All Data must be deleted within 30 days.
> Certification of deletion may be requested.
> Audit trail data may be retained for compliance.

**Question 2 — confirmation that this reaches derived data and research corpora.
Not confirmed, and softened.** The first reply listed "derived datasets (bars,
series, transformations)" and "research corpora or analytical results built from
the data" as things the requirement applies to. Asked to confirm precisely that,
the second reply said instead that because the requirement applies to "all
Data", the reading that it covers derived datasets and internal research corpora
**"is consistent with the wording of the clause"**.

**Record the difference rather than smoothing it.** "The requirement applies to
X" and "reading the requirement to cover X is consistent with its wording" are
different claims, and only the first would settle the question. §16.2 as quoted
does not name derived data; the extension to it is the agent's inference from
the word "Data". Sharadar's licence, by contrast, names the category outright —
which is why Sharadar's exclusion rests on clause text and this one rests on
clause text **plus** an interpretation.

**None of this changes the classification.** `DELETION REQUIRED AFTER
TERMINATION` holds under either reading, because the raw prices are "Data" on
any construction. What is unsettled is the *reach* — specifically whether
`full-01`'s derived tables fall inside it — and that turns on the definition of
"Data" in the Terms.

**Do not send a third enquiry.** A third AI-composed reply adds nothing a
definition would not settle better. `twelvedata.com/terms` §16 and the
definitions section, read by the operator, would be `USER-VERIFIED` primary text
and outranks anything relayed. That is the next step and it is two minutes'
work.

One note on provenance while filing this: the mail client rendered its own
"AI Overview" of the thread above the message, by Gemini. **That is a third
model summarising two others and is not evidence of anything** — the record here
is taken from the vendor's message body, not from the client's summary of it.

---

**Q2(c) and Q2(e) decide it, and a partial answer does not upgrade a
classification.** Sharadar is the worked example: its licence permits retaining
nothing *and* requires deleting datasets derived from its data within 30 days,
which would oblige deletion of the research corpus rather than merely the files.
A vendor that says "yes, keep the files" and says nothing about derived data
leaves `research-01` undetermined, and undetermined is treated as prohibited.

---

## 6. Current status

| vendor | classification | basis |
|---|---|---|
| Kibot | licence text permits permanent retention (**USER-VERIFIED**); **scope over derived data unconfirmed** | Q23–Q26 unanswered, and now moot — the vendor is eliminated on price |
| **Twelve Data** | **`DELETION REQUIRED AFTER TERMINATION`** | **ANSWERED 2026-08-29, with a clause.** Terms of Use §16.2: all Data deleted within 30 days of termination or expiration, certification available on request. **Excluded from any permanent corpus.** Whether the obligation reaches *derived* data is the vendor's interpretation of "all Data" rather than clause text — open, and it decides `full-01`'s fate. Filed at §5.1 and §5.3 |
| FMP | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | sent 2026-08-29, **awaiting reply** |
| Tiingo | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | sent 2026-08-29, **awaiting reply** |

**No retention determination is inferred from the fact that our API access
currently works.** Access and retention are different grants, and it is common
for the first to be generous while the second is silent. Twelve Data is the
demonstration: the API worked throughout, and the answer was still no.

**Nor is one inferred from a blocked fetch.** `EGRESS_BLOCKED` is a fact about a
session's network and says nothing about a vendor's terms — it establishes that
they were not read. Asking was what settled it, and asking took one message.

**Two replies now decide the price spine.** Sharadar, EODHD and Twelve Data are
excluded on licence and Kibot on price, so FMP and Tiingo are what remain. If
both answer as Twelve Data did, no vendor this project has examined satisfies
the permanent-retention rule, and the choice becomes: relax the rule, pay for a
licence that grants retention explicitly, or build the archive only from public
sources ([`DATA_RETENTION_RIGHTS.md`](DATA_RETENTION_RIGHTS.md) §5.1b).
