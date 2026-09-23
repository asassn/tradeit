# What forty-four days of measurement established — 2026-09-23

A consolidation, written to be acted on. Everything here is measured; the
sections it cites hold the arithmetic. Where a number is an estimate it says so.

---

## 1. The answer in one paragraph

**Nothing measured on this corpus is profitable enough to trade, and the search
for it is close to exhausted.** One signal predicts and does not pay (§35, §37).
One combination beat both its parts on seven years and inverted on the next six
(§46, §48). Chart patterns as complete trades earned exactly the market's return
(§44). Twenty-nine indicator arms, four fundamental anomalies at two horizons
each, and twelve pattern families are closed by registered stop rules. Two
attempts to improve the *machinery* rather than the selection — a
volatility-scaled stop (§47) and a market-timing gate (§49) — both made a
random portfolio **worse**. What this project has produced is not a trading edge;
it is an unusually trustworthy instrument for establishing that a trading idea
does not have one.

---

## 2. What exists, measured

| | |
|---|---|
| corpus | **17,433 securities**, 83.4M daily bars, 92M point-in-time fundamental facts, 68 GB |
| survivorship | **11,252 of 21,618** dated exits priced — **52.0%**; graded `MATERIALLY_SURVIVORSHIP_CORRECTED` |
| code and tests | 165,000 lines of Python, **4,189 tests**, 26 architecture decisions |
| evidence | **40 scoreboard sections**, **23 pre-registrations**, **140 trials** on the ledger |
| effort | 435 commits over **44 active days**; several hundred processor-hours; no cloud spend |
| money | two subscriptions (EODHD, Sharadar). **No purchase was made for any result in this document** |

---

## 3. What is established, and would survive scrutiny

**The benchmark nobody beat.** A *random* selection of liquid securities, run
through the platform's own sizer, stops and costs, returned **3.19%/yr on
2013–2019** and **7.71%/yr on 2020–2025** (§46, §48). Every arm in every
portfolio test is measured against that, not against zero. This is the single
most useful number the project owns: it is what "doing nothing clever" pays.

**Five classes of defect, each found by a check that failed rather than by
looking for one.**

| defect | what it did |
|---|---|
| splits applied twice (§0.1a) | corrupted every price level |
| placeholder bars with no trade (§0.7) | produced a +8,511,217% mean return |
| bad prints that clear a liquidity floor (§0.8) | passed every guard built until then |
| **volume already adjusted for future splits (§0.9)** | a look-ahead that let future winners into every liquidity-floored sample |
| **price steps of thousands with nothing recording them (§0.10)** | 10,020 sessions across 2,228 securities; moved one study's mean from **+16.73% to +0.19%** |

**Three defects in the engine and the statistics**, each of which flattered
results: stops that were computed and discarded, significance inflated up to
twelvefold by treating one market day as thousands of independent facts, and a
sizer that would buy 20.6 million shares of a $0.0001 stock and pay $102,911 to
hold $2,000 of it.

**A method that catches its own errors.** Pre-registration before any result,
a trial ledger that raises the bar as the search widens, stop rules that close a
line rather than inviting a variant, placebo controls, and mutation-tested
assertions. It has caught: a positive control reading backwards (§32), two tests
that had gone vacuous, an estimator whose t-statistic and estimate described
different quantities, and — twice in the last week — my own analysis code
reporting a sample it had not actually collected.

**Execution assumptions are fair where we use them (§45).** Against real
1-minute paths, stops fill below the stop price 57.6% of the time, costing a
median 0.007R at the distances used. Below about 2% of price, daily bars cannot
honestly simulate a stop at all.

---

## 4. What is closed, by registered stop rule

Single classical indicators at **5, 10, 21 and 63 sessions** · the four
fundamental anomalies at **63 sessions and twelve months** · **pattern breakouts
as trade rules** · low volatility **as a portfolio** · ADX · market
capitalisation · OBV, volume accumulation, relative strength · **the two-signal
combination** · **volatility-scaled stops** · **market timing by trend**.

Reopening any of these requires a registration stating what *new information* it
brings — not a new parameter.

---

## 5. What the evidence does **not** say

Honesty about the limits matters more here than anywhere else in the document.

- **Absence of proof, not proof of absence.** Each null is bounded by what the
  test could resolve. §32 could not have seen an IC below 0.033; §41 could not
  have resolved earnings surprise at twelve months with twelve independent
  years. Effects smaller than those floors remain possible and remain untradeable
  at our costs anyway.
- **Earnings surprise never failed on direction.** It pointed the declared way on
  every cut at both horizons and missed only significance (§40, §41). It is the
  one thing here that behaves like a real but small effect.
- **Half the dead are missing.** 48% of exits cannot be priced, and pre-1998
  history is survivors only. Every result carries that.
- **The recovery assumption is unresolved.** §46's combination passed on one
  assumption about what delisted holdings were worth and failed on the other.
  §16 measured that 78.8% of the dead were bought out, which makes the
  favourable assumption the likelier — but the engine still takes one number for
  all of them.
- **Nothing here tested day trading.** The free intraday feed is survivors-only
  and accurate only for the most liquid names.

---

## 6. The pattern worth naming

Three independent attempts to beat a plain diversified basket have now failed in
the same direction:

| | result |
|---|---|
| better **selection** (12 signals, 24 indicators, 12 patterns, 4 fundamentals) | at best the market's return (§44), usually less |
| better **stops** (volatility-scaled) | −0.99 pp/yr **on the control** (§47) |
| better **timing** (trend gate) | −1.37 pp/yr **on the control**, drawdown *up* (§49) |

Each was a reasonable idea, pre-registered, and measured against a control that
chose at random. **On this data, every departure from "own a diversified basket
and leave it alone" has cost money.** That is a finding, and it is the most
robust one in the document.

---

## 7. What I would do now

**Stop searching and consolidate.** The free single-signal space is measured.
The two machinery improvements available are refuted. Continuing means either
paying for data whose known weaknesses (survivorship intraday, vendor-stated
coverage) this project's own rules say cannot justify capital, or running a
141st trial against a ledger that already demands t > 2.64.

**Three things are worth doing with what exists, in order:**

1. **Use it as an instrument, not an oracle.** The corpus, the point-in-time
   discipline and the placebo method are reusable for any future claim —
   including someone else's. A tool that can cheaply prove an idea *doesn't*
   work has value precisely because most ideas don't.
2. **Settle the delisting-recovery question** (§46's open item). It is a corpus
   question, free, and it decides whether the one interaction found was real.
   It is also the assumption every future portfolio result will rest on.
3. **If money is ever spent, spend it on information nobody else has cheaply** —
   analyst estimates for a true earnings surprise, which is the one line that
   kept pointing the right way. Not on more price history; price history is
   measured and empty.

**What would genuinely change the answer** is new *information*, not new
*methods*: expectations data, intraday paths with honest survivorship, a
different market, or a longer horizon than thirteen years of fundamentals can
support. Another indicator will not.

---

## 8. The uncomfortable sentence

If the goal was a profitable trading system, **this has not produced one, and
forty-four days of careful measurement say the likeliest reason is that the
edge is not there to find at this scale with this data.** If the goal was to
know that reliably rather than to believe otherwise while losing money slowly,
it has succeeded completely — and the second outcome is worth more than it
feels like today.

**No result in this document authorises live capital.** The interlock in
`src/tradeit/config.py` stands.
