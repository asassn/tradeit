# Costing "buy more history" — and why it cannot be costed as asked

Requested by the owner 2026-10-19, after Track B reached its decision point. The
brief was: name the vendors, the prices, what we could then measure, and what
stays broken without it.

**Two of those four cannot be answered, and the reason is the finding.** The
purchasable options would make the corpus *worse* for this purpose, and the one
product class that would fix it has no public price.

## What the constraint actually is

The power analysis is precise: effects of 0.1–0.8% sit below what block variance
of 1.0–2.6% can resolve, and **only more independent blocks move that.** Thirty-
five years of history are needed to resolve 0.50% at the current median block sd.
The clean Stage A window is 2003–2019 — seventeen years.

So the instinct is right: this is a history problem. **The instinct about what to
buy is wrong.**

## The obvious purchase makes it worse, and this is measured

Extending price history before ~2003 does not add seventeen more honest years. It
adds **survivors' years**.

`RESEARCH_01_DATA_DICTIONARY.md` §0.1b, measured:

> The corpus's earliest session is now **1990-01-02**. 2,484,305 of its prints
> arrived on 2026-09-16 from Sharadar, extending 1,778 securities backwards […]
> **Those are survivors' prints.** […] Lengthening the survivors' history there
> makes the window **more** survivor-weighted, not less.

**The free gain has already been taken.** Sharadar's earlier history was loaded
on 2026-09-16 — 2.48 million prints, back to 1990 — and the record states what it
bought: a window that is *more* survivor-weighted. There is no unspent free
option here, and buying the same shape of data from another vendor buys the same
problem at a price.

**Why prices alone cannot fix it.** The binding gap is not prices of dead
companies, it is **knowing which companies existed and failed.** That is the
survivorship denominator, and `EDGAR_DELISTING_DENOMINATOR.md` §7be records it as
blind to exchange delistings before roughly 2002 — the exact window more history
would have to come from. Gate coverage of dated exits is **25.4% for 1998** and
lower before. A backtest on survivors "is not optimistic, it is fiction", and
paying to lengthen the survivors' record would buy more fiction.

## The vendors on record, and why none of them is the answer

From `PHASE_06_VENDOR_MATRIX.md`, measured rather than asserted:

| vendor | why it does not solve this |
|---|---|
| **Sharadar** | already purchased; its plan returns **nothing before 1997-12-31** whatever `firstpricedate` claims, and its earlier history is already loaded |
| **EODHD** | **measurably collapses ticker identity** — one series per ticker, the most recent holder, 59,925 entries under 59,925 distinct codes. An earlier holder of a reused ticker *is not addressable*. It also holds the recent delistings its list enumerates and not the older ones, and **66% of our 13,247 unpriced exits are pre-2010** |
| **Kibot** | eliminated on price for the EOD archive ($990–$2,400); still live for `intraday-01` only |
| Twelve Data, FMP, Tiingo | in use operationally, never capability-probed for archive depth; none claims point-in-time listing evidence |

**EODHD's limit is the important one**, because it is the shape of the whole
problem: a vendor keyed on ticker cannot represent the identity this corpus keeps
separate, and asking it for a dead registrant's symbol returns *whichever company
holds those letters now*. That is the splice the control universe exists to
catch.

## The product class that would work, and what I could not establish

The requirement is **point-in-time US equity history with delisting evidence** —
not prices plus a guess, but a record of which securities were listed on each
date and what happened to the ones that stopped. The reference implementation of
that is **CRSP**, which covers active *and inactive* US securities with delisting
returns appended to each series.

**What I could not establish, and did not invent:**

* **No price.** CRSP is distributed institutionally, commonly through WRDS, and
  does not publish a figure a sole researcher can read off a page. A search for a
  2026 price returned nothing quotable.
* **No verified low-cost substitute.** A search for alternatives with delisting
  returns surfaced only informal suggestions — the frozen Quandl/Nasdaq WIKI
  archive (~3,000 stocks, ends March 2018), and blog posts proposing
  exchange files plus a self-written delisting rule. None is verified, and a
  self-written delisting rule is the assumption this project refuses to make.
* **No depth claim I am willing to quote.** CRSP's daily coverage reaches
  substantially further back than this corpus, but I did not verify the exact
  start year from primary material and will not assert one.

So this is **not a purchase proposal**, because a proposal naming no price and no
measured capability is the vague *"we could maybe buy X"* the owner specifically
said wastes the offer.

## The one actionable lead, which is free

**CRSP-class access is routinely available through a university library at no
cost to the individual researcher.** That is the cheapest path to the only data
that would change the arithmetic, and establishing whether it is open to this
project costs an email rather than a subscription.

**The operator sends it.** A draft is below; nothing has been sent, and nothing
will be.

## The risk that would remain even with the data

Stated now, because it is the third time this projection shape would be made and
the first two were both wrong in a checkable way:

* Addendum 2 scaled `hurdle × sd / √blocks` by adding blocks and **held sd
  constant.** The blocks doubled; the limit did not halve, because 2008 raised
  median block sd from 1.00% to 1.40%.
* Pre-2003 history contains **1998–2002**, which includes the dot-com collapse —
  another high-variance period, by the same logic that made 2008 raise it.

**So more history is a bet that block sd stops rising as fast as blocks are
added, and that bet has already lost twice.** The proposal that follows from this
is therefore not "buy and then measure" but **"get access, measure the block sd
of the added years first, and only then decide whether the window resolves
anything."** If the added years raise sd as 2008 did, the arithmetic does not
move and no purchase would have helped.

---

## Draft enquiry, for the operator to send

> Subject: CRSP US Stock Database access for an individual research project
>
> Hello,
>
> I am running an independent quantitative research project on US equities and
> need point-in-time historical data that is free of survivorship bias —
> specifically, a record of which securities were listed on each historical date
> and delisting information for those that ceased trading.
>
> My own corpus is built from SEC EDGAR filings and vendor price feeds, and I
> have measured that it cannot establish exchange listings before roughly 2002.
> That gap is what I am trying to close.
>
> Could you tell me:
>
> 1. What access options exist for an individual researcher not affiliated with a
>    subscribing institution, and the cost of each?
> 2. The earliest date of daily US equity coverage, and whether delisting returns
>    are included across that full range?
> 3. Whether a one-time historical extract is available as an alternative to an
>    ongoing subscription, and under what licence terms for internal,
>    non-redistributed research?
>
> Thank you,
> Eric Sasson

**On the licence question deliberately asked narrowly:** retention and deletion
terms are explicitly out of scope for this project and are not a selection
criterion. Question 3 asks about redistribution because that is what determines
whether a one-time extract is even offered, not to evaluate what happens when a
subscription ends.
