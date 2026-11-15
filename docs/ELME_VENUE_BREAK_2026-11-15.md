# The vendor's symbol handling across the ELME venue break — measured

`FORWARD_EXIT_ADJUDICATION_2026-11-14.md` §"Something worth acting on" left
this open:

> ELME's shares did not trade on the NYSE after 2026-10-23 and traded OTC as
> `ELMES` from 2026-10-26. So any ELME print for the nine sessions 2026-10-26 →
> 2026-11-05 cannot be an NYSE print. There is a confirmed venue break inside
> the forward record's ELME tail, and the capture agent got `ok` straight
> across it. The vendor's handling of that break is unmeasured and should be
> before those prints are used.

Measured 2026-11-15 against EODHD's live API, three requests. **The answer is
worse than "unmeasured": there are three distinct defects, and the capture
cannot see any of them.**

## 1. The vendor spliced two venues into one series

`ELMES.US` today returns **sixteen** sessions — 2026-10-15 to 2026-11-05 —
spanning both venues as one continuous series, with no field distinguishing
them:

| session | close | volume | venue |
|---|---|---|---|
| 2026-10-22 | 1.77 | 705,500 | NYSE |
| 2026-10-23 | 1.77 | **0** | NYSE, last session — a price with no trade (§0.7) |
| **2026-10-26** | **0.04** | 30,922,400 | **OTC — a −97.6% single-session move** |
| 2026-11-05 | 0.053 | 350,462 | OTC, last print before dissolution |

This is the exact shape `PHASE_06_VENDOR_MATRIX.md` says to grade as a failure:
*"a continuous series across the break is the failure, not the success."* It
was written about a reused ticker and two issuers; here it is one issuer across
two venues, and the principle is unchanged.

**The price collapse and the venue change coincide, so this series cannot
separate them.** ELME was dissolving — the `15-12G` certifies the shares were
cancelled on 2026-11-06 with holders of record 0 — so a collapse to pennies is
plausible on its own. What cannot be recovered from these bars is how much of
the −97.6% is the market repricing a dissolving company and how much is the
move from a lit exchange to OTC. Anyone reading a return series here is reading
a confounded number.

## 2. The vendor retroactively re-mapped the ticker, after we captured

At capture time, every one of those nine OTC sessions came back under the
ticker **`ELME`** with status `ok`. That is what the forward record holds.

Today:

| ticker | rows returned |
|---|---|
| `ELME.US` | **7** — stops dead at 2026-10-23, the last NYSE session |
| `ELMES.US` | **16** — the whole history, NYSE period included |

So EODHD moved the entire series to the new symbol and **back-filled the NYSE
period under the OTC ticker**, leaving `ELME` truncated at the break. This is
the caveat already recorded in `PHASE_06_VENDOR_MATRIX.md` — *EODHD serves one
series per ticker, the most recent holder* — observed happening to us in real
time rather than inferred.

**Consequence for the record:** our nine OTC bars are stored under
`instrument_id = 1700`, which is ELME's corpus `security_id`, and the vendor no
longer serves those bars under that ticker at all. The identity under which
they were acquired no longer resolves to them.

## 3. Three of sixteen bars have since been revised

Compared bar by bar against what the vendor serves today:

| session | ours | vendor now | |
|---|---|---|---|
| 2026-10-26 | 0.0419 / 30,922,386 | 0.04 / 30,922,400 | revised |
| 2026-10-27 | 0.0458 / 8,305,727 | 0.05 / 8,397,600 | revised |
| 2026-11-05 | 0.05 / **765,000** | 0.053 / **350,462** | revised — volume differs by 53% |

**All seven NYSE bars agree exactly. Every revision is inside the OTC window.**

We keep the first-seen values, because `_write_bars` never overwrites a bar it
already holds. That is the right default and it is what makes the record
bitemporally honest — it preserves what was knowable when we asked. But it
means our copy now diverges from the vendor's, and **nothing in the record says
so**. The divergence is only visible by re-asking, which nothing does.

## 4. The capture cannot detect any of this

It asks by ticker, receives `ok`, and writes under the corpus instrument id. A
venue change, a vendor-side ticker re-mapping, and a silent price revision are
all invisible to it. Nothing failed; nothing was logged; the status was `ok`
sixteen times.

That is not a bug in the capture — it is the boundary of what asking a vendor
for a ticker can tell you, and it is why the identity architecture keeps ticker
identity, listing venue and security identity as separate questions.

## 5. How common is this shape? Measured, not assumed

Scanning every bar in the forward record for single-session moves of 30% or
more — 4,327 bars across 195 instruments:

| instrument | sessions | move |
|---|---|---|
| **1700 (ELME)** | 2026-10-23 → 2026-10-26 | **−97.6%** |
| 1463 | 2026-11-05 → 2026-11-06 | −32.9% |
| 1194 | 2026-10-29 → 2026-10-30 | +30.5% |

ELME is an order of magnitude beyond the other two, which are unremarkable
moves. **The splice shape is confined to one instrument.** This scan is cheap
and worth re-running whenever the record is relied on.

## The read rule

> **`instrument_id = 1700` holds two venues.** Its bars for 2026-10-15 to
> 2026-10-23 are NYSE prints. Its bars for 2026-10-26 to 2026-11-05 are **OTC
> prints under the ticker `ELMES`**, captured under `ELME` before the vendor
> re-mapped them. **They are not one price series and must not be read as
> one.** The −97.6% move between them is a venue change and a dissolution
> confounded, and three of the nine OTC bars have since been revised by the
> vendor.

Nothing is deleted. The bars are a true record of what was asked and what came
back, and the first-seen values are the honest answer to *what was knowable on
the day*. What is added is the rule for reading them.

## What this does not settle

- **Whether the pre-break `ELMES` bars are genuinely NYSE prints** or the
  vendor's reconstruction. They match our `ELME` captures exactly, which is
  consistent with both.
- **Whether other vendors behave the same way.** Tiingo was never asked for
  ELME; the symbol belongs to the EODHD half of the universe.
- **Whether the capture should detect re-mapping at all.** Re-asking an older
  session to compare answers would find revisions, but it needs a decision
  about which answer wins — the same which-vendor-wins decision the
  never-overwrite rule defers, and it is not taken here.

## What was built in response, the same day

The subset of the above that needs no deferred decision is now part of the
nightly job, per
[ADR-0032](adr/0032-a-move-too-large-to-be-trading-is-reported-not-judged.md):
`forward_daily.py` scans the record after each capture and reports
single-session moves of 2× or more in either direction — **report only, no cause
claimed, never blocking, never overwriting.** The threshold is derived from §5's
distribution rather than chosen: above the largest trading move measured
(1.490×) and at or below the smallest ratio a corporate action produces (2:1).
It would have printed §1's move on 2026-10-26 rather than leaving it for a
question asked three weeks later.

§3 is closed too, by a different route: the capture now writes a revision as an
additional row with its own `knowledge_time` rather than skipping a session it
already holds, per
[ADR-0033](adr/0033-a-revision-is-a-new-row-and-neither-answer-wins.md). The
which-vendor-wins question it was waiting on turned out to be already answered —
`OhlcvBar` has been keyed on `knowledge_time` since `0001_initial` and
`BarRepository.history` has always resolved revisions against the reader's clock.
**Neither answer wins; the record holds the disagreement and each reader resolves
it against its own clock.** The path was latent, not working: every one of the
4,327 bars held exactly one `knowledge_time` until today.

**ELME's own three revisions are not captured by it, and will not be.** The
nightly window reaches back seven sessions; ELME's last print was 2026-11-05 and
the vendor no longer serves those bars under the ticker we ask for. The
mechanism catches the *next* vendor revision inside the window, not this one.
Backfilling these three would mean asking for `ELMES` and writing the answer
under instrument 1700 — which is §2's identity question, and is not settled by
§3's being closed.

**§2 stays open.** A vendor re-mapping a ticker with no price move leaves no
trace in a close series and none in a revision either, because the bars arrive
under a symbol we have stopped asking about. That is an identity question, not a
price question.
