#!/usr/bin/env python
"""Measure how often an entry's cost approaches its value, and what a guard changes.

The evidence behind ``docs/PROPOSAL_TRANSACTION_COST_GUARD_2026-09-18.md``.
Replays the stop-ladder registration's runs (2010-2019, RANDOM, LADDER and
HOLD) and §37's low-volatility runs (2020-2025, CALM and RANDOM, plus the
stop-ladder diagnostic's HOLD), base costs and recovery 1.0, recording every
executed BUY fill. Each is run twice over -- with no minimum price, which is
how the two samples crashed, and with the $5 minimum the runners use now --
and, with ``--guard``, again with ``RiskConfig.max_round_trip_cost_pct`` set.

Opens the corpus **read-only**. Writes to ``--out``, never to a registered
result file. ``run`` records one sample; ``summarise`` reads a directory of
them. It measures and changes nothing, so it decides nothing either.

    python scripts/backtest_cost_guard_measure.py run --window primary --sample 2
    python scripts/backtest_cost_guard_measure.py run --window primary --sample 2 \\
        --guard 0.01 0.02
    python scripts/backtest_cost_guard_measure.py summarise
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_lowvol_tilt import (
    CAPITAL,
    DELISTING_AFTER,
    FLOOR,
    HORIZON,
    MIN_PRICE,
    PARTICIPATION,
    RISK_FREE,
    SEED,
    _manifest,
    samples,
)
from backtest_stop_ladder import WINDOWS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.baselines import build_engine
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.factor import FactorTilt, Selection
from tradeit.core.calendar import get_calendar
from tradeit.core.enums import OrderSide
from tradeit.core.models import OhlcvBar
from tradeit.execution.base import CostEstimate, Fill, FillModel, OrderRequest
from tradeit.signals.sampling import common_grid
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.config import CostConfig, StrategyConfig

DB = "sqlite:///file:research01.sqlite?mode=ro&uri=true"
OUT = Path("/Users/ericsasson/Documents/TradeItData/out/costguard")
#: (selection, exits) per window: the registered arms and §37's.
ARMS = {
    "primary": (("random", "ladder"), ("random", "hold")),
    "diagnostic": (("calm", "ladder"), ("random", "ladder"), ("random", "hold")),
}
#: A positive floor is required; this one admits every print, as before 29111dc.
MIN_PRICES = {"none": Decimal("1e-12"), "5": MIN_PRICE}
KEY = ("guard", "window", "sample", "selection", "arm", "min_price")
THRESHOLDS = (Decimal("0.005"), Decimal("0.01"), Decimal("0.02"), Decimal("0.05"), Decimal(1))
STRESS = {"spread_bps": 15.0, "slippage_bps": 25.0}


@dataclass
class _Recorder:
    """The fill model, unchanged, with every executed buy written down."""

    inner: FillModel
    rows: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def parameters(self) -> dict[str, object]:
        return self.inner.parameters

    def simulate(self, order: OrderRequest, bar: OhlcvBar, cost: CostEstimate) -> Fill | None:
        fill = self.inner.simulate(order, bar, cost)
        if fill is not None and fill.side is OrderSide.BUY:
            self.rows.append(
                (
                    fill.filled_at.date().isoformat(),
                    str(fill.instrument_id),
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.commission),
                )
            )
        return fill


def run(args: argparse.Namespace) -> int:
    window = WINDOWS[args.window]
    session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    universe = samples(list(json.loads(window.population.read_text())))[args.sample]
    data = CorpusSessionData(
        session=session,
        universe=tuple(universe),
        start=window.warmup_from,
        end=window.end,
        candidate_source=lambda d, b: [],
    )
    rebalance = frozenset(
        common_grid(get_calendar(), window.start, window.end, HORIZON, HORIZON).dates
    )
    warmup = [d for d in data.sessions(window.warmup_from, window.end) if d < window.start]

    args.out.mkdir(parents=True, exist_ok=True)
    tag = "guard" if args.guard else "off"
    stem = f"{tag}_{args.window}_s{args.sample}"
    with (
        (args.out / f"fills_{stem}.csv").open("w", newline="") as fills_file,
        (args.out / f"runs_{stem}.csv").open("w", newline="") as runs_file,
    ):
        fills, runs = csv.writer(fills_file), csv.writer(runs_file)
        fills.writerow((*KEY, "date", "instrument_id", "quantity", "price", "commission"))
        runs.writerow((*KEY, "cagr", "trades", "min_equity", "final_equity"))
        for guard in args.guard or [None]:
            for price_label, min_price in MIN_PRICES.items():
                for selection, arm in ARMS[args.window]:
                    tilt = FactorTilt(
                        selection=Selection(selection),
                        rebalance_dates=rebalance,
                        splits_on=data.splits_on,
                        volatility_lookback=60,
                        fraction=0.2,
                        min_history=100,
                        min_price=min_price,
                        min_dollar_volume=FLOOR,
                        dollar_volume_lookback=20,
                        max_atr_percent=1.0,
                        stop_pct=Decimal("0.08"),
                        seed=SEED,
                    )
                    for day in warmup:
                        tilt.observe(day, data.bars(day))
                    data.candidate_source = tilt
                    config = StrategyConfig(
                        name=f"costguard_{selection}_{arm}",
                        exits={"time_stop_sessions": HORIZON, "price_exits": arm == "ladder"},
                        sizing={"allow_pyramiding": False},
                        risk={} if guard is None else {"max_round_trip_cost_pct": guard},
                    )
                    label = f"{args.window[:4]}-s{args.sample}-{selection}-{arm}-p{price_label}"
                    engine = build_engine(
                        config,
                        data,
                        _manifest(config, label, data.bar_count),
                        participation=PARTICIPATION,
                        risk_free_rate=RISK_FREE,
                        delisting_after_sessions=DELISTING_AFTER,
                        delisting_recovery=Decimal("1.0"),
                    )
                    recorder = _Recorder(inner=engine.fills)
                    result = dataclasses.replace(engine, fills=recorder).run(
                        BacktestSpec(
                            name=f"costguard/{label}",
                            start=window.start,
                            end=window.end,
                            universe=f"{args.window}-sample-{args.sample}",
                            initial_capital=CAPITAL,
                            strategy_config_digest=config.digest,
                            cost_model="participation",
                            fill_model="bar",
                        )
                    )
                    row = (guard or "off", args.window, args.sample, selection, arm, price_label)
                    fills.writerows((*row, *fill) for fill in recorder.rows)
                    curve = [equity for _, equity in result.equity_curve]
                    cagr = "" if result.metrics is None else f"{result.metrics.cagr:.6f}"
                    runs.writerow((*row, cagr, len(result.trades), min(curve), curve[-1]))
                    print(f"  {label} guard={guard}: {len(result.trades)} trades", flush=True)
    return 0


def _round_trip(quantity: Decimal, price: Decimal, costs: CostConfig) -> Decimal:
    """What ``TransactionCostRule`` charges, at the fill price."""
    friction = Decimal(str(costs.spread_bps)) / 2 + Decimal(str(costs.slippage_bps))
    leg = max(
        Decimal(str(costs.commission_per_share)) * quantity,
        Decimal(str(costs.commission_minimum)),
    ) + quantity * price * friction / Decimal(10000)
    return 2 * leg / (quantity * price)


def summarise(args: argparse.Namespace) -> int:
    fills = [r for f in sorted(args.out.glob("fills_off_*.csv")) for r in csv.DictReader(f.open())]
    base, stress = CostConfig(), CostConfig(**STRESS)
    print("ENTRY FILLS -- round-trip cost as a fraction of notional, at the fill price")
    for window in ("primary", "diagnostic"):
        for price_label in MIN_PRICES:
            rows = [r for r in fills if r["window"] == window and r["min_price"] == price_label]
            if not rows:
                continue
            q = [(Decimal(r["quantity"]), Decimal(r["price"])) for r in rows]
            b = [_round_trip(n, p, base) for n, p in q]
            s = [_round_trip(n, p, stress) for n, p in q]
            print(
                f"  {window:<10} min price {price_label:>4}: {len(rows):>5} fills, "
                f"{sum(p < 5 for _, p in q)} under $5, {sum(p < 1 for _, p in q)} under $1; "
                f"max {max(b):.2%} base, {max(s):.2%} stress"
            )
            for t in THRESHOLDS:
                print(
                    f"      above {t:>6.1%}: {sum(x > t for x in b):>4} base  "
                    f"{sum(x > t for x in s):>5} stress"
                )
    runs: dict[tuple[str, ...], dict[str, str]] = {}
    for path in sorted(args.out.glob("runs_*.csv")):
        for r in csv.DictReader(path.open()):
            runs[tuple(r[k] for k in KEY)] = r
    same: collections.Counter[tuple[str, str]] = collections.Counter()
    total: collections.Counter[tuple[str, str]] = collections.Counter()
    print("\nRUNS WITH THE GUARD ON, against the same run with it off")
    for key, on in sorted(runs.items()):
        if key[0] == "off" or ("off", *key[1:]) not in runs:
            continue
        off = runs[("off", *key[1:])]
        fields = ("cagr", "trades", "min_equity")
        total[(key[0], key[-1])] += 1
        if all(on[f] == off[f] for f in fields):
            same[(key[0], key[-1])] += 1
        elif Decimal(off["min_equity"]) < 0:
            print(
                f"  {'-'.join(key[1:])} guard {key[0]}: min equity {off['min_equity'][:12]} -> "
                f"{on['min_equity'][:12]}, CAGR {off['cagr']} -> {on['cagr']}"
            )
    for group, n in sorted(total.items()):
        print(f"  guard {group[0]}, min price {group[1]}: {same[group]}/{n} runs identical")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", default=DB)
    r.add_argument("--window", required=True, choices=sorted(WINDOWS))
    r.add_argument("--sample", type=int, required=True, choices=range(4))
    r.add_argument("--guard", type=float, nargs="*", default=[])
    sub.add_parser("summarise")
    args = ap.parse_args()
    return run(args) if args.command == "run" else summarise(args)


if __name__ == "__main__":
    raise SystemExit(main())
