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
