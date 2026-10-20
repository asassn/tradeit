#!/usr/bin/env python
"""Fill the decision record from a real run, so the assistant has something to read.

Phase 10's onboard assistant "operates over the platform's own structured data
and provenance and explains decisions the system actually made -- it never
invents a reason", and the roadmap is blunt that it "is only as honest as the
record beneath it". The writers were built on 2026-10-19 and the engine got its
recorder seam the same day; **until this script ran, the record was empty.**

Two databases, and that separation is not incidental
----------------------------------------------------

`research01.sqlite` is **read**. The decision record is **written somewhere
else**, by default a separate file. The corpus is 73GB of evidence this project
spent months assembling, several sibling projects read it, and a diagnostic that
wrote into it would be mixing a run's output into the evidence it was measured
against. QuantTrade's own importer opens it read-only for the same reason.

What is recorded, and what the record cannot say
------------------------------------------------

Per session: the scores that were computed, the orders that were placed, the
executions that settled, and the candidates the risk engine refused with the
rule that refused them. **Not** the P&L, which is a property of a closed trade
rather than of a session -- `record_trade` exists for it and the engine's trade
objects are written separately, which is a gap this script states rather than
papers over.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.observability.record import DatabaseRecorder, reconstruct
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import PortfolioCycle
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.reproducibility.versioning import RunManifest
from tradeit.risk.contextual import SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.storage.tables import ArtifactVersion as ArtifactVersionRow
from tradeit.storage.tables import Base
from tradeit.storage.tables import RunManifest as RunManifestRow
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector FROM sectors
    WHERE scheme = 'SIC' AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


def _persist_manifest(session: Session, manifest: RunManifest) -> int:
    """Store the manifest, and the artifact versions it points at, and return its id.

    Written first and required, because every score row carries
    `run_manifest_id`: a record whose provenance row does not exist is not
    reproducible, and the recorder refuses to invent one.

    **The two artifact versions are written first in turn**, because
    `run_manifests.strategy_config_digest` and `data_snapshot_digest` are
    foreign keys into `artifact_versions.digest`. SQLite does not enforce
    foreign keys unless asked, so skipping them would succeed here and fail on
    PostgreSQL -- a provenance row pointing at a digest nobody stored is
    provenance in name only.
    """
    for artifact in (manifest.strategy_config, manifest.data_snapshot):
        existing = session.get(ArtifactVersionRow, artifact.digest)
        if existing is None:
            session.add(
                ArtifactVersionRow(
                    digest=artifact.digest,
                    kind=str(artifact.kind),
                    name=artifact.name,
                    payload=dict(artifact.payload),
                    created_at=artifact.created_at,
                )
            )
    session.flush()
    row = RunManifestRow(
        run_id=manifest.run_id,
        run_kind="backtest",
        as_of=manifest.as_of,
        manifest_digest=manifest.digest,
        strategy_config_digest=manifest.strategy_config.digest,
        data_snapshot_digest=manifest.data_snapshot.digest,
        code_version=manifest.code_version,
        created_at=manifest.created_at,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="sqlite:///research01.sqlite", help="READ ONLY")
    ap.add_argument(
        "--record",
        default="/Users/ericsasson/Documents/TradeItData/out/decision_record.sqlite",
        help="where the decision record is written; never the corpus",
    )
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", default="2015-01-02")
    ap.add_argument("--end", default="2016-12-30")
    ap.add_argument("--capital", type=Decimal, default=Decimal(100000))
    args = ap.parse_args()

    if Path(args.record).resolve() == Path("research01.sqlite").resolve():
        raise SystemExit("the record must not be written into the corpus")

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())

    corpus: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.corpus, future=True)),
        future=True,
    )()
    record_engine = create_engine(f"sqlite:///{args.record}", future=True)
    Base.metadata.create_all(record_engine)
    record: Session = sessionmaker(bind=record_engine, future=True)()

    resolved = _universe(corpus, tickers)
    config = StrategyConfig(name="ma_cross_baseline")
    universe_ids = set(resolved.values())
    rows = corpus.execute(SECTORS_AS_OF, {"as_of": start.isoformat()}).all()
    sectors = {int(i): str(s) for i, s in rows if int(i) in universe_ids}
    print(f"corpus : {args.corpus} (read)")
    print(f"record : {args.record} (written)")
    print(f"universe: {len(resolved)} of {len(tickers)} resolved, {len(sectors)} classified")

    rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
    data = CorpusSessionData(
        session=corpus,
        universe=tuple(sorted(resolved.values())),
        start=start,
        end=end,
        candidate_source=rule,
    )
    rule.splits_on = data.splits_on

    manifest = _manifest(config, dt.datetime.now(dt.UTC), data.bar_count)
    manifest_id = _persist_manifest(record, manifest)
    print(f"manifest row {manifest_id} written first, because every score cites one")

    engine = EventDrivenEngine(
        cycle=PortfolioCycle(
            sizer=RiskBasedSizer(
                sizing=config.sizing, risk=config.risk, max_participation=Decimal("0.02")
            ),
            engine=MostRestrictiveEngine(
                rule_set=(
                    PortfolioHeatRule(config=config.risk),
                    MaxPositionsRule(config=config.risk),
                    PositionSizeRule(
                        max_position_pct_of_equity=config.sizing.max_position_pct_of_equity
                    ),
                    GrossExposureRule(config=config.risk),
                    SectorExposureRule(config=config.risk, sectors=sectors),
                ),
                sizing=config.sizing,
            ),
            ranker=DiversityAwareRanker(
                correlation_weight=0.0, sector_penalty_per_holding=0.0, held_sectors={}
            ),
            ladder=StopLadder(config=config.exits),
        ),
        costs=ParticipationCostModel(config=config.costs),
        fills=BarFillModel(max_participation=Decimal("0.02")),
        data=data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=0.02,
        ),
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
        manifest=manifest,
        recorder=DatabaseRecorder(session=record, run_manifest_id=manifest_id),
    )
    spec = BacktestSpec(
        name="ma_cross_baseline",
        start=start,
        end=end,
        universe=",".join(sorted(resolved)),
        initial_capital=args.capital,
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    t0 = time.time()
    result = engine.run(spec)
    record.commit()
    print(f"\n{len(result.trades):,} trades, record committed [{time.time() - t0:.1f}s]")

    counts = {
        name: record.execute(text(f"select count(*) from {name}")).scalar()
        for name in ("opportunity_scores", "orders", "executions", "journal_entries")
    }
    for name, count in counts.items():
        print(f"  {name:<22} {count:>7,}")
    if not any(counts.values()):
        raise SystemExit(
            "the run completed and wrote nothing. The recorder was not reached, and "
            "reporting an empty record as success is the failure this project keeps "
            "finding."
        )

    sessions = list(data.sessions(start, end))
    walked = 0
    for session_date in sessions:
        chain = reconstruct(record, portfolio_id=1, session_date=session_date)
        if chain.scores or chain.orders or chain.executions:
            walked += 1
    print(f"\n  {walked:,} of {len(sessions):,} sessions can be walked end to end")

    busiest = max(
        sessions,
        key=lambda d: (
            len(reconstruct(record, 1, d).orders) + len(reconstruct(record, 1, d).executions)
        ),
    )
    print(f"\n--- the busiest session, reconstructed: {busiest} ---")
    print(reconstruct(record, portfolio_id=1, session_date=busiest).describe())
    print(
        "\n  A decision session showing orders and NO executions is correct, not a gap:\n"
        "  the engine settles yesterday's decisions against today's prices, so an\n"
        "  order placed today is filled on the next session and its execution belongs\n"
        "  to that one."
    )
    print(
        "\nP&L is deliberately absent: it is a property of a closed trade rather than\n"
        "of a session, and `record_trade` is its writer. Stated rather than papered\n"
        "over -- a chain that silently lacked its last link would be the empty-success\n"
        "failure in a new costume."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
