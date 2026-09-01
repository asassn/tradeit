# Phase 6 — vendor and data-contract verification

**Nothing was purchased. No subscription was taken.** Implementation *has* begun:
milestone 2 landed the `research-01` schema (`DATA_MODEL.md` Domain 9). No vendor
data has been acquired, which is the claim this line was making and the one that
still holds.

## 0. Verification conditions — read this first

This session's egress policy **blocks every primary vendor source**. Confirmed
`EGRESS_BLOCKED` on: `data.nasdaq.com`, `help.data.nasdaq.com`, `sharadar.com`,
`quantrocket.com`, `resources.quandl.com`, `sec.gov`, `polygon.io`, `eodhd.com`,
`kibot.com`, `flounderteam.github.io`. The proxy README states these are
organisation policy denials and are not to be routed around, so they were not.

What *was* reachable: web search (result extracts only) and
`raw.githubusercontent.com`.

**Confidence grades are used strictly, and no secondary-source claim is promoted
to VERIFIED.**

| grade | meaning |
|---|---|
| **VERIFIED** | read directly from a primary or open-source integration source *in this session* |
| **USER-VERIFIED** | read from the primary source **by the project owner, outside this session**, and reported here. Authoritative for published prices and page text; still subject to the data probe for capability claims |
| **VENDOR-STATED** | written **by the vendor to the operator in correspondence**. Authoritative as to what the vendor *asserts* about its own product and terms — and therefore binding on licence and pricing questions, which are the vendor's to answer. It is **not** a measurement: a capability claim at this grade is still UNTESTED and still subject to the data probe |
| **CORROBORATED** | consistent across ≥2 independent search extracts, primary page not reachable |
| **UNVERIFIED** | single second-hand extract, or not established at all |

Consequently **all pricing in this document except where marked USER-VERIFIED is
UNVERIFIED**.

---

## 1. Vendor eliminations — price and capability only

> **Licence retention and deletion terms are out of scope for vendor selection.**
> What a vendor requires when a subscription ends is the operator's decision to
> manage, at the operator's discretion. It is not a selection criterion, it does
> not disqualify a vendor here, and no part of this system is designed around it.
> Vendors are judged on **coverage, data quality and price**.
>
> Sharadar and EODHD were previously eliminated on licence grounds alone, and
> that elimination is withdrawn. **Both return as candidates on their merits**,
> neither having ever failed a capability test — EODHD's capabilities were never
> the issue, and Sharadar was never probed at all.

### 1.1 Kibot — **ELIMINATED ON PRICE**

**The ~$14 figure this plan was built on was wrong.** *USER-VERIFIED from the
vendor's own pricing page.* Two products were conflated:

- **$14/month is the EOD *subscription*** — it refreshes data already purchased,
  at a quarterly cadence. It is not the price of the archive.
- **The archive is a separate one-time purchase**, priced per universe per
  interval. For the "All Stocks" universe (18,000+ tickers) the observed
  one-time prices run from **$600** (30-minute) through **$3,000** (1-minute) to
  **$9,000** (tick with bid/ask). Cross-universe bundles were **$990**, **$2,400**
  and **$4,800**.

So the corpus this plan assumed cost ~$14 costs **$990–$2,400** — between 70 and
170 times the assumption. Every downstream statement that treated a single ~$14
month as sufficient was wrong, and is corrected rather than quietly dropped.

**One question was never answered and would still gate any purchase.** The
package is described as *"every tradable US common stock"*. Whether *tradable*
means currently tradable — excluding the delisted names that are the entire
problem — is not resolvable from the pricing page, and is Kibot question 1
verbatim. No purchase should proceed without it, at any price.

`docs/KIBOT_DATA_PROBE.md` is retained in full. Its acceptance rules were written
before any data was seen and remain the standard any replacement vendor is held
to; only the vendor it names is out, and only on price.

### 1.2 The candidate field as it now stands

| vendor | status | on what ground |
|---|---|---|
| **Kibot** | eliminated | price — $990–$2,400 for the archive, out of budget |
| **Sharadar** | **candidate, unprobed — questions sent, no reply yet** | never capability-tested; its earlier elimination was licence-based and is withdrawn |
| **EODHD** | **candidate, sample requested and not received.** Depth answered (US 30+ years), delisted data asserted, no trial exists, published pricing does not apply to us (§1.3) | the disqualifier we thought we had was our own misreading; nothing has yet been *measured* |
| **Twelve Data** | in use for operating prices; `full-01` was built from it | never capability-probed for archive use |
| **FMP** | in use for corporate actions and symbol reference | never capability-probed for archive use |
| **Tiingo** | working acquisition adapter | never capability-probed for archive use |

**Nothing here has been probed against the acceptance rules.** Every row above
is a statement about what has and has not been *tested*, and the item most likely
to fail for any of them is survivorship completeness (`KIBOT_DATA_PROBE.md` §G),
which can fail while everything else passes.

### 1.3 Correspondence — what the vendors have actually said

Capability questions have now been **sent** to both surviving candidates. This
section records what came back. It is a log of vendor *assertions*, at
**VENDOR-STATED**; nothing in it has been measured, and nothing in it promotes a
capability cell in §2a.

**EODHD — sent, replied.** One thing is established and it is a licensing fact,
not a capability one: EODHD classifies our use — internal, non-public
organisational research with a one-time historical backfill, no redistribution —
as **commercial**, and states that the subscriptions on its public pricing page
are intended for personal use. **The consequence is that EODHD's published price
is not our price**, and row 18 is corrected accordingly. A commercial quote has
been requested and has not been received. The remainder of the reply is **not
transcribed into this repository**; do not restate it from memory, and quote it
verbatim if it is ever recorded here.

#### Second reply, 2026-08-30 — depth answered, and EODHD is not disqualified

From Levon V., EOD Level 1, cc Sales. Verbatim:

> "US tickers have 30+ years of historical data available. For EU tickers it is
> mostly from 2000 onwards, though there may be exceptions. Delisted tickers are
> also available but need to be checked individually."

**`VENDOR-STATED`. The depth question is answered and EODHD stays a candidate.**
US history is asserted at 30+ years, which spans 1998–2002.

**The "contradiction" was ours.** Their documentation says *"from January 2000"*
for **EU** coverage and *"30+ years"* for **US** — two figures for two markets,
which is not an inconsistency. We read one into it, recorded it as
`internally inconsistent`, and carried it as a live disqualifier — *"if US
history really begins in 2000 it cannot span 1998–2002 and EODHD is out
regardless of price"* — across three emails. Nothing the vendor did caused that.

**"Checked individually" is not a coverage claim** and is not read as one. It
says a delisted ticker can be looked up, not that any particular one is present,
which is the completeness question the vendor already declined to certify.

#### Third reply — the Commercial team, and the first coverage numbers

From **Kristelle, EODHD Commercial team** (`kristelle@eodhistoricaldata.com`).
**Received 2026-08-31, 11:22.** The screenshot carried a time but no date; the
day is the operator's, stated on 2026-08-31 and consistent with the message
being "9 hours ago" in that screenshot. **Derived from those two statements
rather than read off a mail header**, which is why the derivation is recorded
beside it — a date nobody can check is provenance in name only.

**Their numbering is theirs, not ours.** The reply answers items 1–12; our
question set has 22. The mapping is **not assumed** — answers are recorded by
content below, and where content does not unambiguously identify one of our
questions it is left unmapped rather than aligned by position.

**1. The coverage numbers, verbatim:**

> "At the moment we have end of day data for more than 26,000 US delisted
> stocks, almost all delisted companies from Jan 2000. Non-US companies are
> covered, mostly, if they were delisted within the last 6-7 years - we have
> 42,000+ tickers for such stocks. […] We also have fundamental data for
> companies delisted since 2018."

and separately:

> "Data coverage depends on the specific API feed. For Historical EOD and
> Fundamentals APIs, US stock coverage is from the beginning."

**Read together, these say two different things about two different
populations**, and the difference is the whole survivorship question:

| population | US coverage, `VENDOR-STATED` |
|---|---|
| **active** stocks | "from the beginning" |
| **delisted** stocks | **almost all from Jan 2000** |
| delisted, with fundamentals | since 2018 |
| non-US delisted | mostly last 6–7 years only |

> **This is the answer the question had outlived three vendors waiting for, and
> it is a partial pass.** For 2000 onward EODHD asserts near-complete US delisted
> coverage. **For 1998 and 1999 it would supply survivors and not the companies
> that died** — which is survivorship bias exactly, in the two years the
> dot-com build-up occupies.

**It collides with a written decision.** `RESEARCH_01_DATA_CONTRACT.md` §7.1
says the price corpus *"reaches 1998 or it does not ship"*, and that if no vendor
reaches 1998-01-01 **with universe breadth** we return to sourcing prices —
*"not to moving the date"*. Jan 2000 delisted coverage does not meet that. **The
date is not moved here.** Reversing §7.1 is the owner's decision and is put to
him rather than absorbed.

**2. Ticker reuse is handled by a naming convention, and it changes the GM test:**

> "After delisting companies lose their ticker codes, newly traded companies are
> free to reuse them. The system marks tickers with the index 'old'. For example,
> ACR_old.US was traded as ACR before delisting and now another company trades
> under ACR.US."

and:

> "Please note that for delisted tickers that are currently being reused by
> another company or ETF, we add _old at the end (XXX_old)."

> **The GM sample request is now the wrong shape, and would be misread.** We
> asked for `GM`. Under this convention that returns `GM.US` — the *current*
> registrant only, starting after 2009 — while the pre-2009 company lives at
> `GM_old.US`. A short series is therefore the **correct** result, not missing
> coverage, and the splice test cannot fire on one symbol.
>
> **Testing the splice needs both symbols.** The property to check becomes:
> `GM.US` must not extend before the break, `GM_old.US` must not extend after
> it, and neither may contain the other's bars. **Recorded before the files
> arrive**, because the failure mode has inverted twice now and a reader without
> this note would grade a correct short series as a coverage gap.

**3. Identity, and what it is and is not worth:**

> "Yes, via the ID Mapping API" — CUSIP / ISIN / FIGI / LEI / **CIK** ↔ symbol,
> plus a **Stock Symbol Rename History API (US only)**.

A symbol↔CIK mapping is directly relevant to the 33 unestablished ticker
intervals in the seeded corpus. **It is a lead, not evidence.** A vendor
reference file is categorically short of `MANUAL_VERIFIED` — the same reason
`company_tickers.json` could not carry a control — so it may suggest an interval
to verify against a filing and may never supply one.

**4. Lifecycle dates, with a limitation stated by the vendor:**

> "IPODate covers listing (there's no separate listing-date field distinct from
> IPO date), and DelistedDate covers delisting — but only visible for US stocks
> marked as delisted. Non-US delistings aren't covered by that field."

**An IPO date is not a listing date** and the vendor says so plainly. For the
`listings` table that means EODHD can supply a delisting date for US delisted
stocks and cannot supply a venue-listing start.

**5. Throughput:** all commercial subscriptions carry a base of **100k API calls
per day**, `VENDOR-STATED`.

**6. Out of scope, and recorded only so nobody re-asks.** The reply gives 30-day
and 60-day termination notice periods. Per *Working with vendors*, licence
retention and deletion terms are **not a selection criterion and no part of this
system is designed around them**. Noted, weighed at zero.

**7. Price is still unquoted**, and the vendor has asked us a question before it
can quote — see below.

#### FIRST MEASUREMENT, 2026-09-01 — subscribed, and four symbols pulled

The subscription was taken and the sample request answered ourselves, 12 API
calls. **This is the first thing in this document that is a measurement rather
than an assertion.**

| symbol | sessions | span | dividends |
|---|---|---|---|
| `ETYS.US` | 447 | **1999-05-20 → 2001-02-26** | 0 |
| `WBVN.US` | 420 | **1999-11-05 → 2001-07-06** | 0 |
| `GM.US` | 3,968 | 2010-11-18 → 2026-08-31 | 41 |
| `GM_old.US` | 3,333 | 1998-01-02 → 2011-03-31 | 0 |

**Coverage beats the stated floor.** EODHD says delisted US history runs "from
Jan 2000". Both dot-com controls reach **1999** — ETYS from May, WBVN from
November — and `GM_old` reaches **1998-01-02**. On these four names the vendor
under-promised.

**That is four names, not a population.** "Almost all from Jan 2000" is a claim
about ~26,000 tickers and remains untested; four beating it is encouraging and
is not a survivorship measurement. The denominator run is what would settle it.

**It bears on §7.1 and does not yet discharge it.** `RESEARCH_01_DATA_CONTRACT`
§7.1 requires 1998 coverage *with universe breadth*. Breadth is exactly what
four symbols cannot show. The 1998–1999 gap stays open as recorded, with the
first evidence that it may be narrower than the vendor's own description.

##### The splice check reported PASS, and the check was wrong

`GM_old` runs to **2011-03-31** and `GM` starts **2010-11-18** — they **overlap
by 133 days**. The first version of the check compared each span against a
hardcoded 2009 date instead of against the other span, and printed
*"disjoint, no splice. PASS"*.

**It produced the expected-looking answer to a question it had not asked** —
the exact failure §"Never answer by accident" describes, committed by the tool
written to detect it. Fixed to compare the spans to each other, and pinned by a
test that asserts the *old* logic's verdict was false so it cannot return.

**The overlap itself is probably not a vendor defect.** New GM listed on
2010-11-18 while the old entity was still winding down, so the two securities
genuinely coexisted for months. What it establishes is narrower and more useful:

> **A ticker here cannot be resolved by date alone.** `symbol_aliases` must
> carry both securities with intervals decided from **evidence**, not from the
> vendor's spans — because the vendor's spans overlap, and an importer resolving
> "GM on 2011-01-15" against them has two answers.

That is a direct constraint on the ticker-interval curation still outstanding,
and it was found by measurement rather than anticipated.

##### One open question this raises

`GM_old` spans 1998→2011 continuously. The old GM common stopped trading as
`GM` in 2009 and continued under a different ticker during its wind-down, so a
single unbroken `GM_old` series across 2009 may itself be two ticker regimes of
one issuer flattened into one symbol. **Not resolved here**, and it is the
inverse of the trap already recorded from their rename guide: a symbol's history
may extend beyond the period that symbol was actually in use.

#### EODHD's own Claude plugin, read as documentation — and what it settled

`github.com/EodHistoricalData/eodhd-claude-skills` (MIT, vendor-published). Read
in full, **not installed** — the reasoning for that is below. As *documentation*
it settled three things the demo token could not, because
`/exchange-symbol-list` requires a paid key:

**1. `delisted=1` is confirmed, with its response shape.**

```
https://eodhd.com/api/exchange-symbol-list/US?api_token=...&delisted=1
```
returns `Code`, `Name`, `Country`, `Exchange`, `Currency`, `Type`, **`Isin`**,
**`IsDelisted`**. The plugin's own *endpoint* doc omits `delisted` from its
parameter table; only the delisted-tickers **guide** documents it. Worth
recording: the vendor's own documentation is inconsistent about the single
parameter this project most depends on.

**2. Ticker reuse goes further than `_old`.** Reuse by a third or fourth holder
produces `_old1`, `_old2`. `GM`/`GM_old` is the two-holder case, not the general
one — a ticker may name **three or more unrelated companies**, and a symbol list
that stopped at `_old` would silently drop the middle ones.

**3. Coverage is independently corroborated.** "26,000+ US tickers (from Jan
2000), 42,000+ non-US (latest 6-7 years)" — the same figures Kristelle gave, in
a document written before our correspondence. That is corroboration of the
*claim*, and still not a measurement.

**A trap in their Scenario 1, recorded because it is not obvious.** On a plain
rename with no delisting, EODHD *moves* history to the new symbol and the old
code becomes inaccessible. So an absent symbol does not mean an absent company,
and a ticker's history may begin before that ticker existed. Any interval we
derive from a vendor symbol is a statement about the **symbol**, not the
company.

**The ISIN is worth noting.** The delisted symbol list carries `Isin` per symbol
— a *security* identifier, which is what `security_identifiers` exists for. It
is a candidate route to the ticker intervals the corpus lacks, and it would
enter at `VENDOR-STATED`: usable for the broad universe, never sufficient to
carry one of the 30 controls, which require a filing somebody read.

**Why it is not installed.** The plugin's purpose is AI-generated analysis —
company briefs, "trend analysis with key support/resistance levels" — and its
own README says so: *"This plugin delivers AI-generated analysis on top of EODHD
market data. Always verify figures."* That is the opposite of what this system's
assistant is for, which is to explain **actual system evidence and provenance,
never invented rationale**.

The MCP server is the sharper objection. It would make a price fetchable
mid-conversation, which routes around the importer — and the importer is where
identity resolution, point-in-time stamping, splice refusal and
`UNRESOLVED` live. **Every guarantee this corpus makes is enforced in that one
path.** A convenient side-door around it is a risk rather than a feature, and
the risk is that it would be used without anyone noticing the difference.

Finally: **"survivorship" does not appear in any of its 146 documents.** That is
not a criticism — it is simply not what the plugin is for, and it is a precise
statement of why it does not replace anything here.

#### The vendor is waiting on us: which licence class we are

> "if the data is being shared between members of an organisation, being used
> commercially and/ or any raw or derived data is displayed to any third parties
> - then a commercial subscription is required. […] **Which of the above
> categories best fits your use case?**"

| class | applies when |
|---|---|
| **Internal Use** | used within a business entity; raw feeds, values and pricing not shared with non-employees. "If only calculated outputs are seen by your users" |
| **External Use or Display** | data shared or displayed outside the organisation; requires a signed data services agreement and a display licence |

**CORRECTED by the owner: personal use, not commercial.** The first answer
given here was *Internal Use* — a **commercial** sub-category — and that was
wrong about the facts.

**The misclassification is ours, and it is traceable to our own wording.** The
original send described the work as *"an internal, non-public research project"*
and the reply was recorded as classifying *"internal, non-public **organisational**
research"*. "Internal" and "organisational" are business words: they describe a
company using data across its staff. TradeIt is **one individual, on one
machine, researching his own investing**. There is no organisation, no
employees, no colleagues, nothing shared and nothing displayed.

A vendor classifies from the description it is given. We gave a description that
implied a business, and were classified as one.

**The correction states the facts and lets EODHD classify them** — it does not
assert a category on our behalf. If those facts still read as commercial to
them, that is their answer to give.

**The condition that would genuinely change it is unchanged and still recorded.**
Personal-use data is normally licensed to the individual and **cannot be carried
over into commercial use later**. So if TradeIt ever manages anyone else's
money, is sold, is shown to others with real prices, or becomes a product, the
licence must be re-taken — and data acquired under a personal licence may have
to be re-acquired. That is a real cost to weigh once, now, rather than discover
later.

**The condition matters and is recorded rather than left implicit.** The roadmap
includes a dashboard, and a dashboard that ever shows a raw close to anyone other
than the operator moves TradeIt into **External Use / Display** — a different
licence, a signed data services agreement, and a different price. That is a
licence boundary sitting inside a product feature, so it is written here: **if
the dashboard is ever opened to another person with real prices visible, the
licence class must be revisited before it is.**

#### There is no trial. The thing we reframed the question to ask for does not exist

`VENDOR-STATED`:

> "we do not provide trials like that… only monthly subscriptions."

The reframe asks for *the least expensive access that lets us measure coverage
ourselves*. This answers it: **that access is not sold.** A monthly commercial
subscription, at a price not yet quoted, is the only door.

That is a real finding rather than a dead end, and it is the second time the
cheap door has turned out not to exist — Kibot's ~$14/month subscription was not
the archive either. **An advertised entry price is a claim about a different
customer until a vendor quotes ours.**

#### They offered sample files, unprompted, and we chose the three deliberately

`VENDOR-STATED`, offered free and without being asked:

> "you can provide any 2-3 symbols of your choice, and we will send you the
> files with data."

**This is better than the trial we asked for**, because a trial would have shown
us the product and this shows us the *data*. We accepted with **ETYS**, **WBVN**
and **GM**.

| symbol | why |
|---|---|
| `ETYS` | listed 1999, gone 2001. Small and short-lived — the population question 7 is about |
| `WBVN` | same shape, independently chosen so one absence is not one anecdote |
| `GM` | **an identity-splice test, and the vendor was not told so** |

**ETYS and WBVN are the measurement we cannot otherwise make.** §7be established
that our own denominator holds **no exchange-listing evidence before 2006**, so
for the 1998–2002 window this corpus exists to reconstruct we have no instrument
of our own. These two files are evidence about exactly the years we are blind in.

**GM's purpose was withheld on purpose, and that must be read correctly when the
file arrives.** Two unrelated registrants held that ticker across 2009 — CIK
40730 and CIK 1467858 — and no price series may legitimately run across the
break. If the returned file shows **one continuous series through mid-2009, the
vendor has spliced two companies into one price history.** Telling them what we
were testing would have let the answer be prepared rather than measured.

> **A continuous 2009 series is a FAILURE, not a success.** It will look like
> complete coverage. This is the exact shape of *never answer by accident*: the
> expected-looking output is the defect, and anyone reading the file without this
> note would grade it the wrong way round.

**No large survivor was chosen.** `AAPL` would come back complete from any
vendor and prove nothing about the only thing in question.

**Status: requested, not received.** Nothing here promotes a capability cell in
§2a. The assertions are the vendor's; the sample files are the measurement, and
they have not arrived.

**Two defects in the EODHD send, both ours, both corrected in a follow-up on the
same thread.** The message as sent carried two questions belonging to Sharadar's
schema (`permaticker`, the `ACTIONS` table), which are not EODHD concepts; and
**the one EODHD-specific question was omitted** — its documentation gives
*"from January 2000"* in one place and *"30+ years"* in another, and which is
correct for US common stocks is the question that decides EODHD for us. If US
history genuinely begins in 2000 it does not span 1998–2002 and EODHD cannot
serve `research-01` at any price. The misdirected pair has been withdrawn and the
depth question asked; **no answer yet**.

**Sharadar — sent, no reply.** Addressed to Nasdaq Data Link, which distributes
it. Two questions were added ahead of the vendor-neutral set:

- a **licence-classification question asked first**, because EODHD's answer shows
  that published pricing can evaporate on exactly our use, and it is cheaper to
  learn that in the first exchange than the fourth;
- a **restatement of the completeness question**, which is the methodological
  change worth recording: we no longer ask a vendor to certify that its delisted
  coverage is complete. Milestone 0a built an EDGAR-derived benchmark that
  measures coverage independently, so the question put to the vendor is now the
  much smaller one — *what is the least expensive access that lets us run that
  measurement ourselves*. A vendor's assurance was never admissible evidence
  here; now it does not need to be.

### Question 1 — answered **in part**

**EODHD answered question 1 on 2026-08-29, and the reply splits it in two.**
Verbatim, from EOD Level 1 (`supportlevel1@eodhistoricaldata.com`):

> "We do provide historical EOD data for active and delisted securities, along with separate dividends and splits endpoints. However, support cannot certify that the complete active and delisted US universe, every security from 1998 onward, all delisting reasons, or every lifecycle event will be present without a requirements review and commercial coverage assessment."

| half of question 1 | grade | |
|---|---|---|
| **existence** — is delisted EOD data provided at all? | **`VENDOR-STATED`** | **yes**, with separate dividends and splits endpoints |
| **completeness** — the full US universe, from 1998, all reasons, every lifecycle event? | **not certified** | the vendor explicitly declines, absent a requirements review and commercial coverage assessment |

### Our own errors about this vendor — three, and every one is ours

**This is not a vendor-error log.** EODHD has answered every question put to it,
in writing, within a day. All three entries below are mistakes *we* made reading
what it said, and they are kept visible on the same reasoning that keeps the
falsified predictions in `EDGAR_DELISTING_DENOMINATOR.md`: a wrong statement left
standing is cheaper than one silently corrected.

| # | what we recorded | why it was wrong |
|---|---|---|
| 1 | *"Question 1 remains unanswered by anybody… it has outlived three vendors"* | **too strict.** The existence half **was** answered in writing, and treating a partial answer as none discards a real result |
| 2 | question 1 graded `VERIFIED` | **too generous.** `VERIFIED` means answered specifically, and the completeness half was expressly **not**. Grading a question off its easier half is how an unverified claim acquires a tick |
| 3 | *"from January 2000" vs "30+ years" — internally inconsistent*, carried as a disqualifier: *"if US history really begins in 2000… EODHD is out regardless of price"* | **there was no contradiction.** January 2000 is the **EU** figure and 30+ years is the **US** figure — two markets, two numbers. We invented the inconsistency and held it against the vendor across three emails |

**Errors 1 and 2 came from paraphrase**, which is the argument for the rule this
document already carries: quote it verbatim or not at all. **Error 3 came from
something worse** — reading two figures as competing answers to one question
without checking whether they answered the same question. That is the same
failure the schema work is built to prevent: two things that look alike are not
therefore one thing.

**The pattern worth naming:** all three errors ran *against* the vendor except
the one that ran for it, and error 3 nearly eliminated a candidate on our own
misreading. A vendor record that only logs vendor faults would have recorded none
of this.

**A note on provenance.** The reply answers the **original** send, not the
follow-up: it still responds to Q23/Q24, which the follow-up had withdrawn. It
therefore predates, and is unaffected by, the reframe described above.

### The vendor declined to certify completeness *before* we asked it not to

The reframe — stop asking a vendor to certify completeness, ask only for the
cheapest access that lets us measure it — was sent **after** this reply was
written. So the vendor's refusal is not a response to our reframe; it arrived
independently.

**That is corroboration rather than coincidence.** The reframe was argued from
our side (0a builds the benchmark, so an assurance is not admissible evidence);
the vendor reached the same boundary from its side, and declines to assert
precisely what we had decided not to rely on. Two parties independently locating
the same line is the strongest available evidence that the line is real.

**It also sharpens what we must not over-claim.** `EDGAR_DELISTING_DENOMINATOR.md`
§7be measures that our own benchmark holds **no exchange-listing evidence before
2006** — `25-NSE` does not appear once until then. So for 1998–2002 neither party
can currently speak to completeness: the vendor will not certify it, and our
instrument cannot yet measure it. **The reframe asks for the right thing and does
not by itself close that window.**

---

## 2. Vendor matrix

Split into two tables, because the **price/lifecycle spine** and the
**fundamentals spine** are different problems with different candidate sets.
**Every judgement here is coverage, quality or price.** Licence retention terms
are not a criterion and no row records them.

### 2a. Price and security-lifecycle history

| # | criterion | **Kibot** | **Sharadar** | **EODHD** | **Polygon / Massive** | **Twelve Data** |
|---|---|---|---|---|---|---|
| 1 | price history start | "up to 64 years" daily EOD; 1998 coverage claimed — USER-VERIFIED (vendor claim) | "deep history to 1998" — CORROBORATED | **Two populations, two answers — VENDOR-STATED. ACTIVE US: "from the beginning" / "30+ years". DELISTED US: "almost all delisted companies from Jan 2000", 26,000+ names.** So 1998–1999 would be survivors only, which fails `RESEARCH_01_DATA_CONTRACT` §7.1 pending the owner's decision (§1.3). Fundamentals for delisted names only since 2018 | not established | "back to the first trading date" — CORROBORATED |
| 2 | active **and** delisted | **active + delisted rosters, and a delisted-only roster — USER-VERIFIED (vendor claim); completeness UNTESTED** | yes — CORROBORATED | **existence YES — VENDOR-STATED in writing 2026-08-29, with separate dividends and splits endpoints. Completeness for the 1998+ US universe EXPLICITLY NOT CERTIFIED absent a paid requirements review (§1.3)** | **"spotty at best" — CORROBORATED** | not established |
| 3 | delisted history truly downloadable | **UNTESTED — probe item A** | claimed — CORROBORATED | claimed — CORROBORATED | doubtful | unknown |
| 4 | permanent identifier | **UNTESTED — probe item D. Expect none; expect ticker-keyed files** | `permaticker` — UNVERIFIED | **ID Mapping API: CUSIP/ISIN/FIGI/LEI/CIK ↔ symbol — VENDOR-STATED.** A lead for the 33 unestablished ticker intervals, and categorically short of `MANUAL_VERIFIED`: a vendor reference file may suggest an interval, never supply one | not established | not established |
| 5 | raw / unadjusted prices | **unadjusted, split-adjusted and fully-adjusted equity data — USER-VERIFIED (vendor claim); methodology UNTESTED — probe item E** | yes, three bases — CORROBORATED | not established | flat files — CORROBORATED | in use for `full-01` |
| 7 | splits | **UNTESTED — probe item E** | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes |
| 8 | dividends | **UNTESTED — probe item E** | `ACTIONS` — CORROBORATED | yes — CORROBORATED | yes | yes |
| 9 | delisting **reason** | **expect none — a price vendor. Reasons come from EDGAR** | `ACTIONS` — CORROBORATED | delisted product — CORROBORATED | weak | not established |
| 16 | API vs bulk | **UNTESTED — probe item A** | both — UNVERIFIED | both — CORROBORATED | REST + S3 — CORROBORATED | REST |
| 18 | price | **~$14/month EOD subscription — USER-VERIFIED (vendor page); exact tier for the full historical universe UNTESTED — probe item A** | UNVERIFIED (~$69/mo cited); licence classification asked, **no reply yet** | **published price DOES NOT APPLY — VENDOR-STATED that public pricing is for personal use and that our use is commercial (§1.3). The ~€59.99/mo figure is withdrawn, not merely unverified. Commercial quote requested, not received** | UNVERIFIED (~$29/mo cited) | existing subscription |

### 2a-i. Intraday history — a separate corpus, a separate decision

Added because `research-01` is no longer the only corpus: the Day mandate and the
Swing mandate's trigger layer need true 1-minute history, which cannot be derived
from daily bars at any price (`MULTI_TIMEFRAME_MANDATES.md` §6.2). **This does
not gate the EOD decision and must not delay it.**

Every cell is **UNTESTED**. Probe items H1–H12 in `KIBOT_DATA_PROBE.md`.

| # | criterion | **Kibot** | **Twelve Data** | **Polygon / Massive** |
|---|---|---|---|---|
| I-1 | historical intraday exists | advertises intraday products — untested | intraday API, depth not established | flat files include trades/aggregates — CORROBORATED |
| I-2 | **raw 1-minute bars** | untested — H2 | untested | untested |
| I-3 | earliest intraday history | untested — H3 | untested | untested |
| I-4 | **delisted securities intraday** | **expected no** — H4 | expected no | expected no |
| I-5 | **bulk file delivery** | untested — H5. **Hard requirement**: ~295k REST requests otherwise | REST only, as used today | S3 flat files — CORROBORATED, its strongest feature |
| I-6 | regular vs extended hours separable | untested — H8 | untested | untested |
| I-7 | timestamp convention (UTC? open- or close-stamped?) | untested — H9 | untested | untested |
| I-8 | corporate-action treatment intraday | untested — H10 | untested | untested |

**Note the reversal:** Polygon is ruled out for the EOD corpus because delisted
coverage is its weakest area, but bulk flat-file delivery is its *strongest* —
and bulk delivery is the hard requirement for intraday, where delisted coverage
is expected to be unavailable from everyone. **Polygon is therefore not ruled out
for `intraday-01`** and should be evaluated on item H alongside Kibot.

### 2b. Fundamentals and filing metadata

| # | criterion | **SEC EDGAR** | **Sharadar** | **EODHD** | **Twelve Data** | **FMP** |
|---|---|---|---|---|---|---|
| 10 | fundamentals | **XBRL values 2009+ — CORROBORATED** | SF1, ~150 indicators — CORROBORATED | Extended Fundamentals — CORROBORATED | **5 years only — CORROBORATED, disqualifying** | **excluded by project rule** |
| 11 | as originally reported | **yes — "as filed", uncorrected — CORROBORATED** | `ARQ`/`ARY`/`ART` — VERIFIED | not established | no | excluded |
| 12 | restated | amendments are separate filings | `MRQ`/`MRY`/`MRT` — VERIFIED | not established | no | excluded |
| 13 | **actual filing date** | **`filed` in full-index 1994 Q3+; `filed` in FSDS 2009+ — VERIFIED** | `DATEKEY` — VERIFIED | not established | no | excluded |
| 14 | accession / filing id | **`adsh`, primary key — CORROBORATED** | not established | not established | no | excluded |
| 15 | genuinely PIT before 2009 | **filing *dates* yes from 1994 Q3; machine-readable *values* no before 2009** | **UNTESTED** — see §4 | **UNTESTED** — see §4 | n/a | n/a |
| 4 | permanent identifier | **CIK — VERIFIED** (but CIK↔ticker for 1998–2008 is the hard part; see improvement plan §5) | — | — | — | — |

### What the matrix now decides on its own

- **Sharadar and EODHD: candidates, unprobed — but no longer unasked.** Their
  earlier elimination was licence-based and is withdrawn; neither has ever been
  capability-tested for the archive role, and Sharadar's `permaticker` and
  as-reported/restated split are the strongest claimed feature set in the
  fundamentals table. Questions are now with both (§1.3). **EODHD's published
  price is already out** — it applies to personal use and ours is commercial by
  the vendor's own classification — so the field has not narrowed on capability,
  it has narrowed on cost, which was not the axis anyone was watching.
- **Polygon/Massive: still ruled out** for EOD on delisted coverage — the entire
  problem being solved — and **still a candidate for `intraday-01`**, where bulk
  flat-file delivery is its strongest feature.
- **Twelve Data: retained for forward daily prices**, ruled out for historical
  fundamentals (5 years of history, which is disqualifying on its own).
- **FMP: excluded by standing project rule** for fundamentals, ratios, earnings,
  estimates, statements, OHLCV, insider and institutional data. Permitted role is
  corporate-action and symbol-change corroboration only.
- **SEC EDGAR: adopted, and now built.** Free, public domain, and the only source
  that can independently *verify* or *enumerate* what a price vendor claims.
  Milestone 0a has run against a real archive, which changes what we have to ask
  a vendor for: completeness is now something **we measure** rather than
  something a vendor certifies (§1.3).
- **Kibot: eliminated on price**, and entirely unverified as data. See §3.

**No vendor in either table has been probed.** The field is wider than it was —
three candidates rather than one — and none of them has been measured.

---

## 3. Kibot — what is claimed, and what must be proven

Everything in this section is a **vendor claim relayed via USER-VERIFIED reading
of Kibot's own pages**, not a measurement. The distinction matters more here than
anywhere else in this document, because the claim being made — a complete
survivorship-safe US equity history — is one no other vendor in the matrix makes
as directly.

| claimed | status |
|---|---|
| ~$14/month EOD subscription | USER-VERIFIED page price. **Which tier actually exposes the full historical delisted universe is untested** |
| up to 64 years of daily EOD history, stocks/ETFs/futures/forex | vendor claim, **untested** |
| unadjusted / split-adjusted / fully-adjusted equity data | vendor claim, **untested** |
| active+delisted and delisted-only rosters, 1998 coverage | vendor claim, **untested** |

**A cheap price for a claim nobody else makes is a reason for more scrutiny, not
less.** The specific failure mode to look for is a delisted roster that exists
but is thin: heavy on large, well-known failures and light on exactly the
short-lived, small, thinly-traded 1999–2002 listings whose absence *is*
survivorship bias. That is probe item G, and it is the one that can fail while
every other item passes.

**Kibot is not recommended for purchase in this document.** It is recommended as
the sole subject of a data probe — see `docs/KIBOT_DATA_PROBE.md` §8 for the
minimum-cost way to run that probe and the decision gate that follows it.

---

## 4. The pre-2009 point-in-time question, restated

**XBRL does not exist before 2009.** Any vendor's pre-2009 fundamentals were
derived by parsing filing documents, and a `filed_at` that was *reconstructed*
from a period-end rather than taken from the filing record would make the
pre-2009 segment **silently non-point-in-time** — worse than not having the data,
because the defect is invisible in aggregate and contaminates precisely the
dot-com results the corpus exists to produce.

Sharadar and EODHD are the two candidates that could supply pre-2009
fundamental *values*, and **neither has been tested.** The test is stated in
advance, before any data is seen, so it cannot be softened after the fact:

**Acceptance rule, fixed in advance:** a vendor date field whose distribution
clusters on fiscal quarter-ends rather than on plausible filing dates is **not**
point-in-time, whatever the vendor calls it. Compare against EDGAR's `filed` for
the same accession, by year, across 1998–2008.

Meanwhile, EDGAR supplies the *dates* authoritatively from **1994 Q3** for free,
which is what makes deferring pre-2009 *values* survivable — see
`RESEARCH_01_DATA_CONTRACT.md` §7.

---

## 5. Recommendation

1. **Do not purchase anything yet.** No vendor in §2 has been probed, and the
   acceptance rules in `KIBOT_DATA_PROBE.md` were written before any data was
   seen precisely so that a purchase decision could be measured rather than
   argued.
2. **Adopt SEC EDGAR now.** Free, public domain, and — via Form 25/15
   enumeration — the instrument that *measures* whether any price vendor's
   delisted roster is complete. This work is useful under every outcome and
   depends on no purchase.
3. **When a probe does run, run it on Sharadar first.** It has the strongest
   claimed feature set for the fundamentals spine — `permaticker`, the
   as-reported/restated split, `DATEKEY` — and it has never been tested at all.
   EODHD second, on the same basis.
4. **Kibot stays eliminated on price** unless the budget changes, in which case
   it is reconsidered unchanged — its question 1 (does *tradable* include
   delisted?) still gates any purchase at any price.
5. **Defer pre-2009 fundamental *values*.** EDGAR supplies the filing *dates*
   authoritatively from 1994 Q3 for free, which is what makes deferring the
   values survivable (`RESEARCH_01_DATA_CONTRACT.md` §7).

---

## Sources

- **Kibot pricing and EOD subscription pages** — one-time archive prices
  ($600–$9,000 by universe and interval; $990/$2,400/$4,800 bundles), the
  ~$14/month subscription, 64-year daily history, adjustment bases, delisted
  rosters — **USER-VERIFIED, primary source, blocked in this session**
  (`kibot.com` returned `EGRESS_BLOCKED` here)
- [quantrocket-client `fundamental.py`](https://raw.githubusercontent.com/quantrocket-llc/quantrocket-client/master/quantrocket/fundamental.py) — read directly; the only VERIFIED vendor-contract source read *in this session*
- [python-edgar — quarterly index files](https://github.com/edgarminers/python-edgar)
- [Notre Dame SRAF — SEC/EDGAR master index data](https://sraf.nd.edu/sec-edgar-data/master-index-data/)
