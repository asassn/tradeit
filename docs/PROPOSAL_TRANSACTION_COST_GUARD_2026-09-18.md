# Proposal: refuse an entry whose round-trip cost is more than 1% of it

**Status: APPROVED 2026-09-18 by the owner, at 1%, and switched ON by default.**
The guard was first committed switched off (`68c10f7`). The owner approved the
recommendation below, and the default of `RiskConfig.max_round_trip_cost_pct`
became `0.01`. Setting it to `None` switches the guard off and restores the
pre-guard strategy digest.

---

## The decision, in one sentence

**Set `RiskConfig.max_round_trip_cost_pct` to `0.01` by default. The platform
would then refuse any entry whose estimated commission, spread and slippage, to
buy and later sell, is more than 1% of the position.**

**What it prevents.** Two of the stop-ladder test's four 2010–2019 samples went
to negative equity. In each, a single entry at $0.0001–$0.0002 cost several
times the account in commission. With the guard on, and no other change, all
four of those runs finish with equity positive throughout (table below).

**What it costs.** Nothing measurable on the stop-ladder and low-volatility
runs, the ones measured here. In all 20 of their current configurations (the $5
minimum price in place), the results with the guard on are **identical** to the
results with it off: same CAGR, same trade
count, same lowest equity. That holds at 1% and at 2%. The most expensive of
their 7,050 entries cost 0.33% of its value at default costs and 0.85% at the
5× stress costs.

**Recommendation: 1%.** The reasoning is in the section "Why 1%" below.

---

## Detail below: optional reading

### The gap

The platform has no check on cost anywhere between the sizer and the order.

- `RiskBasedSizer` sizes by risk: `risk_per_trade_pct` of equity divided by the
  distance to the stop. The lower the price, the more shares that buys, and a
  per-share commission grows with shares, not with dollars.
- `SizingConfig.min_position_notional` ($500) limits how *small* a position may
  be in dollars. That is a different thing from cost.
- The four rules `build_engine` assembles (heat, positions, size, gross
  exposure) measure risk and exposure, not cost.
- The liquidity cap offered no protection either. A sub-penny print trades in
  billions of shares, so its 20-session dollar volume was **$64.8 billion**,
  and the sizer's 2% liquidity cap came nowhere near binding.

`LiquidityConfig.min_price` ($5) exists, but nothing on the platform enforces
it. After 29111dc, the research strategy `FactorTilt` reads it; the platform
itself does not. A live or paper system assembled from the same parts could
place the same order.

### The two entries, as measured

These were measured by replaying the runs with every fill recorded. The replay
reproduces all 36 recorded pre-min-price and current base-cost results exactly
(CAGR to 1e-6, trade count). It also reproduces the two crashes, which have no
result file.

| | 9106, sample 2 | 6792, sample 3 |
|---|---|---|
| decided | 2010-07-06, close $0.000100 | 2011-04-04, close $0.000100 |
| stop (8%) | $0.000092 | $0.000092 |
| equity | $99,793.09 | $124,162.26 |
| sizer's binding cap | `risk_per_trade` | `risk_per_trade` |
| shares | **62,370,683** | **77,601,411** |
| filled | 2010-07-07 at $0.000100065 | 2011-04-05 at $0.000200 |
| notional at fill | $6,241 | $15,520 |
| entry commission | **$311,853.42** | **$388,007.06** |
| lowest equity, LADDER / HOLD | −$520,343 / −$533,558 | −$650,233 / −$741,807 |

**This corrects three figures in STOP_LADDER Amendment 2 and SIGNAL_SCOREBOARD
§38.** Those are dated records and are left as written.

- **The 9106 entry was 62.4 million shares, with $311,853 of commission.** The
  quoted 20,582,325 shares and $102,911 are the 33% partial-profit exit of that
  trade: 62,370,683 × 0.33 = 20,582,325.
- **The sizing was risk-based, not equal-dollar.** In both entries
  `risk_per_trade` was the cap that bound.
- **The notionals were $6,241 and $15,520, not about $2,000.**

The equity figures and the diagnosis (a sub-penny entry, sunk by per-share
commission) stand. The HOLD arms, which were not run at the time, crash the
same way.

### How often it happens: structurally

At the default `CostConfig` ($0.005 a share, $1 minimum, 3 bps spread, 5 bps
slippage), each leg costs `max(0.005 × shares, $1)` plus 6.5 bps of notional.
Once an order is above 200 shares, the per-share commission is **0.005 / price
of notional per leg**, whatever the size. Price alone decides it.

| price | commission per leg | round trip, default costs | round trip, 5× stress |
|---|---|---|---|
| $5 | 0.10% | 0.33% | 0.85% |
| $2 | 0.25% | 0.63% | 1.15% |
| $1 | 0.50% | 1.13% | 1.65% |
| $0.50 | 1.0% | 2.13% | 2.65% |
| $0.10 | 5.0% | 10.1% | 10.7% |
| $0.01 | 50% | 100% | 101% |
| $0.0001 | 5,000% | 10,000% | 10,001% |

The price below which a threshold refuses an entry:

| threshold | default costs | 5× stress |
|---|---|---|
| 0.5% | $2.70 | **every entry**: spread and slippage alone are 0.65% |
| **1%** | **$1.15** | **$2.86** |
| 2% | $0.535 | $0.74 |
| 5% | $0.205 | $0.23 |

The $1 minimum commission matters only for small orders, where it adds
`$2 / notional` to the round trip. It adds at most 0.03% to a default-sized
position ($100,000 × 0.5% ÷ 8% = $6,250). The smallest position the sizer
permits ($500) pays 0.40%. At the 1% threshold, that $500 position passes at
default costs (0.53%) and is refused under stress costs (1.05%). Stress costs
refuse any position below $571 on which the minimum binds. No current run has one: the smallest
entry in all 7,050 was $4,047.

### How often it happens: in the results

The replays cover every configuration behind the `stopladder_*` and
`lowvol_bt_*` result files: base costs, recovery 1.0, both minimum-price
settings, 40 runs. Counts are **executed entry fills, per run**. LADDER and HOLD
share a selection, so most entries appear once in each. Cost is priced at the
fill price.

| | fills | under $5 | under $1 | round trip over 1% | over 2% | over 5% | over 100% | worst |
|---|---|---|---|---|---|---|---|---|
| 2010–19, no min price | 2,088 | 90 | 12 | 18 | 6 | 4 | **4** | 9,994% |
| 2020–25, no min price | 3,306 | 183 | 30 | 36 | 16 | 6 | 0 | 18.6% |
| 2010–19, $5 min | 3,744 | 0 | 0 | 0 | 0 | 0 | 0 | 0.33% |
| 2020–25, $5 min | 3,306 | 0 | 0 | 0 | 0 | 0 | 0 | 0.33% |

**The catastrophic case is rare, and when it happens it is final.** Before the
minimum price, 4 of 5,394 entry fills cost more than the position. They are the
two entries above, once in each arm, and each one on its own took a $100,000
account below zero. Another 50 fills cost between 1% and 18.6%: seven other
2010–19 entries and eighteen 2020–25 entries, each appearing in both arms. None
of them came from the CALM arm.

### What the guard changes: measured

Every configuration was re-run with the guard on at 1% and at 2%.

| | guard 1% | guard 2% |
|---|---|---|
| current runs ($5 min): identical to guard off | **20 / 20** | **20 / 20** |
| no min price: identical to guard off | 4 / 20 | 8 / 20 |
| 2010–19 s2 LADDER: CAGR, lowest equity | −100% → **+3.3%**, $94,124 | +2.8%, $93,199 |
| 2010–19 s2 HOLD | −100% → **+4.2%**, $92,312 | +3.7%, $90,011 |
| 2010–19 s3 LADDER | −100% → **+5.9%**, $99,105 | +5.6%, $99,319 |
| 2010–19 s3 HOLD | −100% → **+6.0%**, $99,532 | +5.1%, $99,319 |

The non-identical runs without a minimum price differ because refused entries
are replaced by the next-ranked candidates. For example, 2010–19 sample 0 at 1%
refuses 9091 at $1.13 and 2664 at $0.78, and buys 1653 at $17.79 and 4710 at
$85.73 in their place.

### Why 1%

1. **It is inert on everything the platform means to trade.** The platform
   declares a $5 minimum price. At $5 the round trip is 0.33% at default costs
   and 0.85% under stress. A 1% guard refuses no ordinary-sized position at or
   above the declared floor. It is a backstop below it, not a second universe
   filter.
2. **It survives the stress-cost arm, and 0.5% does not.** Registrations test
   robustness at 5× spread and slippage. Those alone cost 0.65% round trip. A
   0.5% guard would refuse every stress-arm entry and void the check. 1% leaves
   0.35 percentage points for commission.
3. **It is proportionate to the stop.** `ExitConfig.max_initial_stop_pct` is 8%,
   so a 1% round trip is already an eighth of the trade's risk (R) before the
   price moves. At 2%, a quarter of R is gone on entry.
4. **2% would also have prevented both crashes** and is equally inert today. The
   case for 1% over 2% is points 2 and 3, not the evidence. If the owner prefers
   the looser backstop, 2% is defensible.

### What the change is

- `TransactionCostRule` (`src/tradeit/risk/rules.py`) is a `RiskRule`. It
  prices a buy and a sell of the proposed quantity at the entry price through
  `ParticipationCostModel.estimate_at`. That is the arithmetic the backtest
  charges, not a second formula. If the round trip is more than the threshold
  as a fraction of notional, it **refuses**. It never reduces: per-share costs
  are the same fraction at any size, and the minimum commission makes smaller
  orders dearer.
- `ParticipationCostModel.estimate_at` is `estimate` taken from a price instead
  of a bar. `estimate` now delegates to it, and a test pins the two as equal.
- `RiskConfig.max_round_trip_cost_pct: float | None`, default `0.01` since
  approval (first committed as `None`). `None` omits the field from
  `StrategyConfig.to_payload`, so a strategy with the guard off keeps its
  pre-guard digest (checked against the pre-guard HEAD for the defaults and for
  `config/strategies/baseline.toml`). When it is set, it is part of the digest.
- `build_engine` adds the rule unless the field is `None`.
- `RiskLimitType.TRANSACTION_COST` is added. The enum is not persisted, so no
  migration is needed.

### What it does not do

- **It does not see market impact.** A `SizingDecision` carries no dollar
  volume, so the estimate is commission, spread and slippage only: a floor on
  cost, not a ceiling. The backtest engine charges no impact either, since it
  calls `estimate` without volume. The two agree.
- **It prices the proposal, not a later reduction.** If another rule cuts the
  quantity into minimum-commission territory, the guard does not re-price it.
  Under default costs, a reduction pushes an entry over 1% only if it leaves
  less than $230 of notional. That is below the $500 minimum, but `PortfolioCycle`
  does not re-apply the minimum after a risk-rule reduction, so it can happen.
  None did in any run here.
- **`scripts/backtest_research01.py` assembles its own rules** and would not pick
  the guard up. `build_engine` is the only assembly in `src/`.
- **It does not repair the liquidity reading.** A $64.8 billion "average dollar
  volume" on a placeholder print is a separate defect in how dollar volume is
  read at sub-penny prices. It is not addressed here.

### What turning it on by default changed

- **Every strategy's digest.** With the default at `0.01`, the field is in every
  payload. Configurations whose behaviour is identical hash differently from
  the manifests of every run before the change. A run that must reproduce an
  old digest sets the field to `None`.
- **No open registration.** The stop-ladder verdict had already been read (§38,
  INCONCLUSIVE) when the guard was switched on. The one registration still
  awaiting results, FUNDAMENTALS_12M, is a signal study that builds no trading
  engine and reads no `StrategyConfig`, so the guard cannot reach it.
  `config/strategies/baseline.toml` states the new default.
- **Two older runners were not measured.** `backtest_rs250_gate.py` and
  `backtest_survivorship.py` also assemble through `build_engine` and apply no
  minimum price, so a re-run of either now includes the guard and may differ
  from its recorded result wherever it bought below about $1.15. To reproduce a
  recorded result exactly, set `max_round_trip_cost_pct` to `None`.
  `backtest_volatility_sizing.py` applies the $5 minimum, where the guard has
  never bound.

### Alternative considered: a minimum price in the sizer

A sizer refusing prices below `LiquidityConfig.min_price` ($5) would also have
prevented both crashes. It is blunter:

- Without a minimum price it would remove the 273 fills priced under $5,
  against 54 for the 1% guard.
- It does not follow `CostConfig`: at zero commission a $3 stock costs almost
  nothing, and it would still be refused.
- It duplicates a universe decision that `FactorTilt` already makes for
  itself.

The cost rule states the invariant directly: **do not pay more to trade a
position than the position can plausibly earn.** A price floor only
approximates that.

### Reproducing

```
.venv/bin/python scripts/backtest_cost_guard_measure.py run --window primary --sample 2
.venv/bin/python scripts/backtest_cost_guard_measure.py run --window primary --sample 2 --guard 0.01 0.02
.venv/bin/python scripts/backtest_cost_guard_measure.py summarise
```

Run it from the repository root, for all four samples of both windows. It opens
`research01.sqlite` read-only and writes to `TradeItData/out/costguard/`. Tests
are in `tests/unit/test_risk_transaction_cost.py`. One of them replays 9106's
entry through the real sizer, which reproduces the 62,370,683 shares bound by
`risk_per_trade`, and shows the guard refusing it. Another runs it through the
assembled engine: equity goes below zero with the guard off and stays at
$100,000 with it on.
