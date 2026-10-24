# The cost stress test, run at last — and what it actually measures

`cost_sensitivity` asks *"does the edge survive a doubling of modelled costs?"*
Its threshold has always been `WRITTEN` — `execution/base.py` says *"a strategy
that trades often can be profitable at 2 basis points of slippage and
unprofitable at 15, and the difference between those two numbers is entirely a
modelling assumption"* — so the criterion needed a **measurement**, not a
decision. It had never had one.

Run 2026-10-23, on `ma_cross_baseline` over 2010-01-04 to 2019-12-31, the first
runs of this harness since [ADR-0028](adr/0028-market-impact-is-charged.md) made
market impact reachable.

| | declared model | every cost ×2 | change |
|---|---|---|---|
| CAGR | 0.61% | 0.52% | **−0.09 pp** |
| Sharpe | −0.84 | −0.89 | −0.05 |
| max drawdown | 3.02% | 3.10% | +0.08 pp |
| win rate | 54.17% | 52.08% | −2.09 pp |
| trades | 96 | 96 | — |
| turnover | 1.18×/yr | 1.18×/yr | — |

## The criterion cannot be answered for this strategy, and that is the finding

**There is no edge to stress.** The baseline is already `below_hurdle` with a
Sharpe of −0.84 at the *declared* model. Doubling costs moves it from not
working to not working slightly more, so recording `survives_double_costs =
False` would be true in letter and misleading in substance: it would read as
"costs destroyed the edge" when the edge was never there.

So the criterion stays **unmeasured** in `readiness_report.py`, with this
document as the reason. That is the `UNRESOLVED`-is-first-class discipline
applied to a readiness criterion: a question that is ill-posed for a given
candidate gets no answer rather than a convenient one.

## The transferable result: sensitivity is a function of turnover

**A doubling of every modelled cost costs this strategy 9 basis points of
CAGR.** That is small, and the reason is visible in the table: turnover is
**1.18×/yr**, unchanged between the runs. Costs are charged per trade, so a
strategy that trades a little pays a little, however the model is scaled.

Which means `execution/base.py`'s warning is about **high-turnover** strategies
specifically, and this criterion will only bite on candidates that trade often.
A calibration point for reading it later:

> At 1.18 turns a year, ×2 on commission, spread, slippage and the impact
> coefficient together is worth **−0.09 pp of CAGR and −0.05 of Sharpe**.

A candidate turning over, say, 20× a year would face roughly seventeen times
that drag, which is the regime where the criterion earns its place. **Do not
read a pass on a low-turnover strategy as evidence the cost model is
conservative** — it is evidence that the strategy barely trades.

## What had to be fixed to run it at all

**`scripts/backtest_research01.py` could not run.** `EventDrivenEngine` made
`delisting_after_sessions` and `delisting_recovery` required; seven of its eight
callers were updated and this one — the primary single-run harness — was not.
It raised `TypeError` before loading a bar.

Nothing caught it: `scripts/` is outside CI's type-check scope and no test
constructs the engine from there. **The criterion was unmeasured partly because
the tool was broken**, and the tool was broken because nothing exercises it.

The two values are copied from the other seven callers rather than chosen —
they are delisting assumptions, and inventing one here would need a scoped
proposition. `--cost-multiplier` is new, scales all cost components together,
and prints a banner saying the run is not the declared model, so a stress run's
equity curve cannot be mistaken for a real one.

## What this is not

**Not evidence of anything about profitability.** Both runs print the
survivorship gate's own verdict — `SURVIVOR_BIASED` against this corpus, the
companies that failed substantially absent — and both are `below_hurdle` on the
trial check. The runs exercise the machinery and calibrate the cost model's
reach. Nothing here moves a strategy toward capital.
