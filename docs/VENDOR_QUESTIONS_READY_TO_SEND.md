# Vendor capability questions — ready to send

One message, complete and sendable as written. **No purchase, no account, no
commitment is implied by it.** Every question asks for a specific answer rather
than reassurance, because the whole point is a record that can be checked later.

**Licence retention and deletion terms are deliberately not asked about.** What
a vendor requires when a subscription ends is the operator's decision to manage
at their discretion; it is not a selection criterion and no part of this system
is designed around it. These questions are about **whether the data is any
good** — coverage, delisted history, identity, corporate actions.

**File every reply** with the date, the responder's name and role, what was
answered, and a grade.

| grade | meaning |
|---|---|
| `VERIFIED` | answered in writing, specifically |
| `CORROBORATED` | implied by documentation the vendor pointed at, but not directly answered |
| `UNVERIFIED` | unanswered, evasive, or restated marketing copy |

**Marketing language is not an answer.** A reply that points at a product page
without addressing the question leaves the item `UNVERIFIED`.

**Send it yourself.** The body carries no name or signature — add your own
sign-off before sending. If a vendor routes support through an in-app form
rather than mail, paste the body into the form; nothing in it depends on being
an email.

---

## 1. The capability questions — 22 of them

> **Written for Kibot, and vendor-neutral in substance.** Kibot is eliminated on
> price ([`PHASE_06_VENDOR_MATRIX.md`](PHASE_06_VENDOR_MATRIX.md) §1.1), so send
> these to whichever vendor is being evaluated — Sharadar and EODHD are the
> untested candidates.
>
> **Question 1 is the one that decides any price vendor**: whether "delisted"
> securities are actually included, or only currently active ones. It has never
> been answered by anybody.

**Send to:** the vendor's sales or pre-sales contact.
**Subject:** Pre-sales questions — historical US equity EOD data and delisted coverage

> Hello,
>
> We are evaluating your data for a one-time historical backfill of US equity
> daily data for an internal, non-public research project. We do not
> redistribute, resell, publish or display data to third parties.
>
> Before subscribing we need clear answers to the following.
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
> Thank you.

**Questions 1 and 7 are the decisive pair.** Question 1 establishes whether
delisted history exists at all; question 7 establishes whether it is *complete* —
whether the small, short-lived 1998–2002 listings are present, or only the large
well-known failures. A roster can pass question 1 and fail question 7, and the
second failure is the one that silently reintroduces survivorship bias, which is
the entire problem `research-01` exists to avoid.

**Question 5 is what eliminated Kibot**, in the end: the advertised subscription
price was not the price of the archive, and asking "what does it cost in total"
is what surfaces that before a purchase rather than after.
