# Phase 9 closed — what the audit found, and what it cost

Audited and closed 2026-10-17, under the delivery plan of the same week.
**Phase 9 was not three weeks of work. It was five modules already complete,
three built-but-never-run, and one promise the module made and did not keep.**

## The audit

| module | tests | run on real data before | state |
|---|---|---|---|
| `engine` | 33 | yes | complete |
| `corpus` | 17 | yes, 7 scripts | complete |
| `performance` | 31 | yes | complete |
| `overfitting` | 25 | yes, 22 scripts | complete |
| `factor` | — | yes, 3 scripts | complete |
| `walkforward` | 18 | **never** | **now exercised** |
| `montecarlo` | 16 | **never** | **now exercised** |
| `sensitivity` | 19 | **never** | **now exercised** |
| `baselines` | 3 | yes | **1 of 4 baselines existed** |

The roadmap's Phase 9 requires an event-driven backtester, walk-forward, Monte
Carlo on trade sequence *and* parameter perturbation, attribution, and its own
overfitting controls. **Every one of those was built.** The gap was never
design; it was that a third of it had only ever seen scripted bars — the same
gap Phase 5's breakout engine carried until it met real prices this month and
worked.

## One audit correction

The audit first recorded `parameter_perturbation` as a gap, because
`montecarlo.py` raises on it. It is not a gap: `sensitivity.py` **is** the
re-runner, and `montecarlo.py` refuses the method and points there because it
is handed a finished result and cannot re-run anything. The two are a designed
pair. Recorded because an audit that invents a gap is as unhelpful as one that
misses a real one.

## What running them found

Two defects that 53 passing unit tests did not.

**`WalkForwardReport.consistency` had no `@property`** while its four siblings
did. The first caller from outside the tests formatted the bound method and got
a `TypeError`. Eighteen tests missed it because they all called it correctly —
nothing had ever read the class from outside.

**`total_return_pct` holds a fraction despite its name.** The first
walk-forward output read `0.02%` for a return of `2.40%`: a hundredfold
understatement, harmless in a smoke test and not harmless in a conclusion.

## Three baselines that did not exist

`baselines.py` promised, in its own docstring, *"buy and hold, the benchmark, a
moving-average rule, a naive classifier"* — and explained why they must be
runnable strategies rather than numbers somebody quotes. **It shipped one.**

The consequence reaches every measurement so far: **§44 through §53 never
compared themselves against doing nothing.** Their control was a placebo — a
random security, same session, same stop distance — which answers *did this
beat a coin toss* and not *did this beat inaction*. The second question decides
whether the trading costs were worth paying, and it was never asked.

Now implemented, sharing one stop rule so a difference between them is a
difference in selection rather than in risk management:

* **`BuyAndHold`** — each name bought once, never again. The floor.
* **`EqualWeightBenchmark`** — re-offers the universe on a cadence. Distinct
  because buy-and-hold lets winners grow into the portfolio and is therefore an
  undeclared momentum bet.
* **`NaiveMomentumClassifier`** — "it went up yesterday". If twelve geometric
  conditions cannot beat one bar of sign, the geometry is decoration.

Ten tests, up from three, and four mutations caught — each one collapsing a
baseline into another, which is the failure that would have made four baselines
secretly two.

## What Phase 9 does **not** deliver

**It does not make the per-trade route work.** The extended-window measurement
of the same day showed block variance rising with the added years, so effects
of 0.1–0.8% remain below what 1.0–2.6% block variance resolves at any window
length this corpus offers.

**It does not re-run §44–§53 against the new baselines.** Those studies are
closed by their own stop rules and re-opening them to add a comparison would
reverse written decisions. The baselines exist for everything measured *after*
today, and the gap in the earlier work is recorded rather than patched.

## Phase 9: COMPLETE

Nine modules, 162 unit tests plus the seven added today, every roadmap
requirement built and now every module exercised on real data.
