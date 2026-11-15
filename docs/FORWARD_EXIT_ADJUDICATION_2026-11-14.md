# Forward-capture exit adjudication — ELME and MGPI, 2026-11-14

**Status: PARTIAL. One ticker adjudicated against filing-index evidence; one
adjudicated as NOT an exit. Neither ticker's symbol↔CIK mapping was confirmed
against a filing document, because `www.sec.gov` refused every request of this
session. Read §1 before reading anything else in this file.**

Written 2026-11-14 by an automated session. Nothing in this document is
`MANUAL_VERIFIED`; see §1.3.

---

## 0. The question this document was asked

TradeIt's nightly forward-capture agent covers 195 symbols. Two stopped
printing daily bars while roughly 193 peers kept printing on the same asks.
The capture record holds only `ok` and `empty` statuses — no vendor errors — so
the silence is not a refusal and not a market-wide gap.

The silence dates below are **taken from the task brief that commissioned this
document and were not independently measured by this session.** The capture
database is not present in this working copy, so they are recorded here as
*reported*, not as *verified*:

| ticker | last printed session | silent sessions reported |
|---|---|---|
| ELME | 2026-11-05 | 2026-11-06, 11-09, 11-10, 11-11, 11-12, 11-13 |
| MGPI | 2026-11-10 | 2026-11-11, 11-12, 11-13 |

A ticker change, an exchange move, a trading halt, an acquisition, a
going-private transaction and a bankruptcy all produce this same signature in a
price feed. The job was to establish which, from filings, and to say
`UNEXPLAINED` where the filings do not reach.

### 0.1 Addendum 2026-11-14 — the capture dates above are now measured

Added by the commissioning session, which holds the capture record this
document could not see. **§4 item 10 is closed**: the dates in the table above
are no longer *reported*, they are measured, and they are unchanged.

Measured directly against `forward.sqlite` (`forward_observations`), not read
from the `tradeit status` summary, because the number that summary prints was
itself the subject of a defect fix on 2026-10-26 and taking it on trust would
repeat the error:

| measurement | value |
|---|---|
| rows in the record | 4,591 |
| symbol-sessions at/after the 2026-10-23 17:50 cutoff | 3,269 |
| distinct sessions | 17 |
| distinct symbols | 195 |
| silent symbol-sessions | 9 |
| most recent ask | 2026-11-14 03:11 |

The silent nine are exactly the nine in the table above — ELME ×6, MGPI ×3 —
and the arithmetic closes twice independently: `149 + 195×16 = 3,269` for the
symbol-sessions, and `4,439 + 152 = 4,591` for the asks. The status vocabulary
across all 4,591 rows is `ok` (4,576) and `empty` (15); the four `empty` rows
outside the nine are the pre-cutoff `ABAX`/`AUTO` rows that
[`ops/UNIVERSE.md`](../ops/UNIVERSE.md) already records as **not** exits.

**What this addendum does not touch:** every `UNRESOLVED` in §4 items 1–9 and
11 stands. The capture dates were the one thing this session could settle, and
settling them changes nothing about the filings, which remain unread.

---

## 1. What this session could and could not reach — read this first

### 1.1 `www.sec.gov` refused this session entirely

Every request to `www.sec.gov` returned **HTTP 403**, under three different
generic User-Agent strings (`TradeIt Research Agent`,
`TradeIt Research Agent/1.0`,
`TradeIt Research Agent (compliance: see sec.gov fair access)`), across at
least seven requests spread over several minutes, against three different path
families (`/files/`, `/cgi-bin/browse-edgar`, `/Archives/edgar/data/`).

The refusal body is quoted verbatim, stripped of markup and whitespace-collapsed:

> **Your Request Originates from an Undeclared Automated Tool**
>
> To allow for equitable access to all users, SEC reserves the right to limit
> requests originating from undeclared automated tools. Your request has been
> identified as part of a network of automated tools outside of the acceptable
> policy and will be managed until action is taken to declare your traffic.
> Please declare your traffic by updating your user agent to include company
> specific information.

(`Reference ID: 0.6418d017.1794639246.2bf6fb91`, captured 2026-11-14. A second
refusal body for `/files/company_tickers.json` was titled
`SEC.gov | Request Rate Threshold Exceeded`,
`Reference ID: 0.15172117.1794639383.4d36d37b`. The two titles are different
renderings of the same 403 and are recorded exactly as received rather than
treated as one.)

**This session did not supply a contact address to get past it, and that was
deliberate.** The commissioning brief forbade putting the repository owner's
email or any personal contact detail into a request header, URL or payload, and
directed that a refusal of a generic name be recorded rather than worked
around. Inventing a plausible-looking contact address would have been a
fabricated provenance fact sent to a federal regulator. The refusal is recorded
instead.

**The consequence is specific and it is the main limitation of this document:
no filing document was read.** Not one `.htm`, not one `.xml`, not one SGML
header. Everything below rests on EDGAR's own structured index.

### 1.2 `data.sec.gov` was reachable and is the sole evidence base

`https://data.sec.gov/submissions/CIK##########.json` returned HTTP 200 under
the same generic User-Agent. These files are EDGAR's authoritative submission
index for a CIK: form type, filing date, acceptance datetime, accession number,
Exchange Act `act`, SEC file number, period of report, 8-K item list and
primary document filename, plus entity-level name, former names, ticker and
exchange arrays.

Three endpoints were probed and found unavailable:
`https://data.sec.gov/files/company_tickers.json` (**404**, `NoSuchKey`), and
`https://data.sec.gov/api/xbrl/companyconcept/CIK0000835011/dei/TradingSymbol.json`
(**404**, `NoSuchKey` — the XBRL company APIs carry numeric facts only, so the
cover-page `dei:TradingSymbol` of a filing is not retrievable this way; the
control probe
`.../dei/EntityCommonStockSharesOutstanding.json` returned **200**, confirming
the endpoint family works and that the gap is the concept, not the host).

Request log, in order, all with `User-Agent: TradeIt Research Agent`, all well
under 10 requests/second:

| endpoint | result |
|---|---|
| `www.sec.gov/files/company_tickers.json` | 403 ×2 (plus 2 UA variants) |
| `www.sec.gov/cgi-bin/browse-edgar?...CIK=0000835011&type=8-K...` | 403 |
| `www.sec.gov/Archives/edgar/data/835011/` | 403 |
| `www.sec.gov/Archives/edgar/data/835011/index.json` | 403 |
| `data.sec.gov/files/company_tickers.json` | 404 |
| `data.sec.gov/submissions/CIK0000835011.json` | 200 |
| `data.sec.gov/submissions/CIK0000104894.json` | 200 |
| `data.sec.gov/submissions/CIK0000876661.json` | 200 |
| `data.sec.gov/api/xbrl/companyconcept/CIK0000835011/dei/TradingSymbol.json` | 404 |
| `data.sec.gov/api/xbrl/companyconcept/CIK0000835011/dei/EntityCommonStockSharesOutstanding.json` | 200 |

### 1.3 The grade that travels with every fact below

**`EDGAR-INDEX-DERIVED / NO FILING DOCUMENT READ / NOT MANUAL_VERIFIED`.**

- *`EDGAR-INDEX-DERIVED`* — the fact comes from EDGAR's structured submission
  index, not from the text or XML of a filing. The index is the Commission's
  own record of what was filed and is strong evidence that a filing of a given
  form type, date and accession **exists**. It is not evidence of what that
  filing **says**.
- *`NO FILING DOCUMENT READ`* — §1.1. Every claim about a filing's contents
  below is absent rather than weak: where this document does not state a
  filing's content, that is because it was not read, and the absence must never
  later be read as a finding of no content.
- *`NOT MANUAL_VERIFIED`* — `MANUAL_VERIFIED` means a person read the filing.
  A program reading it does not satisfy the grade, and this session read nothing
  anyway. `src/tradeit/edgar/acquire.py` enforces that boundary structurally.
  **Nothing in this file may be promoted to `MANUAL_VERIFIED` on the strength of
  this file.**

### 1.4 How the candidate CIKs were generated — and why that is a weakness

The commissioned method was: generate candidate CIKs from
`https://www.sec.gov/files/company_tickers.json`, then confirm each against an
actual filing naming the ticker. **Neither half was possible.** The reference
file was refused (§1.1) and no filing was readable.

What happened instead, stated exactly so it is not mistaken for the method:

- **MGPI → CIK 0000835011** was proposed by the prior session named in the
  brief. This session **tested** the proposal rather than adopting it.
- **ELME → CIK 0000104894** was proposed from **this model's prior knowledge**.
  That is a guess in the sense the project's rules forbid, and it is labelled as
  one here. It was likewise tested, not adopted.

The test in both cases was the same: fetch that CIK's submissions index and ask
whether EDGAR's own entity record for it is consistent with the ticker. Both
passed — see §2.1 and §3.1. **A passing consistency test is not the confirmation
the method asked for.** It narrows the candidate; it does not establish the
mapping. See `docs/EDGAR_DELISTING_DENOMINATOR.md` §4: a reference file never
carries a mapping by itself, and EDGAR's `tickers` array in the submissions
index is a reference artefact of exactly that class. It is not a filing someone
read.

**Both mappings below are therefore recorded as `CANDIDATE_CORROBORATED`, not
as confirmed.** This is the single thing a later reader is most likely to
over-read, which is why it is stated three times.

---

## 2. ELME

### 2.1 Identity

| field | value | source |
|---|---|---|
| candidate CIK | **0000104894** | model prior knowledge (§1.4) |
| EDGAR `name` | `Elme Communities` | `data.sec.gov/submissions/CIK0000104894.json` |
| EDGAR `formerNames` | `WASHINGTON REAL ESTATE INVESTMENT TRUST`, `from` `1994-11-14T05:00:00.000Z`, `to` `2022-10-03T04:00:00.000Z` | ibid. |
| EDGAR `tickers` | `["ELMES", "ELME"]` | ibid. |
| EDGAR `exchanges` | `["OTC", "NYSE"]` | ibid. |
| SEC file number on every 2026 filing below | `001-06622` | ibid. |
| SIC / state / EIN | `6798` *Real Estate Investment Trusts* / `MD` / `530261100` | ibid. |

**Mapping grade: `CANDIDATE_CORROBORATED`.** EDGAR's entity record for CIK
0000104894 lists `ELME` among its tickers and names an entity whose filings all
carry one Section 12(b) file number. **The filing + accession that confirms the
ticker↔CIK mapping — which this document was asked to supply — is
`UNRESOLVED`.** No filing naming the symbol was readable (§1.1).

The `tickers` and `exchanges` arrays are two parallel JSON lists. Reading them
as positionally paired — `ELMES`↔`OTC`, `ELME`↔`NYSE` — is an **assumption
about an undocumented file format**, not a documented EDGAR contract. It is
noted because it is the only thing in the whole record that touches the prior
session's `ELMES`/OTC claim, and it is not strong enough to settle it.

### 2.2 Every filing indexed since 2026-09-01

Form types are reproduced exactly as EDGAR renders them. `25-NSE` is not `25`.
`15-12G` is not `15`, and is not `15-12B`.

| filed | form | accession | act | file no. | period | 8-K items | primary document | acceptance (UTC) |
|---|---|---|---|---|---|---|---|---|
| 2026-09-18 | `8-K` | `0000104894-26-000112` | 34 | 001-06622 | 2026-09-14 | `1.02,2.01,9.01` | `elme-20260914.htm` | 2026-09-18T20:22:54.000Z |
| 2026-09-29 | `8-K` | `0000104894-26-000117` | 34 | 001-06622 | 2026-09-28 | `2.01,3.01,5.02,7.01,8.01,9.01` | `elme-20260928.htm` | 2026-09-29T20:48:51.000Z |
| 2026-10-26 | `8-K` | `0001104659-26-118561` | 34 | 001-06622 | 2026-10-23 | `3.01,8.01,9.01` | `tm2628252d1_8k.htm` | 2026-10-26T10:02:20.000Z |
| 2026-10-26 | `25-NSE` | `0000876661-26-000862` | 34 | 001-06622 | — | — | `xslF25X02/primary_doc.xml` | 2026-10-26T15:44:21.000Z |
| 2026-11-02 | `8-K` | `0001104659-26-121321` | 34 | 001-06622 | 2026-10-28 | `5.02,8.01` | `tm2628645d1_8k.htm` | 2026-11-02T21:05:37.000Z |
| 2026-11-06 | `8-K` | `0001104659-26-123680` | 34 | 001-06622 | 2026-11-06 | `1.01,5.02,9.01` | `tm2629090d1_8k.htm` | 2026-11-06T15:20:58.000Z |
| 2026-11-06 | `15-12G` | `0001104659-26-123774` | 34 | 001-06622 | — | — | `tm2629092d1_1512g.htm` | 2026-11-06T19:00:19.000Z |

That is the complete set. The submissions index `recent` array for this CIK
holds 1,000 entries spanning 2011-04-28 to 2026-11-06, so the 2026-09-01 window
is fully covered and nothing was truncated into the older shard.

**Filings outside the asked window, flagged as context and not as findings:**
`PREM14A` `0001104659-25-087888` filed 2025-09-05; `DEFM14A`
`0001104659-25-092947` filed 2025-09-24; `8-K` `0001104659-25-110656` filed
2025-11-13, items `1.01,1.02,2.01,2.03,5.01,7.01,9.01`. **No `S-4` appears
anywhere in this CIK's 1,000-entry recent array.** The merger-proxy-shaped
2025 record is roughly a year before the silence and this document draws no
line between them.

### 2.3 The `25-NSE` filer, corroborated twice

Accession `0000876661-26-000862` appears in **two independent EDGAR index
entries**: under CIK 0000104894 (Elme Communities, as a subject) and under CIK
0000876661 (as a filer). `data.sec.gov/submissions/CIK0000876661.json` gives
that CIK's `name` as **`NEW YORK STOCK EXCHANGE LLC`**, with `formerNames`
`NEW YORK STOCK EXCHANGE INC` (`to` `2010-05-21T04:00:00.000Z`). Both entries
agree on form `25-NSE`, filing date `2026-10-26` and acceptance
`2026-10-26T15:44:21.000Z`.

This is the same filer CIK that filed the Lehman Brothers Holdings `25-NSE`
recorded in `docs/research/control_identity_evidence.json` (accession
`0000876661-08-000395`), which is a consistency check on this reading of the
accession-prefix convention and nothing more.

### 2.4 What is established

Narrowly, and only this:

1. On **2026-10-26** a **`25-NSE`** — the EDGAR form type for the SEC's Form 25,
   *Notification of Removal from Listing and/or Registration under Section
   12(b)*, filed under Rule 12d2-2 by a national securities exchange — was filed
   with the Commission under accession `0000876661-26-000862`, by a filer whose
   EDGAR CIK 0000876661 is named `NEW YORK STOCK EXCHANGE LLC`, and it is
   indexed against CIK 0000104894 under SEC file number `001-06622`. **This fact
   is the filing of the notification and nothing more.**
2. On **2026-10-26** an **`8-K`** was filed under accession
   `0001104659-26-118561` for a period of **2026-10-23**, and EDGAR's index
   records its item list as including **`3.01`** — the item number for *Notice
   of Delisting or Failure to Satisfy a Continued Listing Rule or Standard;
   Transfer of Listing*.
3. On **2026-09-29** an **`8-K`** was filed under accession
   `0000104894-26-000117` for a period of **2026-09-28** whose indexed item list
   includes **`2.01`** (*Completion of Acquisition or Disposition of Assets*)
   **and** **`3.01`**.
4. On **2026-11-06** a **`15-12G`** was filed under accession
   `0001104659-26-123774`, indexed against CIK 0000104894 under file number
   `001-06622`.
5. Four further `8-K` filings exist on the dates and accessions in §2.2.

### 2.5 What is NOT established

- **No effective delisting date.** 2026-10-26 is the EDGAR filing date of the
  `25-NSE`. It is not an effective delisting date, not a last trading session,
  and not the SGML `EFFECTIVENESS DATE` — that header field was not read, and no
  body-stated removal date was read. The `25-NSE` XML's `ruleProvision`,
  `exchange` and security-class fields were **all** unread.
- **No cessation of trading.** *Removal from listing ends a listing and nothing
  else.* A class removed from one exchange can continue to be quoted elsewhere;
  `IPET` and `TGLO` in `docs/research/control_identity_evidence.json` are both
  cases where the security demonstrably survived its delisting. EDGAR's own
  entity record for this CIK still carries `ELMES` and `OTC` — which is a
  positive reason to suspect the security survived, not a reason to assume it
  did not.
- **Nothing about what any `8-K` says.** The `3.01` item numbers are EDGAR index
  metadata. Whether the 2026-10-26 `3.01` reports a listing-standard failure, a
  voluntary delisting or a transfer of listing is **unread**. Item `2.01` on
  2026-09-18 and 2026-09-29 may be a property disposition, an acquisition, or a
  whole-company transaction; **unread**.
- **The `15-12G`'s contents.** Which section it certifies under, which class it
  covers, the share-of-record count asserted, and the date reporting
  obligations suspend — **all unread**. Note that a Form 15 suspends or
  terminates *reporting*; it does not stop a security trading, and it is not a
  delisting.
- **No deregistration, extinguishment, dissolution or succession is
  established.** The `25-NSE` does not establish termination or withdrawal of
  registration under any section, and the `15-12G`'s effect was not read.
- **The price-feed silence is NOT explained.** The `15-12G` was accepted
  2026-11-06T19:00:19Z — after that day's US close — and 2026-11-06 is the first
  reported silent session. **That is a coincidence of calendar dates and
  nothing more. A Form 15 filing does not cause a price feed to return zero
  rows.** The `25-NSE`, eight sessions before the silence, did not stop the feed
  either: ELME is reported as having printed bars through 2026-11-05. **Nothing
  in the EDGAR record states what changed on or about 2026-11-05/06.**
- **The prior session's `ELMES` splice claim is neither confirmed nor refuted.**
  Whether the forward record's ELME tail after 2026-10-26 splices across a NYSE
  → OTC/`ELMES` break cannot be settled from filings; it is a question about the
  vendor's symbol continuity, and the only EDGAR-side input is the positional
  array-pairing assumption in §2.1, which is too weak to carry it.

### 2.6 Verdict

> ### `EXIT_EVIDENCED`
>
> A Section 12(b) listing-removal notification filed by NEW YORK STOCK EXCHANGE
> LLC (`25-NSE`, 2026-10-26) **and** a `15-12G` (2026-11-06) are both on the
> record against CIK 0000104894, accompanied by two `8-K` filings whose indexed
> items include `3.01`. An Exchange Act **listing** exit event is evidenced by
> the existence of those filings.
>
> **Scope of the verdict, which must travel with it:** it says an exit event is
> evidenced. It does **not** supply an effective date, does **not** establish
> that trading ceased, does **not** establish that the security was
> extinguished, and does **not** explain the price-feed silence. EDGAR still
> carries `ELMES`/`OTC` for this CIK, so the security may well survive
> off-exchange — treating this verdict as a death certificate would reproduce
> precisely the `IPET`/`TGLO` error the control file exists to prevent.

---

## 3. MGPI

### 3.1 Identity

| field | value | source |
|---|---|---|
| candidate CIK | **0000835011** | proposed by prior session (§1.4) |
| EDGAR `name` | `SEMPREON, INC` | `data.sec.gov/submissions/CIK0000835011.json` |
| EDGAR `formerNames` | `MGP INGREDIENTS INC`, `from` `2002-10-11T04:00:00.000Z`, `to` **`2026-10-28T04:00:00.000Z`**; and `MIDWEST GRAIN PRODUCTS INC`, `from` `1994-09-09T04:00:00.000Z`, `to` `2002-10-10T04:00:00.000Z` | ibid. |
| EDGAR `tickers` | `["CASK", "MGPI"]` | ibid. |
| EDGAR `exchanges` | `["Nasdaq", "Nasdaq"]` | ibid. |
| SEC file number on every Exchange Act filing below | `000-17196` | ibid. |
| SIC / state / EIN | `5180` *Wholesale-Beer, Wine & Distilled Alcoholic Beverages* / `KS` / `480531200` | ibid. |

**Mapping grade: `CANDIDATE_CORROBORATED`.** The prior session's proposed CIK
survives its test: EDGAR's entity record for 0000835011 lists **both** `MGPI`
and `CASK`, and its former name `MGP INGREDIENTS INC` is the name the symbol
`MGPI` would be expected to belong to. **The filing + accession that confirms
the ticker↔CIK mapping is `UNRESOLVED`** — no filing naming either symbol was
readable (§1.1). Note the near miss: this CIK's `144` and `4` filings carry
`issuerTradingSymbol` in their XML and would have settled it in one request;
those documents live on `www.sec.gov`.

Observe that `exchanges` here is `["Nasdaq", "Nasdaq"]` — two entries, same
string. Whatever pairing rule applies, **EDGAR does not record a venue change
for this CIK**, which is a meaningful negative given that an exchange move is
one of the hypotheses in play.

### 3.2 Every filing indexed since 2026-09-01

| filed | form | accession | act | file no. | period | 8-K items | primary document | acceptance (UTC) |
|---|---|---|---|---|---|---|---|---|
| 2026-09-15 | `144` | `0001140361-26-036586` | 33 | 000-17196 | — | — | `xsl144X01/primary_doc.xml` | 2026-09-15T14:13:20.000Z |
| 2026-09-15 | `144/A` | `0001140361-26-036591` | 33 | 000-17196 | — | — | `xsl144X01/primary_doc.xml` | 2026-09-15T14:25:29.000Z |
| 2026-09-18 | `4` | `0001140361-26-037155` | — | — | 2026-09-16 | — | `xslF345X06/form4.xml` | 2026-09-18T23:27:59.000Z |
| 2026-10-01 | `4` | `0000835011-26-000110` | — | — | 2026-10-01 | — | `xslF345X06/wk-form4_1790889911.xml` | 2026-10-01T21:25:13.000Z |
| 2026-10-15 | `8-K` | `0000835011-26-000115` | 34 | 000-17196 | 2026-10-14 | `5.03,7.01,9.01` | `mgpi-20261014.htm` | 2026-10-15T11:02:37.000Z |
| 2026-10-28 | `8-K` | `0000835011-26-000122` | 34 | 000-17196 | 2026-10-28 | `2.02,9.01` | `mgpi-20261028.htm` | 2026-10-28T11:31:17.000Z |
| 2026-10-28 | `10-Q` | `0000835011-26-000124` | 34 | 000-17196 | 2026-09-30 | — | `mgpi-20260930.htm` | 2026-10-28T11:36:27.000Z |
| 2026-11-06 | `144` | `0001140361-26-042372` | 33 | 000-17196 | — | — | `xsl144X01/primary_doc.xml` | 2026-11-06T17:34:22.000Z |
| 2026-11-12 | `4` | `0001140361-26-043447` | — | — | 2026-11-10 | — | `xslF345X06/form4.xml` | 2026-11-12T21:30:20.000Z |

That is the complete set. The `recent` array for this CIK holds 1,000 entries
spanning 2017-03-15 to 2026-11-12, so the window is fully covered.

### 3.3 The decisive negatives

Across that entire 1,000-entry span — 2017-03-15 to 2026-11-12, covering the
asked window and nine and a half years before it:

- **No `25`. No `25-NSE`.** The form does not occur once.
- **No `15`, `15-12B`, `15-12G` or `15-15D`.** None occurs once.
- **No `8-K` carrying item `3.01`** (delisting / continued-listing failure /
  transfer of listing). None.
- **No `8-K` carrying item `1.03`** (bankruptcy or receivership). None.
- **No `S-4`. No `DEFM14A`. No `PREM14A`.** None occurs in the array. The only
  `8-K` filings carrying item `2.01` in the whole span are **2021-04-01**
  (`0001628280-21-006351`) and **2017-06-27** (`0001628280-17-006742`), both
  years before the silence.
- The **SEC file number is unchanged at `000-17196`** on every Exchange Act
  filing in the window, before and after the silence began.

These are statements about EDGAR's index for CIK 0000835011. They are strong
precisely because the exit-shaped forms are *index-level* facts: a `25-NSE` or a
`15-12G` filed against this CIK would appear here exactly as ELME's do in §2.2.
Their absence is therefore evidence of absence in a way that an unread
document's silence never is.

### 3.4 What is established

1. **The registrant CIK is unchanged.** CIK 0000835011 filed a `10-Q`
   (`0000835011-26-000124`, period 2026-09-30) on 2026-10-28, a `144` on
   2026-11-06, and a `4` on 2026-11-12 for a transaction dated **2026-11-10** —
   the last session MGPI is reported to have printed. **The registrant is an
   active Exchange Act filer across and after the entire silent window.**
2. **EDGAR's conformed entity name for this CIK changed.** `MGP INGREDIENTS
   INC` carries a `formerNames` `to` timestamp of **`2026-10-28T04:00:00.000Z`**
   and the current `name` is **`SEMPREON, INC`**.
3. **An `8-K` reporting item `5.03`** — the item number for *Amendments to
   Articles of Incorporation or Bylaws; Change in Fiscal Year* — was filed
   **2026-10-15** (`0000835011-26-000115`) for a period of **2026-10-14**.
4. **No listing-removal or deregistration filing exists** against this CIK
   (§3.3).
5. EDGAR's reference metadata for this CIK carries **both** `MGPI` and `CASK`,
   both against the string `Nasdaq`.

### 3.5 What is NOT established

- **That the trading symbol changed.** This is the pivot of the whole question
  and it is **not evidenced**. The `tickers` array carrying `CASK` is a
  reference artefact; by `EDGAR_DELISTING_DENOMINATOR.md` §4 it never carries a
  mapping by itself, and it is **undated** — it states neither when `CASK`
  began nor that `MGPI` ended. No filing naming either symbol was readable.
- **That `CASK` is the successor of `MGPI` rather than a second concurrent
  class.** EDGAR lists two tickers against two identical `Nasdaq` strings and
  says nothing about the relationship between them. This issuer's own history
  shows EDGAR retaining an entry long after it has been superseded.
- **Any effective date for a symbol change.** `2026-10-28T04:00:00.000Z` is the
  timestamp on which **EDGAR's conformed company name** transitions. It is a
  metadata-maintenance timestamp, **not** a corporate effective date and **not**
  a symbol-change date.
- **What the `5.03` `8-K` says.** Item `5.03` covers charter amendments, bylaw
  amendments *and* fiscal-year changes. Which of those it reports — and whether
  it mentions a name change or a symbol at all — is **unread**.
- **The price-feed silence is NOT explained, and there is a nine-session hole in
  the obvious story.** If the symbol had changed when EDGAR's name changed, the
  feed should have gone quiet on or about **2026-10-28**. It is reported to have
  kept printing MGPI bars through **2026-11-10** — nine sessions later — and
  then stopped. **Nothing in the EDGAR record accounts for that gap**, and it is
  the single strongest reason not to write "ticker change" into the corpus from
  this evidence. A reader who skips this paragraph will record a fact this
  document does not support.

### 3.6 Verdict

> ### `OTHER_EVENT_EVIDENCED` — and **NOT** an exit
>
> **The exit hypothesis is affirmatively refuted, not merely unsupported.**
> There is no Form 25 or 25-NSE, no Form 15 in any variant, no `8-K` item
> `3.01` or `1.03`, no `S-4` or merger proxy, no change of SEC file number — and
> the registrant filed a `10-Q` on 2026-10-28 and a Form `4` on 2026-11-12 for a
> transaction dated 2026-11-10. A registrant that is still filing periodic
> reports and insider forms through the silent window has not exited.
>
> **What is positively evidenced is an issuer name change** — `MGP INGREDIENTS
> INC` → `SEMPREON, INC`, EDGAR transition timestamp `2026-10-28T04:00:00.000Z`,
> alongside an `8-K` reporting item `5.03` filed 2026-10-15 for period
> 2026-10-14.
>
> **`TICKER_CHANGE_EVIDENCED` is deliberately withheld.** The brief's test was:
> *if the registrant CIK is unchanged **and** only the trading symbol changed,
> that is a ticker change and not an exit.* The first clause is established.
> **The second is not** — no filing naming a symbol was readable, EDGAR's
> `tickers` array is an undated reference artefact that cannot carry a mapping,
> and the nine sessions between the name change and the last MGPI print (§3.5)
> are unexplained. A ticker change is the only hypothesis presently consistent
> with the record, and *consistent with* is not *established by*.
>
> **Operationally, for the forward-capture record, this is unambiguous: do
> not record a survivorship exit for MGPI.** Classify it as a **false positive
> for exit**, pending a filing read.

---

## 4. What this does not settle

Every field below was reached for and not reconciled. Each is named
individually with the reason.

1. **`ticker ↔ CIK` confirming filing + accession, `ELME`.** `UNRESOLVED`. The
   deliverable asked for the filing and accession that confirms the mapping;
   `www.sec.gov` returned 403 to every request, so no filing naming `ELME` was
   read. The mapping rests on EDGAR reference metadata plus a model-prior
   candidate (§1.4), which is the weakest provenance in this document.
2. **`ticker ↔ CIK` confirming filing + accession, `MGPI`.** `UNRESOLVED`. Same
   cause. The `144` and `4` XML instances under this CIK carry
   `issuerTradingSymbol` and would have settled it; they are on the refused
   host.
3. **Effective delisting date for ELME.** `UNRESOLVED`. The `25-NSE` filing date
   2026-10-26 is a `header_field` date and is not an effective date. The SGML
   `EFFECTIVENESS DATE` was not read; no body-stated removal date was read.
4. **ELME `25-NSE` exchange string, security class and `ruleProvision`.**
   `UNRESOLVED`. The XML instance was not fetched. This matters more than it
   looks: venue strings are recorded verbatim and never normalised, and the only
   venue token available here is EDGAR's reference rendering `NYSE`, which is
   **not** the filing's own string and must not be substituted for it. The LEH
   record's `NEW YORK STOCK EXCHANGE INC` came from the instance; nothing
   equivalent exists for ELME.
5. **ELME `15-12G` section, class, holder count and suspension date.**
   `UNRESOLVED`. Document not read. Whether it covers the same class as the
   `25-NSE` is open.
6. **Contents of every `8-K` cited, both tickers.** `UNRESOLVED`. All item
   numbers quoted in this document are EDGAR index metadata derived from the
   filer's header declaration, **not** from body text. Specifically open: what
   ELME's 2026-10-26 item `3.01` reports; what ELME's 2026-09-18 and 2026-09-29
   item `2.01` disposed of or acquired; what MGPI's 2026-10-15 item `5.03`
   amended.
7. **`CASK`'s start date and whether `MGPI` ended.** `UNRESOLVED`. EDGAR's
   `tickers` array is undated and non-ordinal. `2026-10-28T04:00:00.000Z` is a
   name-record timestamp, not a symbol date.
8. **The `ELMES` ↔ `OTC` and `ELME` ↔ `NYSE` pairing.** `UNRESOLVED`. Derived
   from positional alignment of two parallel JSON arrays — an assumption about
   an undocumented format, not a documented contract. The prior session's claim
   of an ELME tail splicing across a NYSE → OTC/`ELMES` break therefore remains
   **neither confirmed nor refuted**.
9. **The cause of the price-feed silence, both tickers.** `UNEXPLAINED` for
   MGPI outright, and `UNEXPLAINED` for ELME as to timing. For ELME the
   filings evidence an exit event but place it on 2026-10-26, eight sessions
   before the feed stopped; the `15-12G` shares a calendar date with the first
   silent session and has no mechanism by which it could cause the silence. For
   MGPI nothing in the record speaks to 2026-11-10 at all. **The record does
   not explain either silence**, and that is the answer rather than a gap in
   the answer.
10. **The capture record itself.** `UNVERIFIED`. The last-print and silent-session
    dates in §0 come from the commissioning brief. The capture database is not
    in this working copy and this session measured none of them. If any date is
    wrong, §2.5 and §3.5 change.
11. **The 2025 ELME merger-proxy record.** `NOT RECONCILED, DELIBERATELY`. A
    `PREM14A`, a `DEFM14A` and an `8-K` carrying items `2.01` and `5.01` sit in
    late 2025, roughly a year before the silence. Whether they connect to the
    2026 exit filings was not investigated and no line is drawn between them
    here.

**One thing this document does settle, and it is the operationally useful
half:** the two silences have different shapes. ELME's registrant has an
exit-shaped filing record; MGPI's registrant has an affirmatively
non-exit-shaped one and is still filing. Whatever the forward-capture agent
does next, **MGPI must not be written into the corpus as a survivorship
exit.**

---

## 5. Reproducing this

```bash
UA='TradeIt Research Agent'
curl -sS -A "$UA" https://data.sec.gov/submissions/CIK0000104894.json   # ELME
curl -sS -A "$UA" https://data.sec.gov/submissions/CIK0000835011.json   # MGPI/CASK
curl -sS -A "$UA" https://data.sec.gov/submissions/CIK0000876661.json   # 25-NSE filer
```

`www.sec.gov` returns HTTP 403 to this User-Agent (§1.1). Lifting the
limitations in §4 requires reading filing documents from
`www.sec.gov/Archives/`, which requires a declared User-Agent carrying a
contact address. **That is an operator decision, not an agent one** — it sends
a contact detail to an outside party, and this session was instructed not to
supply one. The items in §4 numbered 1–6 would all be answerable in roughly ten
further requests once it is.

Nothing here is `MANUAL_VERIFIED`. Promotion requires a person to read the
filings.

---

## 6. Filings read — items closed and items still open

**Added 2026-11-15 by a later automated session.** §§0–5 are left exactly as
written: they are the historical record of what was knowable **without** filing
access, and that record is worth keeping intact because §1's refusal was
correct behaviour. This section does not edit a single verdict in place. Where
a filing changes a verdict in §2.6 or §3.6, the correction is stated here in
§6.9 and cross-referenced from the open list in §6.10.

### 6.0 What changed, and the grade that travels with this section

`www.sec.gov` is now reachable. The repository owner set **`EDGAR_USER_AGENT`**
as an environment variable on this environment, which is the mechanism
`CLAUDE.md` prescribes for SEC access, and it carries a declared contact
address. The variable was checked before use and found set, non-empty, 39
bytes, a single line with no newline or carriage return and no surrounding
whitespace. **Its value is not recorded here, was not printed, logged or
committed, and was passed only by variable reference (`-A "$EDGAR_USER_AGENT"`).
What this document records is that a contact was declared — never the address.**

One probe was made first, exactly as §1.1 would have required:
`https://www.sec.gov/files/company_tickers.json` returned **HTTP 200**
(796,319 bytes). The 403 of §1.1 is therefore explained by the missing
declaration and nothing else; **§1.1 stands as an accurate record of that
session and is not retracted.** Twenty-seven requests were made in total
across `www.sec.gov` and `data.sec.gov`, issued sequentially with a deliberate
pause between each, far below ten per second.

**Grade for every finding in §6: `AGENT-READ-FILING` — filing documents were
read by a program, not by a person.**

- *`AGENT-READ-FILING`* — the fact comes from the text or XML of the filing
  named beside it, read in full, with the operative language quoted verbatim
  below. This is strictly stronger than §1.3's `EDGAR-INDEX-DERIVED`: it is
  evidence of what the filing **says**, not only that it exists.
- *Still **NOT** `MANUAL_VERIFIED`.* `MANUAL_VERIFIED` means a *person* read the
  filing. A program reading it does not satisfy the grade however carefully it
  reads, `src/tradeit/edgar/acquire.py` enforces that boundary structurally,
  and running with a declared User-Agent does not change it. **Promotion of
  anything in this section to `MANUAL_VERIFIED` requires a human to open these
  documents.** The accessions and filenames in §6.11 are listed so that a human
  can do exactly that.

### 6.1 Identity: both mappings confirmed against filings — §4 items 1 and 2 **CLOSED**

§1.4 recorded that neither mapping had been confirmed against a filing, that
ELME's candidate CIK came from a model's prior knowledge, and that both were
therefore `CANDIDATE_CORROBORATED` rather than confirmed. **Both candidates are
now confirmed from filings that name the symbol against the CIK.** The
model-prior candidate for ELME was correct; that it was correct does not make
guessing it sound, and §1.4's self-criticism stands.

| mapping | confirming filing | accession | the filing's own words |
|---|---|---|---|
| `ELME` ↔ CIK 0000104894 | `8-K` filed 2026-10-26, cover page, *Securities registered pursuant to Section 12(b) of the Act* | `0001104659-26-118561` | `Shares of Beneficial Interest` / `ELME` / `NYSE` |
| `ELME` ↔ CIK 0000104894 (independent) | `8-K` filed 2026-09-18, same cover table | `0000104894-26-000112` | `Shares of Beneficial Interest` / `ELME` / `NYSE` |
| `ELMES` ↔ CIK 0000104894 | `8-K` filed 2026-11-02, Item 8.01 and cover footnote | `0001104659-26-121321` | see §6.5 |
| `MGPI` ↔ CIK 0000835011 | `4` filed 2026-10-01, XML instance | `0000835011-26-000110` | `<issuerCik>0000835011</issuerCik>`, `<issuerName>MGP INGREDIENTS INC</issuerName>`, `<issuerTradingSymbol>MGPI</issuerTradingSymbol>` |
| `MGPI` ↔ CIK 0000835011 (independent) | `10-Q` filed 2026-10-28, cover page 12(b) table | `0000835011-26-000124` | `Common Stock, no par value` / `MGPI` / `NASDAQ Global Select Market` |
| `CASK` ↔ CIK 0000835011 | `4` filed 2026-11-12, XML instance | `0001140361-26-043447` | `<issuerCik>0000835011</issuerCik>`, `<issuerName>SEMPREON, INC</issuerName>`, `<issuerTradingSymbol>CASK</issuerTradingSymbol>` |

Each of these is a filing in which the registrant (or, for the `4`, the
reporting owner filing against the registrant) writes the symbol and the CIK in
the same instrument. That is the confirmation §1.4 asked for and could not get.

**Two claims carried in §4 are refuted by reading the documents, and they
matter because both were offered as the quick route to the answer:**

1. **§4 item 2 states that the `144` instances "carry `issuerTradingSymbol`".
   They do not.** Both Form `144` instances under this CIK were fetched
   (`0001140361-26-036586` filed 2026-09-15 and `0001140361-26-042372` filed
   2026-11-06) and **neither contains any element whose name contains
   `symbol`** — zero matches, case-insensitive, in each file. The `144`
   identifies the security by `<securitiesClassTitle>COMMON</securitiesClassTitle>`
   and the issuer by `<issuerCik>` and `<issuerName>` only. The `4` does carry
   `issuerTradingSymbol`; the `144` does not. Only half of that claim was true,
   and the half that was false would have produced an `UNRESOLVED` if it had
   been relied on alone.
2. **The ELME `25-NSE` contains no ticker or symbol anywhere.** The complete
   submission text (SGML header plus both documents, 4,987 bytes) has **zero**
   case-insensitive matches for `symbol` or `ticker`. A Form 25 identifies the
   security by class description, not by trading symbol, so it could never have
   confirmed the `ELME` mapping. This is worth recording as a reading rule: *a
   Form 25 is not a ticker-identity source.*

**The reference file, now reachable, is also worth recording — and it is still
only a reference file.** `https://www.sec.gov/files/company_tickers.json`
(10,404 rows, fetched 2026-11-15) contains `CASK` → `835011` /
`'SEMPREON, INC'` and `ELMES` → `104894` / `'Elme Communities'`, and **contains
no row for `MGPI` and no row for `ELME` at all.** That is consistent with
everything below, but by `EDGAR_DELISTING_DENOMINATOR.md` §4 a reference file
never carries a mapping by itself, and this one is undated and current-state
only. It is recorded as corroboration, not as a source. The mappings above rest
on the filings, not on this file.

### 6.2 ELME `25-NSE` read in full — §4 items 3 and 4 **CLOSED**, with a dating conflict the filings do not resolve

Accession `0000876661-26-000862`. The submission holds two documents:
`primary_doc.xml` (type `25-NSE`) and `ruleprovisionnotice.htm` (type
`EX-99.25`).

**§4 item 4 — the instance's own fields, verbatim.** The XML root element is
`notificationOfRemoval`, `schemaVersion` `X0203`:

| field | value exactly as the instance writes it |
|---|---|
| `exchange` / `cik` | `0000876661` |
| `exchange` / `entityName` | **`NEW YORK STOCK EXCHANGE LLC`** |
| `issuer` / `cik` | `0000104894` |
| `issuer` / `entityName` | `Elme Communities` |
| `issuer` / `fileNumber` | `001-06622` |
| `descriptionClassSecurity` | **`Shares of Beneficial Interest`** |
| `ruleProvision` | **`17 CFR 240.12d2-2(b)`** |
| `signatureName` / `signatureTitle` / `signatureDate` | `Anthony Sozzi` / `Analyst, Market Watch` / `2026-10-26` |

**The exchange string is `NEW YORK STOCK EXCHANGE LLC`, and it is not the same
string as the `LEH` control's.** `docs/research/control_identity_evidence.json`
records the exchange field of the Lehman `25-NSE` (`0000876661-08-000395`, same
schemaVersion `X0203`, same filer CIK) as `NEW YORK STOCK EXCHANGE INC`. Same
filer, same form, same schema, **two different venue strings**, because the
entity's conformed name changed in 2010. This is the verbatim-preservation rule
earning its keep: normalising either string to `NYSE` would have destroyed a
real distinction and manufactured a false identity between two records eighteen
years apart. §4 item 4's warning — that EDGAR's reference rendering `NYSE` is
not the filing's own string — was correct, and the substitution it feared would
have been wrong.

**One exit, four renderings of one venue, all of them primary text.** The
registrant's own 12(b) cover tables write `NYSE`; the exchange's instance writes
`NEW YORK STOCK EXCHANGE LLC`; the exchange's narrative exhibit writes
`The New York Stock Exchange ("NYSE" or the "Exchange")`; the 2026-11-02 cover
writes `NYSE*` with an explanatory footnote. All four are recorded as written.

**§4 item 3 — the effective date. The SGML header is not independent, and the
two filing bodies that state a removal date do not agree.** Three date facts,
each quoted exactly:

1. The SGML header reads `EFFECTIVENESS DATE:		20261026` — **identical to
   `FILED AS OF DATE: 20261026`**. It carries no information the filing date
   does not already carry and **is not an effective delisting date.** §4 item 3
   was right to refuse the filing date; it turns out the header field refuses it
   too, by simply repeating it.
2. The `EX-99.25` body states the Exchange's intention to remove the securities

   > on November 6, 2026, pursuant to the provisions of Rule 12d2-2(b)

3. The registrant's own `8-K` of 2026-11-02 (`0001104659-26-121321`), cover-page
   footnote, states

   > On October 26, 2026, the New York Stock Exchange filed a Form 25-NSE with the Securities and Exchange Commission to delist Elme Communities’ shares of beneficial interest from the New York Stock Exchange, which delisting will become effective 10 calendar days after such filing.

   Ten calendar days after 2026-10-26 is **2026-11-05**, one day before the date
   in the Exchange's own notice.

**No single effective delisting date is established, and the discrepancy is
recorded rather than resolved.** The exchange that filed the notification says
November 6; the registrant says ten calendar days after October 26, which is
November 5. Both are primary text in filed documents. Choosing between them
would require a rule neither document states, and inventing one to produce a
tidy single date is precisely the fabrication this corpus refuses. **What the
filings do establish is a one-day window, 2026-11-05 to 2026-11-06, and that
both candidate dates fall on or before the dissolution in §6.3.** For corpus
purposes the listing end is `2026-11-05/2026-11-06, CONFLICTING_PRIMARY_SOURCES`.

**What the `EX-99.25` establishes that the index could not — the delisting was
involuntary, and trading was suspended thirteen days before the Form 25.**
Quoted verbatim:

> The Exchange has determined that the Company’s Shares of Beneficial Interest are no longer suitable for listing based on "abnormally low selling price" levels, pursuant to Section 802.01D of the Listed Company Manual.

> On October 23, 2026, the Exchange determined that the Shares of Beneficial Interest of the Company should be suspended from trading and directed the preparation and filing with the Commission of this application for the removal of the Shares of Beneficial Interest from listing and registration on the NYSE. The Company was notified on October 23, 2026.

> Pursuant to the above authorization, a press release regarding the proposed delisting was issued and posted on the Exchange’s website on October 23, 2026, and trading in the Shares of Beneficial Interest was immediately suspended.

> The Company notified the Exchange on October 23, 2026, that they do not intend to appeal NYSE Regulation’s decision. Consequently, all conditions precedent under SEC Rule 12d2-2(b) to the filing of this application have been satisfied.

**NYSE trading in ELME ceased on 2026-10-23**, three days before the Form 25 was
filed and thirteen days before the earlier of the two candidate effective dates. §2.5's
insistence that *removal from listing ends a listing and nothing else* remains
the correct rule; the cessation of trading here is established by the
suspension, which is a separate fact stated in the same exhibit, not by the
Form 25.

### 6.3 ELME `15-12G` read in full — §4 item 5 **CLOSED**, and it reports a dissolution

Accession `0001104659-26-123774`, document `tm2629092d1_1512g.htm`, signed
`November 6, 2026` by `W. Drew Hammond`, `Executive Vice President, Chief
Financial Officer, Chief Administrative Officer and Treasurer`.

| §4 item 5 asked | the form says |
|---|---|
| the section certified under | Form title: `CERTIFICATION AND NOTICE OF TERMINATION OF REGISTRATION UNDER SECTION 12(g) OF THE SECURITIES EXCHANGE ACT OF 1934 OR SUSPENSION OF DUTY TO FILE REPORTS UNDER SECTIONS 13 AND 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934.` The single box marked `x` is **`Rule 12g-4(a)(1)`**; all five other boxes are `¨` |
| the class covered | **`Common Shares of Beneficial Interest`** |
| other classes with a surviving duty to report | **`None`** |
| the holder count asserted | **`0*`** |
| when reporting obligations suspend | **the form states no date.** Its only date is the signature date, `November 6, 2026` |

**The asterisk is the most important text in this document, and it is the
registrant's own certification:**

> \* Elme Communities dissolved as of 11:59 a.m. on November 6, 2026. At that time, all outstanding common shares of beneficial interest, par value $0.01 per share, of Elme Communities were cancelled.

**Two filing oddities, recorded verbatim and not normalised.** The form's
`Commission File Number:` reads **`000-06622`**, whereas every other 2026 filing
in §2.2 and the `25-NSE` instance itself carry **`001-06622`**. And a registrant
whose class was registered under Section 12(b) terminates under Section 12(g)
with `Rule 12g-4(a)(1)`. Both strings are reproduced as written; this document
does not reconcile them and does not guess at the mechanism.

**Note what this does and does not do, per the rule in the brief.** A Form 15
suspends or terminates *reporting*; it does not stop a security trading and is
not a delisting. The extinguishment of the security here is **not** an effect of
the Form 15 — it is a separate corporate fact (dissolution and cancellation)
that the Form 15 *reports*, and that the `8-K` of the same date independently
documents in §6.4.

### 6.4 The `8-K` contents — §4 item 6 **CLOSED**

#### ELME 2026-10-26, item `3.01` (`0001104659-26-118561`): a **listing-standard failure**, not a transfer and not a voluntary delisting

§4 item 6 asked which of the three it was. It is the first:

> On October 23, 2026, Elme Communities (the “Company”) was notified by the New York Stock Exchange (“NYSE”) that it had commenced proceedings to delist Elme’s common shares of beneficial interest (the “Common Shares”) from the NYSE and immediately suspended trading of the Common Shares. The NYSE exercised its discretionary authority in accordance with Section 802.01D of the NYSE Listed Company Manual due to “abnormally low selling price” levels.

> Given the Company’s previously adopted Plan of Sale and Liquidation, which was approved by the Board of Trustees of the Company on July 30, 2025 and by the shareholders of the Company on October 30, 2025 (the “Plan of Sale and Liquidation”), Elme does not intend to appeal the NYSE’s determination, and, therefore, it is expected that the Common Shares will be delisted.

Its item `8.01` announces the press release covering "the anticipated
commencement of Common Shares trading over-the-counter (“OTC”), and the
Company’s dissolution and related establishment of a liquidating trust".

#### ELME 2026-09-29 (`0000104894-26-000117`) carried a *second*, different item `3.01` — a **voluntary** delisting notice

This is a distinction the index could not show, because both filings index as
`3.01`. Twenty-four days before the NYSE acted, the Company gave notice of a
voluntary delisting of its own:

> On September 29, 2026, in connection with its intended dissolution and the cancellation of its common shares of beneficial interest, par value $0.01 per share (the “Common Shares”), described under Item 8.01 below, the Company, in accordance with the authority granted by the Company’s Board of Trustees (the “Board”) pursuant to the Plan of Sale and Liquidation (as defined below), provided notice to the New York Stock Exchange (the “NYSE”) of its intention to voluntarily delist its Common Shares from the NYSE.

> The Company anticipates filing a Form 25, or Notification of Removal from Listing, with the Securities and Exchange Commission (the “SEC”) and the NYSE relating to the delisting of the Common Shares on October 26, 2026. The Company anticipates the last day of trading of its Common Shares on the NYSE to be November 5, 2026 and that the Company’s share transfer books will be closed as of 4:00 p.m. (Eastern Time) on such date.

Its item `8.01` states the dissolution plan:

> In accordance with the Plan of Sale and Liquidation approved by the Board on July 30, 2025 and by the shareholders of the Company on October 30, 2025 (the “Plan of Sale and Liquidation”), following delisting from the NYSE as described above, the Company intends to terminate its existence by voluntary dissolution effective November 6, 2026 (the “Effective Time”).

> Simultaneously, all of the outstanding Common Shares and certificates representing Common Shares will be automatically cancelled and will thereafter no longer be deemed outstanding.

**So the Form 25 that was actually filed on 2026-10-26 is not the one this `8-K`
anticipated.** The Company planned a voluntary removal; the Exchange pre-empted
it with an involuntary one on the very date the Company had named, under
`17 CFR 240.12d2-2(b)` (exchange-initiated) rather than `12d2-2(c)`
(issuer-initiated). Two item `3.01` filings, two different legal events, one
indexed item number. **An `8-K` item list cannot distinguish a voluntary from an
involuntary delisting** — a reading rule worth carrying forward.

#### ELME 2026-09-18 and 2026-09-29, item `2.01`: **property dispositions**, not a whole-company transaction

- **2026-09-18** (`0000104894-26-000112`): on 2026-09-14 the sale of
  `Riverside Apartments, a 1,222 unit community located in Alexandria, Virginia and related undeveloped land`
  to `FPA Multifamily, LLC` closed `for a purchase price of $250.0 million`. Its
  item `1.02` records that proceeds were used to `cause the repayment in full of all remaining indebtedness, liabilities and other obligations under, and terminated, the Loan Agreement` — the $520.0 million Goldman Sachs Bank USA term loan entered 2025-11-12.
- **2026-09-29** (`0000104894-26-000117`): on 2026-09-28, dispositions of
  `3801 Connecticut Avenue, a 307-unit community located in Washington, DC, for a purchase price of $55.0 million`
  and `The Kenmore, a 371-unit community located in Washington, DC, for a purchase price of $55.0 million`, both to FPA.

These are asset sales inside a liquidation, not an acquisition of the registrant.

#### ELME 2026-11-06 (`0001104659-26-123680`), items `1.01,5.02,9.01`: the dissolution itself, and a cover page that says `None`

Its cover page reads **`Securities registered pursuant to Section 12(b) of the
Act: None.`** — the 12(b) line that carried `ELME`/`NYSE` four filings earlier.
Item 1.01:

> At the close of business (Eastern Time) on November 5, 2026, the share transfer books of Elme Communities (the “Company”) were closed. On November 6, 2026 (the “Effective Date”), prior to the Effective Time (as defined below), in accordance with the Plan of Sale and Liquidation … the Company will transfer its remaining assets and liabilities to Elme Communities Liquidating Trust, a newly-created Maryland common law trust (the “Liquidating Trust”) … The Company has filed a Notice of Termination of Existence with the Maryland Department of Assessments & Taxation (“SDAT”), and pursuant to such notice, which has been accepted by SDAT, the Company will be dissolved as of 11:59 a.m. on the Effective Date (the “Effective Time”).

> Also at the Effective Time, all outstanding common shares of beneficial interest, par value $0.01 per share (the “Common Shares”), of the Company will be cancelled. … The Beneficial Interests will not trade on the New York Stock Exchange or any other exchange or over-the-counter.

**The successor instrument does not trade anywhere.** That is stated in three
separate filings and is the fact that distinguishes this exit from `IPET` and
`TGLO`, where the security demonstrably survived its delisting.

#### MGPI 2026-10-15 (`0000835011-26-000115`), item `5.03`: a **charter amendment and a bylaw amendment**, not a fiscal-year change — and it **does** name a symbol

§4 item 6 asked which of the three `5.03` sub-cases applied. Two of the three:

> On October 14, 2026, MGP Ingredients, Inc. (the “Company”) filed a Certificate of Amendment and Restatement to its Amended Articles of Incorporation with the Secretary of State of the State of Kansas to change the Company’s corporate name from “MGP Ingredients, Inc.” to “Sempreon, Inc.” (the “Name Change Amendment”). **The Name Change Amendment will become effective on November 2, 2026.** No stockholder vote was required in connection with the Name Change Amendment, as it involved a change in corporate name only.

> The Company’s Amended and Restated Bylaws were also amended to reflect the change in corporate name, which amended bylaws will also become effective on November 2, 2026.

No fiscal-year change. And it names symbols twice: its cover page 12(b) table
reads `Common Stock, no par value` / **`MGPI`** / **`NASDAQ Global Select
Market`**, and its item `7.01` reads:

> On October 15, 2026, the Company issued a press release announcing the change of its corporate name from “MGP Ingredients, Inc.” to “Sempreon, Inc.” to be effective on November 2, 2026, at which time it **expects** its ticker symbol will also change to “CASK”.

Note the registrant's own exchange string is **`NASDAQ Global Select Market`**,
not EDGAR's reference rendering `Nasdaq`, and its cover-page file number is
**`0-17196`**, not EDGAR's `000-17196`. Recorded as written.

### 6.5 `ELMES` ↔ `OTC` — §4 item 8 **CLOSED** by a filing, and the positional-pairing assumption is retired

§2.1 and §4 item 8 rested this on positional alignment of two parallel JSON
arrays and correctly called that an assumption about an undocumented format.
**It is no longer needed.** Two filings name the symbol and the venue together.

The press release filed as `EX-99.1` to the 2026-10-26 `8-K`
(`tm2628252d1_ex99-1.htm`, accession `0001104659-26-118561`), titled
`Elme Communities Announces Suspended NYSE Trading; Anticipates Trading Will
Begin OTC Effective October 26, 2026`, states the intention:

> The Company anticipates the Common Shares will commence trading over-the-counter (“OTC”) on Monday, October 26, 2026 under the symbol “ELMES”.

The `8-K` filed 2026-11-02 (`0001104659-26-121321`) then states, in the past
tense, that it happened — in its item `8.01`:

> The Common Shares commenced trading over-the-counter (“OTC”) on October 26, 2026 under the symbol “ELMES”.

and again in a cover-page footnote attached to the `NYSE*` entry:

> Elme Communities’ shares of beneficial interest began trading over-the-counter under the symbol “ELMES” on October 26, 2026.

**`ELMES` is the OTC symbol for CIK 0000104894's Shares of Beneficial Interest,
effective 2026-10-26, stated by the registrant in a filed `8-K`.** The
`ELME`↔`NYSE` half is confirmed independently by the 12(b) cover tables in
§6.1.

**What this does and does not settle about the array format.** EDGAR's
`tickers` `["ELMES", "ELME"]` and `exchanges` `["OTC", "NYSE"]` do pair
positionally *in this record*. One confirmed instance is not a documented
contract, and §2.1's caution should not be deleted on the strength of it. The
pairing is no longer *load-bearing* here, which is the useful outcome: the
finding rests on the filings and would survive the arrays being read the wrong
way round.

**And it has a consequence for the price record that §2.5 flagged and could not
settle.** ELME's shares did not trade on the NYSE after 2026-10-23 — trading was
suspended that day (§6.2) — and traded OTC as `ELMES` from 2026-10-26. So **any
ELME print for any of the nine sessions from 2026-10-26 through 2026-11-05
cannot be an NYSE print.** There is a real venue break inside the forward record's ELME tail, and
the capture agent asked for `ELME` and received `ok` across it. Whether the
vendor returned OTC `ELMES` data under the `ELME` symbol, stale NYSE data, or
something else is a question about the vendor's symbol continuity that no filing
can answer. §2.5's last bullet is therefore **upgraded from "neither confirmed
nor refuted" to "the break is confirmed to exist; the vendor's handling of it is
unmeasured"** — and it should be measured before those nine sessions of ELME
prints are used for anything.

### 6.6 MGPI: the symbol did become `CASK` — §4 item 7 **substantially CLOSED**, the exact date **still open**

§3.6 withheld `TICKER_CHANGE_EVIDENCED` because no filing naming a symbol was
readable and EDGAR's `tickers` array is undated. Both obstacles are gone.

**That the symbol changed is now evidenced by filings, in three independent
ways:**

1. **A filed periodic report states the change and its date.** The `10-Q` for
   the quarter ended 2026-09-30 (`0000835011-26-000124`, filed 2026-10-28) says,
   twice, in the notes and again in MD&A:

   > On October 15, 2026, the Company announced that effective November 2, 2026, its corporate name will change from “MGP Ingredients, Inc.” to “Sempreon, Inc.” and its Nasdaq ticker symbol will also change from “MGPI” to “CASK”.

   This is a *filed* report, not furnished `7.01` material, and it names both
   symbols, the direction of the change and the date.
2. **The `8-K` of 2026-10-15 and its press release say the same thing
   prospectively** (§6.4), the press release headline reading
   `New corporate identity will launch November 2, with planned Nasdaq ticker change to CASK`
   and its body `The Sempreon brand will officially launch on November 2, 2026, alongside the company’s planned transition to its new Nasdaq ticker symbol, CASK.`
3. **A filing made after the stated date reports the new symbol against the same
   CIK.** The `4` filed 2026-11-12 (`0001140361-26-043447`) carries
   `<issuerCik>0000835011</issuerCik>`, `<issuerName>SEMPREON, INC</issuerName>`,
   `<issuerTradingSymbol>CASK</issuerTradingSymbol>`, where the `4` filed
   2026-10-01 under the same CIK carried `MGP INGREDIENTS INC` and `MGPI`. The
   symbol in that field changed between those two filings and the CIK did not.

**Together with §3.3's negatives — which were not disturbed by any document read
here — this is a ticker change and not an exit.** Same CIK, same SEC file number
`000-17196` before and after, same exchange, no `25`, no `25-NSE`, no `15` in any
variant, no `8-K` item `3.01` or `1.03`.

**The exact date the symbol actually changed is still not established, and the
distinction matters.** Every filing that gives 2026-11-02 gives it
prospectively — "will become effective", "expects", "planned", "will also
change". No filing states, after the fact, that the symbol changed on a
particular date. What the filings establish is a **bracket**: `MGPI` was the
registered 12(b) trading symbol in documents filed as late as **2026-10-28**
(the `10-Q` and the `8-K` `0000835011-26-000122`, both cover tables reading
`MGPI` / `NASDAQ Global Select Market`), and `CASK` was the issuer trading
symbol in a document filed **2026-11-12**. Issuer-stated intended effective date
**2026-11-02**, falling inside that bracket.

So: **`TICKER_CHANGE_EVIDENCED`, `MGPI` → `CASK`, CIK 0000835011 unchanged,
issuer-stated effective date 2026-11-02 (prospective), actual change bracketed
to (2026-10-28, 2026-11-12].** The 2026-11-02 date should be recorded with its
prospective character attached, not as a measured transition date.

**`2026-10-28T04:00:00.000Z` is now positively refuted as a corporate date.**
§3.5 called EDGAR's `formerNames` `to` timestamp a metadata-maintenance
timestamp rather than a corporate effective date, and hedged. It can now be
stated outright: the corporate name change was executed with the Kansas
Secretary of State on **2026-10-14** and made effective **2026-11-02**. EDGAR's
timestamp of 2026-10-28 matches **neither** — it falls five days before the
effective date and fourteen days after the filing with the state. It is EDGAR
housekeeping and must never be read as a corporate event date.

### 6.7 The silences — §4 item 9: ELME **EXPLAINED**, MGPI's cause explained and its **timing still `UNEXPLAINED`**

#### ELME: explained exactly, to the session

§2.5 said *"Nothing in the EDGAR record states what changed on or about
2026-11-05/06"* and §4 item 9 recorded the timing as `UNEXPLAINED`. **The
filings state it precisely, and they match the measured capture record to the
day:**

| capture fact (§0.1, measured) | filing fact (§6.2–§6.4, read) |
|---|---|
| last ELME print **2026-11-05** | `8-K` 2026-09-29: `The Company anticipates the last day of trading of its Common Shares on the NYSE to be November 5, 2026 and that the Company’s share transfer books will be closed as of 4:00 p.m. (Eastern Time) on such date.` — and `8-K` 2026-11-06: `At the close of business (Eastern Time) on November 5, 2026, the share transfer books of Elme Communities (the “Company”) were closed.` |
| first silent session **2026-11-06** | `15-12G`: `Elme Communities dissolved as of 11:59 a.m. on November 6, 2026. At that time, all outstanding common shares of beneficial interest, par value $0.01 per share, of Elme Communities were cancelled.` |
| silent thereafter: 11-09, 11-10, 11-11, 11-12, 11-13 | the successor Beneficial Interests `will not trade on the New York Stock Exchange or any other exchange or over-the-counter` and are not transferable |

**The feed went quiet because the security ceased to exist.** The transfer books
closed at the close of business on the last printing session; the registrant
dissolved and the shares were cancelled before noon on the first silent
session. This is not a coincidence of calendar dates — it is the same event
recorded in two systems. §2.5's refusal to attribute the silence to the
`15-12G` was nonetheless correct reasoning: the Form 15 did not cause the
silence. The *dissolution* did, and the Form 15 is one of three filings that
report it.

#### MGPI: cause evidenced, timing not

The cause is no longer open: the symbol changed from `MGPI` to `CASK` (§6.6),
and a capture agent asking a vendor for `MGPI` after the change would be asking
for a symbol that no longer trades. **The timing still does not reconcile, and
the gap has not closed so much as moved.**

§3.5 reported nine unexplained sessions between EDGAR's 2026-10-28 name
timestamp and the last MGPI print. That framing was built on a timestamp now
known to be meaningless (§6.6). On the issuer's own stated date the arithmetic
is this: **2026-11-02 was a Monday, and per §0.1 MGPI's only silent
symbol-sessions in the captured window are 2026-11-11, 11-12 and 11-13 — so the
vendor returned MGPI bars on every session from 2026-11-02 through 2026-11-10,
seven sessions on and after the stated effective date, and then stopped.**

**Nothing in any filing read here accounts for those seven sessions.** No
filing says the transition was delayed, rescheduled or staged; no filing names
an actual transition date at all. Three readings are consistent with the
evidence — the symbol change took effect later than 2026-11-02; or it took
effect on 2026-11-02 and the vendor continued serving the retired symbol for
seven sessions; or the vendor served `CASK` data under the `MGPI` key — **and
the filings cannot distinguish them, because every one of them is a fact about
the vendor or about Nasdaq's symbol file, not about the Commission's record.**

**`UNEXPLAINED` is retained for MGPI's silence timing, and it is a correct
answer, not a gap in the answer.** It is also now a *bounded* unexplained: the
cause of the silence is evidenced, the residual question is a seven-session
discrepancy between an issuer-stated effective date and a vendor's symbol
retirement, and **it is answerable only against the vendor's own symbol history
or Nasdaq's daily symbol-change file — neither of which is a filing, and neither
of which this session holds.** That is the next measurement, and it belongs to
whoever owns the vendor adapter, not to EDGAR.

### 6.8 The 2025 ELME merger-proxy record — §4 item 11 **CLOSED**: the filings draw the line themselves

§4 item 11 declined to connect the late-2025 `PREM14A`, `DEFM14A` and
items-`2.01`/`5.01` `8-K` to the 2026 exit filings, and declined deliberately,
on the ground that no filing drew the line. **The 2026 filings draw it
explicitly, repeatedly, and by date.** The line is therefore drawn here because
a filing draws it, not because the shapes are suggestive.

Every ELME exit filing read above anchors itself to the same instrument:

> the Plan of Sale and Liquidation, which was approved by the Board of Trustees of the Company on July 30, 2025 and by the shareholders of the Company on October 30, 2025

and the dissolution `8-K` of 2026-11-06 and the `25-NSE` press release both cite

> the definitive proxy statement filed by the Company with the SEC on September 24, 2025 for the special meeting of shareholders held on October 30, 2025

**`DEFM14A` accession `0001104659-25-092947` is filed 2025-09-24** (§2.2's own
context list), matching that citation on date and form. The late-2025 `8-K`
`0001104659-25-110656` of 2025-11-13, items `1.01,1.02,2.01,2.03,5.01,7.01,9.01`,
is likewise matched by the 2026-09-18 `8-K`'s own description of
`the closing of the sale of 19 multifamily assets to an affiliate of Cortland Partners, LLC in November 2025`
and of the `Loan Agreement` entered `on November 12, 2025` with
`Goldman Sachs Bank USA`.

**The 2026 exit is the terminal step of a shareholder-approved liquidation that
began in 2025**, and §2.2's observation that no `S-4` appears anywhere in the
CIK's recent array is now explained rather than merely noted: there was no
acquirer. The company sold its properties, distributed the proceeds, was
delisted, dissolved and cancelled its shares. **This matters for the corpus
beyond ELME: an exit of this shape leaves no `S-4` and no successor CIK, so a
survivorship pipeline that looks for a merger counterparty will find nothing and
must not read that absence as evidence the issuer survived.**

### 6.9 Corrections to the verdicts in §2.6 and §3.6

Per the brief, the verdicts above are **not edited in place**. The corrections
are here.

#### §2.6 ELME — verdict `EXIT_EVIDENCED` **STANDS**; three of its four scope caveats are now **REFUTED** and must stop travelling with it

The headline verdict was right. Its scope paragraph was appropriately cautious
on the evidence then available and is now wrong on the evidence read here. §2.6
says the verdict *"does **not** supply an effective date, does **not** establish
that trading ceased, does **not** establish that the security was extinguished,
and does **not** explain the price-feed silence"*, and warns that the security
*"may well survive off-exchange"*.

| §2.6 caveat | status after reading the filings |
|---|---|
| does not supply an effective date | **PARTLY REFUTED.** Two primary dates are now on the record and they conflict by one day: `November 6, 2026` (`EX-99.25` to the `25-NSE`) and ten calendar days after 2026-10-26, i.e. 2026-11-05 (`8-K` `0001104659-26-121321`). The SGML `EFFECTIVENESS DATE` merely repeats the filing date. Record as `2026-11-05/2026-11-06, CONFLICTING_PRIMARY_SOURCES` — a one-day window, not an `UNRESOLVED`. |
| does not establish that trading ceased | **REFUTED.** NYSE trading was suspended on 2026-10-23 (`EX-99.25`, and the `8-K` item `3.01`); OTC trading as `ELMES` ran from 2026-10-26; the transfer books closed at the close of business on 2026-11-05; the successor Beneficial Interests trade nowhere. |
| does not establish that the security was extinguished | **REFUTED, and this is the material change.** `All outstanding common shares of beneficial interest, par value $0.01 per share, of Elme Communities were cancelled` at `11:59 a.m. on November 6, 2026`, certified by the registrant on its own `15-12G` with a holder-of-record count of `0`. |
| does not explain the price-feed silence | **REFUTED.** §6.7. |
| "the security may well survive off-exchange — treating this verdict as a death certificate would reproduce precisely the `IPET`/`TGLO` error" | **REFUTED on the facts, vindicated as a method.** It survived off-exchange for exactly nine sessions, as `ELMES`, and was then cancelled. The caution was the right default and the filings happen to overturn it; a session that had assumed extinguishment without reading the `15-12G` would have been right by accident, which §1.3's own discipline rejects. |

**Revised corpus-facing statement for ELME.** `EXIT_EVIDENCED`, and
specifically a **liquidation-and-dissolution exit with the security
extinguished**: involuntary NYSE delisting under `17 CFR 240.12d2-2(b)` for
`"abnormally low selling price"` levels under Section 802.01D, trading suspended
2026-10-23, OTC as `ELMES` 2026-10-26 to 2026-11-05, listing removal effective
2026-11-05 or 2026-11-06 (sources conflict), dissolution and share cancellation
11:59 a.m. 2026-11-06, Section 12(g) registration terminated by `15-12G` under
`Rule 12g-4(a)(1)` with 0 holders of record, successor instrument
non-transferable and not traded on any venue. **This is a genuine survivorship
exit and should be recorded as one** — with the one-day date conflict preserved
rather than collapsed, and with the venue break in the price tail flagged per
§6.5.

#### §3.6 MGPI — verdict `OTHER_EVENT_EVIDENCED` and **NOT** an exit **STANDS**; the withheld `TICKER_CHANGE_EVIDENCED` is now **GRANTED**

§3.6's operative conclusion is unchanged and is strengthened: the registrant is
an active filer across and after the silent window, and the exit-shaped forms
are absent. **Do not record a survivorship exit for MGPI** — that instruction
stands exactly as written.

What changes is the withheld grade. §3.6 withheld `TICKER_CHANGE_EVIDENCED`
because *"no filing naming a symbol was readable, EDGAR's `tickers` array is an
undated reference artefact that cannot carry a mapping, and the nine sessions
between the name change and the last MGPI print (§3.5) are unexplained"*, and
concluded *"A ticker change is the only hypothesis presently consistent with the
record, and **consistent with** is not **established by**."* The first clause is
now false — four filings name a symbol (§6.1, §6.6) — and the second is
irrelevant, because the finding no longer rests on the array. The third survives
in altered form (§6.7) and is the reason the *date* is still qualified.

**`TICKER_CHANGE_EVIDENCED` is granted**: `MGPI` → `CASK`, CIK 0000835011
unchanged, SEC file number `000-17196` unchanged, exchange unchanged, issuer name
`MGP INGREDIENTS INC` → `SEMPREON, INC` effective 2026-11-02 by charter
amendment filed with the Kansas Secretary of State 2026-10-14, symbol change
stated by the registrant as effective 2026-11-02 and corroborated by a
post-change `4`. **Classify MGPI as a false positive for exit and carry the
ticker change forward with its date marked prospective** — the actual
transition is bracketed to (2026-10-28, 2026-11-12], and the seven sessions of
`MGPI` prints on and after 2026-11-02 are unexplained by any filing.

### 6.10 What is still open after reading the filings

Restating every item of §4, with its status. Nothing is quietly dropped.

| §4 item | status after §6 | what would settle it |
|---|---|---|
| 1. `ELME` ticker↔CIK confirming filing + accession | **CLOSED.** `0001104659-26-118561`, cover-page 12(b) table: `Shares of Beneficial Interest` / `ELME` / `NYSE`. Corroborated independently by `0000104894-26-000112`. The §1.4 model-prior candidate was correct. | *nothing — needs only human promotion* |
| 2. `MGPI` ticker↔CIK confirming filing + accession | **CLOSED.** `0000835011-26-000110` (`4`, `issuerTradingSymbol` `MGPI`); corroborated by the `10-Q` `0000835011-26-000124` cover table. Note the `144` route named in §4 does **not** exist (§6.1). | *nothing — needs only human promotion* |
| 3. ELME effective delisting date | **PARTLY CLOSED, CONFLICTING.** `EFFECTIVENESS DATE 20261026` is the filing date repeated. Two primary dates disagree by one day: 2026-11-06 (exchange's notice) and 2026-11-05 (registrant's "10 calendar days"). Recorded as a window, not resolved. | the SEC's own order or notice of removal, or the NYSE's delisting notice; **a human should decide whether TradeIt records the window or picks a side, and that is a methodology decision, not an agent one** |
| 4. ELME `25-NSE` exchange string, class, `ruleProvision` | **CLOSED.** `NEW YORK STOCK EXCHANGE LLC` / `Shares of Beneficial Interest` / `17 CFR 240.12d2-2(b)`. Differs from the `LEH` control's `NEW YORK STOCK EXCHANGE INC`; both preserved. | *nothing — needs only human promotion* |
| 5. ELME `15-12G` section, class, holder count, suspension date | **CLOSED on three of four.** `Rule 12g-4(a)(1)` under Section 12(g); `Common Shares of Beneficial Interest`; `0` holders of record. **The form states no suspension date** — only its signature date, 2026-11-06. | the suspension date is a question about Rule 12g-4's operation, not about this document; nothing further to read |
| 6. Contents of every `8-K` cited | **CLOSED.** §6.4. ELME 2026-10-26 `3.01` = involuntary listing-standard failure; ELME 2026-09-29 `3.01` = a *separate* voluntary delisting notice; both `2.01` items = property dispositions; MGPI `5.03` = charter + bylaw amendment, no fiscal-year change, names both `MGPI` and `CASK`. | *nothing — needs only human promotion* |
| 7. `CASK`'s start date and whether `MGPI` ended | **SUBSTANTIALLY CLOSED; exact date OPEN.** The change is evidenced and `CASK` is the successor, not a second class. The date is issuer-stated prospectively as 2026-11-02 and bracketed to (2026-10-28, 2026-11-12]. | **not a filing.** Nasdaq's daily symbol-change file, or the vendor's own symbol history, is the only thing that can date the actual transition |
| 8. `ELMES`↔`OTC` and `ELME`↔`NYSE` pairing | **CLOSED.** Both halves stated in filings (§6.5); the positional-array assumption is no longer load-bearing. The general question of whether EDGAR's parallel arrays pair positionally remains undocumented and should stay treated as such. | *nothing for this record* |
| 9. Cause of the price-feed silence | **ELME: CLOSED/EXPLAINED** — security cancelled 11:59 a.m. 2026-11-06, transfer books closed 2026-11-05 (§6.7). **MGPI: cause EVIDENCED (ticker change), timing `UNEXPLAINED`** — seven sessions of `MGPI` prints on and after the stated 2026-11-02 effective date. | MGPI timing: the vendor's symbol history or Nasdaq's symbol-change file. **`UNEXPLAINED` remains the correct answer until one of those is measured** |
| 10. The capture record itself | **Already closed by §0.1** (measured by the commissioning session). Untouched here; §6.7 relies on it and says so. | — |
| 11. The 2025 ELME merger-proxy record | **CLOSED.** The 2026 filings cite the Plan of Sale and Liquidation (Board 2025-07-30, shareholders 2025-10-30) and the definitive proxy filed 2025-09-24, matching `DEFM14A` `0001104659-25-092947`. There was no acquirer; this was a liquidation. | *nothing — needs only human promotion* |

**Needs a human to settle, as distinct from needing more evidence:** items 1, 2,
4, 6 and 11 are evidentially complete and blocked only on the
`MANUAL_VERIFIED` boundary — a person must open the documents. Item 3's one-day
conflict needs a *methodology* decision about how TradeIt records conflicting
primary dates, which is the owner's call, not an agent's. Items 7 and 9 (MGPI)
need a measurement that is **not available from EDGAR at all** and must come
from the vendor side.

### 6.11 Reproducing §6

`EDGAR_USER_AGENT` must be set in the environment and must carry a contact
address. **Never print, echo, log or commit its value; pass it only by variable
reference.**

```bash
# probe first; stop if this is not 200
curl -sS -A "$EDGAR_USER_AGENT" -o /dev/null -w '%{http_code}\n' \
  https://www.sec.gov/files/company_tickers.json

B=https://www.sec.gov/Archives/edgar/data
# ELME 25-NSE: full submission text carries the SGML header and both documents
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000087666126000862/0000876661-26-000862.txt"
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000110465926123774/tm2629092d1_1512g.htm"   # 15-12G
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000110465926118561/tm2628252d1_8k.htm"      # 8-K 3.01 (NYSE)
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000110465926118561/tm2628252d1_ex99-1.htm"  # ELMES press release
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000010489426000112/elme-20260914.htm"       # 8-K 1.02,2.01
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000010489426000117/elme-20260928.htm"       # 8-K 2.01,3.01 (voluntary)
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000110465926121321/tm2628645d1_8k.htm"      # 8-K: ELMES began 10-26
curl -sS -A "$EDGAR_USER_AGENT" "$B/104894/000110465926123680/tm2629090d1_8k.htm"      # 8-K: dissolution
curl -sS -A "$EDGAR_USER_AGENT" "$B/835011/000083501126000115/mgpi-20261014.htm"       # 8-K 5.03
curl -sS -A "$EDGAR_USER_AGENT" "$B/835011/000083501126000115/final-sempreonoctober15ann.htm"
curl -sS -A "$EDGAR_USER_AGENT" "$B/835011/000083501126000124/mgpi-20260930.htm"       # 10-Q (grep Sempreon)
curl -sS -A "$EDGAR_USER_AGENT" "$B/835011/000083501126000110/wk-form4_1790889911.xml" # issuerTradingSymbol MGPI
curl -sS -A "$EDGAR_USER_AGENT" "$B/835011/000114036126043447/form4.xml"               # issuerTradingSymbol CASK
```

Append `/index.json` to any accession directory to list its documents. Twenty-seven
requests were made for this section, sequentially, well under ten per second.

**Nothing in §6 is `MANUAL_VERIFIED`. A program read these filings. Promotion
requires a person to read them.**
