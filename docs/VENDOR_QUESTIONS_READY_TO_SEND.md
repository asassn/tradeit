# Vendor questions — ready to send

Three messages, complete and sendable as written. **No purchase, no account, no
commitment is implied by any of them.** Each asks for clause citations rather
than reassurance, because the whole point is a record that can be checked later.

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

## 4. Current status

| vendor | classification | basis |
|---|---|---|
| Kibot | licence text permits permanent retention (**USER-VERIFIED**); **scope over derived data unconfirmed** | Q23–Q26 unanswered |
| Twelve Data | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | `twelvedata.com` returned `EGRESS_BLOCKED`; question unsent |
| FMP | `UNCLEAR — WRITTEN CONFIRMATION REQUIRED` | `site.financialmodelingprep.com` returned `EGRESS_BLOCKED`; question unsent |

**No retention determination is inferred from the fact that our API access
currently works.** Access and retention are different grants, and it is common
for the first to be generous while the second is silent.
