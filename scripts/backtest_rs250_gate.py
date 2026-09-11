#!/usr/bin/env python
"""Does refusing the weakest fifth by 250-session relative strength help?

**A gate is a portfolio claim**, so it is tested the way a portfolio would feel
it: the same rule, the same universe, the same costs, run twice on the same
sample -- once refusing candidates in the bottom quintile of ``pct_250`` on the
session they are decided, once not. Paired, so the only difference is the gate.

Specification and criteria: ``docs/prereg/RS250_GATE_2026-09-12.md``, committed
before the first run. The threshold is 0.20 and is not tuned. A candidate whose
rank is unknown is **not** refused -- the gate does not apply to it -- and both
counts are reported, because "no rank" and "a bad rank" are different facts.

Four disjoint samples, because §15 established that one sample's number carries
noise of the size being measured, and two recovery assumptions, because §16's
per-security classification covers the 2000s population only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_survivorship import _arms, _manifest, _spans
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestResult, BacktestSpec
from tradeit.backtesting.baselines import MovingAverageCross, build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.core.models import OhlcvBar
from tradeit.portfolio.cycle import EntryCandidate
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import StrategyConfig

OUT = Path("/Users/ericsasson/Documents/TradeItData/out")
THRESHOLD = 0.20


@dataclass
class GatedCandidates:
    """The baseline rule, with the weakest fifth refused at the gate.

    The inner source is stateful -- it carries its own trailing history -- so it
    is called on **every** session whatever the gate decides. Filtering its
    output is the only thing that happens here: a gate refuses, and can never
    promote, size or exit.
    """

    inner: MovingAverageCross
    ranks: Mapping[tuple[int, dt.date], float]
    threshold: float = THRESHOLD
    offered: int = field(default=0, init=False)
    refused: int = field(default=0, init=False)
    unranked: int = field(default=0, init=False)

    def __call__(
        self, session_date: dt.date, bars: Mapping[int, OhlcvBar]
    ) -> Sequence[EntryCandidate]:
        candidates = self.inner(session_date, bars)
        kept: list[EntryCandidate] = []
        for candidate in candidates:
            self.offered += 1
            rank = self.ranks.get((candidate.instrument_id, session_date))
            if rank is None:
                # Unknown is not bad: the gate does not apply.
                self.unranked += 1
                kept.append(candidate)
            elif rank < self.threshold:
                self.refused += 1
            else:
                kept.append(candidate)
        return kept


def _ranks(path: Path) -> dict[tuple[int, dt.date], float]:
    with path.open() as handle:
        return {
            (int(row["security_id"]), dt.date.fromisoformat(row["session_date"])): float(
                row["pct_250"]
            )
            for row in csv.DictReader(handle)
        }


def _run(
    session: Session,
    config: StrategyConfig,
    universe: list[int],
    label: str,
    args: argparse.Namespace,
    ranks: Mapping[tuple[int, dt.date], float] | None,
) -> tuple[BacktestResult, GatedCandidates | None]:
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    inner = MovingAverageCross(fast=args.fast, slow=args.slow, stop_pct=args.stop_pct)
    source: MovingAverageCross | GatedCandidates = inner
    gate: GatedCandidates | None = None
    if ranks is not None:
        gate = GatedCandidates(inner=inner, ranks=ranks)
        source = gate
    t0 = time.time()
    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=start,
        end=end,
        candidate_source=source,
    )
    spec = BacktestSpec(
        name=f"ma_cross/{label}",
        start=start,
        end=end,
        universe=label,
        initial_capital=args.capital,
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = build_engine(
        config,
        data,
        _manifest(config, label, data.bar_count),
        participation=args.participation,
        risk_free_rate=args.risk_free,
        delisting_after_sessions=args.delisting_after,
        delisting_recovery=args.delisting_recovery,
    )
    result = engine.run(spec)
    print(
        f"  {label:<10} {data.bar_count:>9,} bars  ran in {time.time() - t0:>5.0f}s, "
        f"{len(result.trades):,} trades",
        flush=True,
    )
    return result, gate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument("--spans", type=Path, default=OUT / "security_spans.csv")
    ap.add_argument("--start", default="2020-01-02")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--offset", type=int, required=True)
    ap.add_argument("--map", type=Path, default=None)
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--stop-pct", type=Decimal, default=Decimal("0.08"))
    ap.add_argument("--participation", type=Decimal, default=Decimal("0.02"))
    ap.add_argument("--risk-free", type=float, default=0.03)
    ap.add_argument("--delisting-after", type=int, default=10)
    ap.add_argument("--delisting-recovery", type=Decimal, required=True)
    args = ap.parse_args()

    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    config = StrategyConfig(name="ma_cross_baseline")
    survived, died = _arms(
        _spans(str(args.spans)), args.start, args.end, 250, args.cap, args.offset
    )
    universe = sorted(survived + died)
    ranks = _ranks(args.map or OUT / f"rs250gate_map_{args.offset}.csv")
    print(
        f"sample {args.offset}: {len(universe)} securities "
        f"({len(survived)} survived, {len(died)} died); {len(ranks):,} ranks; "
        f"recovery {args.delisting_recovery}",
        flush=True,
    )

    ungated, _ = _run(session, config, universe, "ungated", args, None)
    gated, gate = _run(session, config, universe, "gated", args, ranks)
    assert gate is not None

    def line(label: str, result: BacktestResult) -> None:
        m = result.metrics
        assert m is not None
        sharpe = "   none" if m.sharpe is None else f"{m.sharpe:>7.2f}"
        print(
            f"  {label:<8}{m.total_return_pct:>10.2%}{m.cagr:>9.2%}"
            f"{m.max_drawdown_pct:>10.2%}{sharpe}{m.win_rate:>9.2%}{m.trade_count:>8,}"
        )

    print(
        f"\n  {'arm':<8}{'return':>10}{'CAGR':>9}{'drawdown':>10}{'sharpe':>7}"
        f"{'win':>9}{'trades':>8}"
    )
    line("ungated", ungated)
    line("gated", gated)
    a, b = ungated.metrics, gated.metrics
    assert a is not None and b is not None
    print(
        f"\n  gate: {gate.offered:,} candidates offered, {gate.refused:,} refused "
        f"({gate.refused / max(1, gate.offered):.1%}), {gate.unranked:,} unranked and "
        f"therefore not gated"
    )
    print(
        f"RESULT,{args.offset},{args.delisting_recovery},{a.total_return_pct:.6f},"
        f"{b.total_return_pct:.6f},{a.cagr:.6f},{b.cagr:.6f},"
        f"{a.max_drawdown_pct:.6f},{b.max_drawdown_pct:.6f},"
        f"{a.trade_count},{b.trade_count},{gate.offered},{gate.refused},{gate.unranked}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
