# Forward survivorship system

Once the historical archive exists, TradeIt maintains survivorship safety
itself, so the history never needs re-purchasing. **Not implemented.** Design
for approval.

## 1. The premise

Today's feeds answer *"what trades now?"*. That question, asked every day and
stored naively, produces a ticker list — and a ticker list silently rewrites
history every time a company disappears. What is needed is a **longitudinal
security master**: an append-only record of every security that has ever
existed, with the dates it existed under each name.

**A disappeared ticker must never vanish from the database.** Its record stays
permanently queryable, and its disappearance is itself a recorded event with a
`knowledge_time`.

## 2. No vendor is trusted alone

Three feeds, deliberately overlapping, each strong where the others are weak:

| source | authoritative for | weak at |
|---|---|---|
| **SEC EDGAR** | filings, filing dates, CIK, form 25/15 delisting notices, 8-K events | no prices, no tickers-as-such, only covers filers |
| **Twelve Data** | daily prices, active symbol list | corporate lifecycle, delisted history |
| **FMP** | corporate-action and symbol-change feeds *only* — see §6 | excluded from fundamentals by project rule |
| **TradeIt registry** | the accumulated truth, and every disagreement ever seen | nothing — it is the ledger |

The registry is not a cache of the vendors. It is the only place where
*yesterday's* answer is still available to compare against today's.

## 3. The daily cycle

```
1  fetch      Twelve Data active symbols; FMP symbol/action deltas;
              EDGAR daily index (new filings, forms 25/15/8-K)
2  diff       against the registry's state as of yesterday
3  classify   every difference into an event type (§4)
4  corroborate seek a second source for each event; record agreement or not
5  record     append events + observations; NEVER update a security row in place
6  report     unresolved differences to an operator queue
```

Step 5 is the discipline: the registry is append-only in the same way
`pattern_observations` is. `securities.delisting_date` is *set once* when an
event is confirmed; the evidence for it lives in the event log.

## 4. Detection rules

| event | primary signal | corroboration | recorded as |
|---|---|---|---|
| **new listing** | symbol appears in TD active list | first price bar; EDGAR S-1/424 | `securities` insert + `symbol_aliases` open interval |
| **ticker change** | symbol disappears **and** another appears for the same CIK / vendor id | FMP symbol-change feed; 8-K item 5.03 | close old alias interval, open new, **same `instrument_id`** |
| **delisting** | symbol disappears from TD for > *N* sessions | **EDGAR Form 25 / Form 25-NSE** (exchange delisting) or **Form 15** (deregistration) | `delisting_date` + `delisting_reason`, alias interval closed |
| **bankruptcy** | 8-K item 1.03; ticker often gains a `Q` suffix | subsequent Form 15/25 | `bankrupt_liquidated` or `bankrupt_reorganised` |
| **merger / acquisition** | symbol disappears; S-4 / 8-K item 2.01 | FMP action feed; acquirer's filing | `security_relationships` + `acquired_by` |
| **reverse merger** | shell's CIK continues under a new name and ticker | 8-K item 5.06 ("shell company transaction") | `reverse_merged_into`, **new `instrument_id`** — see §5 |
| **spin-off** | new symbol + Form 10 registration | parent 8-K | `spun_off_from` |
| **ticker reuse** | a ticker reappears whose prior alias interval is **closed** and whose CIK/vendor id **differs** | any | **new `instrument_id`**, new alias interval — never an extension |
| **symbol disappearance, unexplained** | gone from TD, no EDGAR or FMP evidence | — | `pending_investigation`, **not** a delisting |

### The two rules that matter most

**A disappearance is not a delisting until something says why.** A symbol that
vanishes with no corroborating filing enters `pending_investigation` and stays
there. Recording it as `delisted` on absence alone would be exactly the
inference the survivorship vocabulary exists to forbid — the same distinction
`SurvivorshipStatus` already draws between `NOT_FOUND` and
`PROVIDER_HISTORY_UNAVAILABLE`.

**Ticker reuse always mints a new identity.** If the reappearing ticker's CIK or
vendor permanent id differs from the previous holder's, it is a different
security. The `symbol_aliases` exclusion constraint makes the alternative
impossible to represent, which is the point.

## 5. Reverse mergers deserve their own paragraph

A shell company's CIK survives while the *economic entity* changes completely.
CIK continuity therefore implies **legal** continuity, not economic continuity —
and the price series across a reverse merger is two different businesses under
one filer.

`research-01` treats a reverse merger as a **new `instrument_id`**, linked to the
shell by `reverse_merged_into`. This mirrors the structural-break handling
already proven in Phase 4/5: the BBBY 536-session gap ends an analytical episode
and nothing crosses it. Same principle, different trigger.

## 6. FMP's permitted role

The standing rule excludes FMP for fundamentals, ratios, earnings, estimates,
statements, OHLCV, insider and institutional data. That leaves **corporate
actions and symbol-change notification**, which is the only role assigned here —
and always as a *corroborating* source, never the sole basis for a lifecycle
event. Every FMP-sourced event is recorded with `source = 'fmp'` and requires
either EDGAR or Twelve Data agreement before it changes a security's state.

If that constraint should be tightened further, the design degrades gracefully:
EDGAR alone covers delistings (25/15), bankruptcies (8-K 1.03) and mergers (8-K
2.01 / S-4) for every filer, and Twelve Data covers the disappearance signal.
FMP shortens the detection latency; it is not load-bearing.

## 7. Operator queue

Automation must not resolve ambiguity by guessing, so it doesn't:

- unexplained disappearances past a threshold,
- vendor disagreement on a lifecycle event,
- a ticker reappearing with an ambiguous identity,
- a security with prices but no filings, or filings but no prices.

Each is a queue item with the evidence attached. Nothing in the queue changes
the registry until resolved, and an unresolved item is reported by the gate as a
coverage caveat rather than being silently dropped.

## 8. What this buys

After the one-time historical backfill, the recurring data need is only:
**forward prices** (Twelve Data, already subscribed), **forward filings**
(EDGAR, free), and **corporate-action latency** (FMP, already subscribed). The
registry accumulates permanently, so the survivorship-safe universe of 2035 is
built from the 1998 archive plus every daily observation since — never
re-purchased.

That is the entire argument for the one-time-download model, and it is exactly
why the retention-rights question in `PHASE_06_VENDOR_MATRIX.md` §1 must be
answered in writing first.
