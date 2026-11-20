#!/usr/bin/env python
"""The four risk rules that have never been installed: can they be, and what do they change?

`decide/plan.py` reports controls that cannot fire, and four of them are risk
rules declared in `risk/rules.py` and `risk/contextual.py` that no script
installs: `PyramidRule`, `CorrelationRule`, `DrawdownHaltRule`, `DailyLossRule`.
Arming them changes what a backtest measures, which `CLAUDE.md` places behind an
explicit scoped proposition. This produces the evidence that proposition needs.

**Two of the four cannot be installed as written, and that is a design finding
rather than a configuration choice.** `DrawdownHaltRule.peak_equity` and
`DailyLossRule.session_open_equity` are **constructor fields**, while
`MostRestrictiveEngine.rule_set` is a frozen tuple built once per run and never
rebuilt per session. So a static value is all they can ever see:

* a drawdown halt constructed with initial capital measures drawdown from
  *inception*, not from the running peak — a different and weaker rule that
  stops firing the moment the account is above where it started;
* a daily loss limit constructed with initial capital measures loss since
  *inception*, not since today's open, which is not a daily loss limit at all.

Neither is a bug in the rules: both are correct functions of the state they are
given, and both expect a caller that supplies that state per session. Nothing
does. Installing them anyway would produce a number that looks like a measured
risk control and is not one.

The other two are installable and are measured here, as an A/B differing in
exactly one thing.

What this is **not**
--------------------

Not a trial. No hypothesis about returns is registered and the ledger does not
move. The return figures below are in sample, on one universe and one window,
and **must not be cited as a result**. The question is whether each rule binds
on real evidence and what it costs when it does.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import itertools
import sys
import time
from decimal import Decimal

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import base_rules
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.decide import resolve_universe
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.base import RiskDecision, RiskRule
from tradeit.risk.contextual import CorrelationRule, MatrixCorrelationSource
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import PyramidRule
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

#: Names that resolve to the **same** security at both ends of 2010-2019.
#: `backtest_correlation_limit.py`'s default list includes DIS, T and XOM,
#: which do not — DIS is claimed by two securities at once and the other two
#: hand their ticker on mid-window. Resolution is per date
#: (DATA_DICTIONARY §0.2), and a universe that silently changes company
#: mid-window measures something nobody meant.
STABLE = (
    "AAPL,ADBE,AMAT,AMD,CAT,COST,CSCO,GE,HD,IBM,INTC,JNJ,KO,MCD,MMM,"
    "MRK,MSFT,NKE,NVDA,PFE,PG,QCOM,SBUX,TXN,UNP,UPS,WMT"
)


def build_correlations(
    data: CorpusSessionData, sessions: list[dt.date], lookback: int
) -> dict[tuple[int, int], float]:
    """Pairwise correlation of daily returns over the first ``lookback`` sessions.

    Computed once and held fixed — the same stated simplification
    ``backtest_correlation_limit.py`` makes, and for the same reason: the
    question is whether the rule binds, not what it earns.
    """
    span = sessions[:lookback]
    closes: dict[int, dict[dt.date, float]] = {}
    for day in span:
        for instrument_id, bar in data.bars(day).items():
            closes.setdefault(instrument_id, {})[day] = float(bar.close)
    series: dict[int, np.ndarray] = {}
    for instrument_id, by_day in closes.items():
        values = np.array([by_day[d] for d in span if d in by_day])
        if len(values) < lookback // 2 or (values <= 0).any():
            continue
        series[instrument_id] = np.diff(values) / values[:-1]
    out: dict[tuple[int, int], float] = {}
    for a, b in itertools.combinations(sorted(series), 2):
        x, y = series[a], series[b]
        n = min(len(x), len(y))
        if n < 20:
            continue
        x, y = x[-n:], y[-n:]
        if x.std() == 0 or y.std() == 0:
            continue
        out[(a, b)] = float(np.corrcoef(x, y)[0, 1])
    return out


@dataclasses.dataclass
class Counting:
    """Wraps a rule and counts how often it allowed and rejected.

    **A rule binds by resizing at least as often as by refusing**, and a trade
    count alone cannot see that — §57 records the first version of a sector-cap
    test making exactly that mistake. ``PyramidRule`` here changes the return
    while leaving the trade count untouched, because it governs *adds* to a
    position that already exists: a rejected add is not a missing trade, it is a
    smaller one.
    """

    inner: RiskRule
    allowed: int = 0
    rejected: int = 0
    reasons: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def parameters(self) -> dict[str, object]:
        return self.inner.parameters

    def evaluate(self, proposal: object, portfolio: object) -> object:
        verdict = self.inner.evaluate(proposal, portfolio)  # type: ignore[arg-type]
        if verdict.decision is RiskDecision.REJECT:
            self.rejected += 1
            key = verdict.reason.split(";")[0][:60]
            self.reasons[key] = self.reasons.get(key, 0) + 1
        else:
            self.allowed += 1
        return verdict


def report_uninstallable() -> None:
    """State the structural finding before any number is produced."""
    print("=" * 78)
    print("NOT INSTALLABLE AS WRITTEN -- a design gap, not a configuration choice")
    print("=" * 78)
    for name, field, consequence in (
        (
            "DrawdownHaltRule",
            "peak_equity",
            "measures drawdown from INCEPTION, not from the running peak; stops "
            "firing entirely once the account is above where it started",
        ),
        (
            "DailyLossRule",
            "session_open_equity",
            "measures loss since INCEPTION, not since today's open -- which is "
            "not a daily loss limit",
        ),
    ):
        print(f"\n{name}")
        print(f"  `{field}` is a constructor field, and MostRestrictiveEngine.rule_set is")
        print("  built once per run and never rebuilt per session, so it can only ever")
        print("  hold a static value.")
        print(f"  Installed anyway, it {consequence}.")
    print("\nNeither is a bug in the rule: both are correct functions of the state they")
    print("are given, and both expect a caller that supplies it per session. Arming")
    print("them needs that caller first, which is a change to the engine's contract.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--tickers", default=STABLE)
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.02)
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, default=Decimal(1))
    ap.add_argument("--lookback", type=int, default=126)
    args = ap.parse_args()

    report_uninstallable()

    engine_ = create_engine(args.db)
    install_sqlite_busy_timeout(engine_)
    session: Session = sessionmaker(bind=engine_)()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())

    # Per date, at BOTH ends: a name that changes company mid-window measures
    # something nobody meant.
    at_start, _ = resolve_universe(session, tickers, start)
    at_end, why_not = resolve_universe(session, tickers, end)
    resolved = {t: i for t, i in at_start.items() if at_end.get(t) == i}
    dropped = sorted(set(tickers) - set(resolved))
    print("=" * 78)
    print("INSTALLABLE -- measured as an A/B differing in exactly one rule")
    print("=" * 78)
    print(f"universe: {len(resolved)} of {len(tickers)} resolve to one security at both ends")
    if dropped:
        print(f"  dropped: {', '.join(dropped)}")
        for ticker in dropped:
            if ticker in why_not:
                print(f"    {ticker}: {why_not[ticker]}")

    config = StrategyConfig(name="ma_cross_baseline")

    def make_data() -> CorpusSessionData:
        rule = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
        data = CorpusSessionData(
            session=session,
            universe=tuple(sorted(resolved.values())),
            start=start,
            end=end,
            candidate_source=rule,
        )
        rule.splits_on = data.splits_on
        return data

    data = make_data()
    sessions = list(data.sessions(start, end))
    print(f"  {data.bar_count:,} raw bars over {len(sessions):,} sessions")

    matrix = build_correlations(data, sessions, args.lookback)
    limit = config.risk.max_correlation_for_new_position
    over = sum(1 for v in matrix.values() if v > limit)
    print(f"  {len(matrix):,} correlation pairs, {over:,} over the {limit:.2f} limit")
    source = MatrixCorrelationSource(values=matrix)

    def run(
        label: str, extra: tuple[RiskRule, ...]
    ) -> tuple[int, float, float, float | None, float]:
        run_data = make_data()
        cycle = PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing,
                risk=config.risk,
                max_participation=args.participation,
                costs=config.costs,
            ),
            engine=MostRestrictiveEngine(
                rule_set=(*base_rules(config), *extra), sizing=config.sizing
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        )
        engine = EventDrivenEngine(
            cycle=cycle,
            costs=ParticipationCostModel(config=config.costs),
            fills=BarFillModel(max_participation=args.participation),
            data=run_data,
            analyzer=StandardPerformanceAnalyzer(
                annualisation_factor=config.indicators.annualisation_factor,
                risk_free_rate=args.risk_free,
            ),
            delisting_after_sessions=args.delisting_after,
            delisting_recovery=args.delisting_recovery,
            manifest=_manifest(config, dt.datetime.now(dt.UTC), run_data.bar_count),
        )
        spec = BacktestSpec(
            name=f"unarmed-rules-{label}",
            start=start,
            end=end,
            universe="stable-27",
            initial_capital=args.capital,
            strategy_config_digest=config.version(dt.datetime.now(dt.UTC)).digest,
            cost_model="participation",
            fill_model="bar",
        )
        t0 = time.time()
        result = engine.run(spec)
        took = time.time() - t0
        trades = len(result.trades)
        metrics = result.metrics
        # `total_return_pct` is a FRACTION despite its name: 0.1829 is 18.29%.
        ret = float(metrics.total_return_pct)
        cagr = float(metrics.cagr)
        drawdown = float(metrics.max_drawdown_pct)
        sharpe = None if metrics.sharpe is None else float(metrics.sharpe)
        shown = "    n/a" if sharpe is None else f"{sharpe:7.3f}"
        print(
            f"  {label:<28} {trades:4} trades   CAGR {cagr:>6.2%}   "
            f"maxDD {drawdown:>7.2%}   Sharpe {shown}   [{took:.1f}s]"
        )
        return trades, ret, drawdown, sharpe, cagr

    print("\nbaseline and each rule, one at a time:")
    base = run("none (the archive's set)", ())
    counted_pyramid = Counting(PyramidRule(config=config.sizing))
    pyr = run("+ PyramidRule", (counted_pyramid,))  # type: ignore[arg-type]
    counted_correlation = Counting(CorrelationRule(config=config.risk, source=source))
    cor = run("+ CorrelationRule", (counted_correlation,))  # type: ignore[arg-type]
    both = run(
        "+ both",
        (
            PyramidRule(config=config.sizing),
            CorrelationRule(config=config.risk, source=source),
        ),
    )

    print("\nhow often each rule was consulted, and what it said:")
    for counted in (counted_pyramid, counted_correlation):
        total = counted.allowed + counted.rejected
        print(f"  {counted.name:<22} {total:5} proposals   {counted.rejected:4} rejected")
        for reason, count in sorted(counted.reasons.items(), key=lambda kv: -kv[1]):
            print(f"      {count:4}  {reason}")

    # **Judged on drawdown as well as return.** A risk control exists to reduce
    # the loss, usually at some cost in return, so reporting return alone would
    # make a rule that is doing exactly its job look pointless.
    print("\nwhat each changed, against the archive's set:")
    for label, arm in (("PyramidRule", pyr), ("CorrelationRule", cor), ("both", both)):
        d_trades = arm[0] - base[0]
        d_ret = (arm[1] - base[1]) * 100  # fraction -> percentage points
        d_dd = (arm[2] - base[2]) * 100
        d_sharpe = None if arm[3] is None or base[3] is None else arm[3] - base[3]
        sharpe_shown = "    n/a" if d_sharpe is None else f"{d_sharpe:+7.3f}"
        unbound = d_trades == 0 and abs(d_ret) < 0.005 and abs(d_dd) < 0.005
        verdict = "NEVER BOUND" if unbound else ""
        print(
            f"  {label:<18} {d_trades:+4} trades   return {d_ret:+6.2f} pp   "
            f"maxDD {d_dd:+6.2f} pp   Sharpe {sharpe_shown}   {verdict}"
        )

    print(
        "\nIn sample, one universe, one window, unregistered. Not a result, and not\n"
        "evidence about profitability. What it says is whether each rule binds."
    )
    print(
        "\nAnd the number that dwarfs all of the above: the baseline earns "
        f"{base[4]:.2%} a year\nat a Sharpe of {base[3]:.3f} over ten years. "
        "Every rule here moves the result by a\nfraction of a percentage point "
        "on a strategy that does not beat cash. Arming them\nis housekeeping; "
        "it is not the thing standing between this system and a return."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
