#!/usr/bin/env python
"""The three scores, on a real portfolio deciding real sessions.

Phase 7's claim is the whole project's organising distinction: *a great stock
and a great trade for this portfolio right now are different questions, and only
the second may authorise a trade.* ``src/tradeit/opportunity/`` implements it in
583 lines across five modules with 35 passing tests, and the audit of
2026-10-17 found that **no script had ever imported the package**. It had never
scored anything from the corpus.

That is the same gap Phases 8 and 9 carried, and it matters more here, because
the separation is not an implementation detail that either works or does not.
**A fit score that never changes a ranking has not been exercised**, however
many unit tests it passes: the code would run, the numbers would look
plausible, and the claim the phase exists to support would be untested. So the
measurement this script is built around is not "does it run" but:

    How often does portfolio fit actually change what gets the capital?

What is measured
----------------

A real moving-average backtest over 2010-2019. At every session, for every
candidate the strategy nominates:

* the **standalone** score, which here is deliberately crude -- a momentum
  proxy, not a validated signal. **It must be: no validated signal exists**
  after 144 ledger trials, and inventing one to make this exercise look better
  would be the fabrication this project refuses. What is under test is the
  separation machinery, which does not care where score 1 came from.
* the **fit** score, from a ``PortfolioContext`` built out of the live
  portfolio -- equity, cash, position count, sector exposure from the SIC map,
  heat, and the weight already held in this exact security.
* the **final** score, and a ``Slate`` for the Swing mandate.

Then: how often the fit vetoed a candidate the standalone score ranked first,
and how often the two orderings disagree at all. Those two numbers are the
phase's evidence.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys
import time
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from backtest_correlation_limit import build_correlations
from backtest_research01 import MovingAverageCross, _manifest, _universe
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradeit.backtesting.base import BacktestSpec
from tradeit.backtesting.corpus import CorpusSessionData
from tradeit.backtesting.engine import EventDrivenEngine
from tradeit.backtesting.performance import StandardPerformanceAnalyzer
from tradeit.core.enums import Bartimeframe, SignalDirection
from tradeit.execution.simulation import BarFillModel, ParticipationCostModel
from tradeit.opportunity import (
    Contender,
    PortfolioContext,
    VetoReason,
    build_slate,
    combine,
    score_fit,
)
from tradeit.portfolio.allocation import DiversityAwareRanker
from tradeit.portfolio.cycle import EntryCandidate, PortfolioCycle
from tradeit.portfolio.mandate import Mandate, UnitOfStudy
from tradeit.portfolio.sizing import RiskBasedSizer
from tradeit.portfolio.stops import StopLadder
from tradeit.risk.contextual import UNCLASSIFIED, SectorExposureRule
from tradeit.risk.engine import MostRestrictiveEngine
from tradeit.risk.rules import (
    GrossExposureRule,
    MaxPositionsRule,
    PortfolioHeatRule,
    PositionSizeRule,
)
from tradeit.risk.snapshot import sector_exposures
from tradeit.storage.session import install_sqlite_busy_timeout
from tradeit.strategy.base import OpportunityScore
from tradeit.strategy.config import StrategyConfig

SECTORS_AS_OF = text("""
    SELECT instrument_id, sector FROM sectors
    WHERE scheme = 'SIC' AND valid_from <= :as_of
      AND (valid_to IS NULL OR valid_to > :as_of)
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite:///research01.sqlite")
    ap.add_argument(
        "--tickers", default="AAPL,MSFT,IBM,KO,JNJ,XOM,GE,WMT,PG,INTC,CSCO,PFE,MRK,DIS,T"
    )
    ap.add_argument("--start", default="2010-01-04")
    ap.add_argument("--end", default="2019-12-31")
    ap.add_argument("--standalone-weight", type=float, default=None)
    ap.add_argument(
        "--pyramid",
        action="store_true",
        help=(
            "also nominate names already held, as Phase 8 pyramiding would. "
            "The cross-only source never re-nominates a holding."
        ),
    )
    ap.add_argument("--overlap-lookback", type=int, default=126)
    # ALREADY_AT_FULL_SIZE reached 65.6% of its threshold at the configured
    # 0.5% risk per trade -- within reach, so leaving it inferred rather than
    # fired would be settling for an argument where a measurement is
    # available. This overrides sizing for the EXERCISE and is NOT a proposal
    # to change risk config, exactly as --max-sector and --max-correlation are
    # not in the two Phase 8 scripts.
    ap.add_argument("--risk-per-trade", type=float, default=None)
    args = ap.parse_args()

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    tickers = tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    session: Session = sessionmaker(
        bind=install_sqlite_busy_timeout(create_engine(args.db, future=True)), future=True
    )()
    resolved = _universe(session, tickers)
    config = StrategyConfig(name="ma_cross_baseline")
    if args.risk_per_trade is not None:
        config = config.model_copy(
            update={
                "sizing": config.sizing.model_copy(
                    update={"risk_per_trade_pct": args.risk_per_trade}
                )
            }
        )
        print(
            f"sizing OVERRIDDEN for this exercise: risk per trade "
            f"{args.risk_per_trade:.2%}. Not a proposal."
        )
    rows = session.execute(SECTORS_AS_OF, {"as_of": start.isoformat()}).all()
    universe_ids = set(resolved.values())
    sectors = {int(i): str(s) for i, s in rows if int(i) in universe_ids}
    print(f"universe: {len(resolved)} of {len(tickers)} resolved, {len(sectors)} classified")

    def make_data() -> tuple[CorpusSessionData, MovingAverageCross]:
        rule = MovingAverageCross(fast=50, slow=200, stop_pct=Decimal("0.08"))
        data = CorpusSessionData(
            session=session,
            universe=tuple(sorted(resolved.values())),
            start=start,
            end=end,
            candidate_source=rule,
        )
        rule.splits_on = data.splits_on
        return data, rule

    # `overlap` defaulted to 0.0 in the first version of this script, which made
    # `distinctness` a constant 1.0 and one of the five fit components inert for
    # all 587 candidates. It is a declared input, so a default is a silent
    # answer: fed here from the same pairwise correlation the Phase 8 exercise
    # built, as the nearest honest proxy for "another way to own what we own".
    probe_data, _ = make_data()
    probe_sessions = list(probe_data.sessions(start, end))
    correlations = build_correlations(probe_data, probe_sessions, args.overlap_lookback)
    print(f"overlap proxy: {len(correlations):,} measured pairs")

    def overlap_against(instrument_id: int, held_ids: list[int]) -> float:
        """Strongest correlation with anything already held, 0 when unknown.

        0.0 for an unmeasured pair is a *known* understatement rather than a
        guess, and it is recorded as such below: unlike the correlation RULE,
        which must fail closed because it gates a trade, this feeds a ranking
        weight where refusing to rank is not an option the caller has.
        """
        best = 0.0
        for other in held_ids:
            if other == instrument_id:
                continue
            key = (min(instrument_id, other), max(instrument_id, other))
            value = correlations.get(key)
            if value is not None:
                best = max(best, value)
        return max(0.0, best)

    tally: collections.Counter[str] = collections.Counter()
    vetoes: collections.Counter[str] = collections.Counter()
    sector_veto_pooled = 0
    closest_weight = 0.0
    closest_cash = 0.0
    fit_scores: list[float] = []
    reorderings: list[tuple[dt.date, str, str]] = []
    slate_sizes: list[int] = []
    slates = 0

    class ScoringEngine(EventDrivenEngine):
        """Scores the candidates the strategy nominates, at the moment it does.

        The scoring is read-only: it does not feed the engine's own decisions.
        Wiring score 3 into position selection would change what the backtest
        measures, and that is a product decision with a return attached, not
        an exercise. **This answers whether the machinery discriminates, not
        whether using it pays.**
        """

        def _decide(self, state, bars, session_date):  # type: ignore[no-untyped-def]
            nonlocal slates
            candidates = list(self.data.candidates(session_date))
            portfolio = state.portfolio()
            if args.pyramid:
                # What a pyramiding source does: offer to add to what is
                # already held. Scored only -- never handed to the engine, so
                # the backtest it runs is unchanged.
                nominated = {c.instrument_id for c in candidates}
                for position in portfolio.open_positions:
                    iid = position.instrument_id
                    bar = bars.get(iid)
                    if bar is None or iid in nominated:
                        continue
                    candidates.append(
                        EntryCandidate(
                            score=OpportunityScore(
                                instrument_id=iid,
                                session_date=session_date,
                                direction=SignalDirection.LONG,
                                total=0.0,
                                components=(),
                                feature_set_digest="pyramid-probe",
                                strategy_config_digest=config.digest,
                            ),
                            entry_price=bar.close,
                            stop_price=bar.close * Decimal("0.92"),
                        )
                    )
                    tally["pyramid_nominations"] += 1
            if candidates:
                exposures = sector_exposures(portfolio, sectors)
                held = {p.instrument_id: p for p in portfolio.open_positions}
                equity = portfolio.equity
                contenders: list[Contender] = []
                standalone_by_ref: dict[str, float] = {}
                for candidate in candidates:
                    iid = candidate.instrument_id
                    sector = sectors.get(iid, UNCLASSIFIED)
                    position = held.get(iid)
                    weight = 0.0
                    if position is not None and equity > 0:
                        price = portfolio.last_prices.get(iid, position.average_entry_price)
                        weight = float(position.market_value(price) / equity)
                    bar = bars.get(iid)
                    # A crude momentum proxy, stated as crude. No validated
                    # signal exists to put here and inventing one would be the
                    # fabrication this project refuses.
                    standalone = 0.5
                    if bar is not None and bar.low < bar.high:
                        standalone = float((bar.close - bar.low) / (bar.high - bar.low))
                    # `required_cash` defaulted to Decimal(0) in the first
                    # version, which made INSUFFICIENT_CASH unreachable -- the
                    # veto fires on `required_cash > cash` and zero never
                    # exceeds anything. That was a gap in this script, not in
                    # the module, and it meant one of five refusal paths was
                    # reported as "never fired on real data" when it had never
                    # been given the chance. Taken from the real sizer here.
                    decision = self.cycle.sizer.size(
                        candidate.score,
                        candidate.entry_price,
                        candidate.stop_price,
                        portfolio,
                        candidate.average_dollar_volume,
                    )
                    context = PortfolioContext(
                        equity=equity,
                        cash=portfolio.cash,
                        open_positions=portfolio.position_count,
                        position_limit=config.risk.max_positions,
                        sector_exposure=exposures.get(sector, 0.0),
                        sector_limit=config.risk.max_sector_exposure_pct,
                        heat=float(portfolio.heat()),
                        heat_limit=config.risk.max_portfolio_heat_pct,
                        existing_weight=weight,
                        max_weight=config.sizing.max_position_pct_of_equity,
                        overlap=overlap_against(iid, list(held)),
                        required_cash=decision.notional,
                    )
                    # "Never fired" is not a measurement. How CLOSE a veto came
                    # to firing is, and it distinguishes a path that is merely
                    # unexercised from one this configuration cannot reach.
                    nonlocal closest_weight, closest_cash
                    if context.max_weight > 0:
                        closest_weight = max(
                            closest_weight, context.existing_weight / context.max_weight
                        )
                    if context.cash > 0:
                        closest_cash = max(
                            closest_cash, float(context.required_cash / context.cash)
                        )
                    fit = score_fit(context)
                    if not fit.reconciles:
                        raise SystemExit(
                            f"fit components do not sum to the score on {session_date} "
                            f"for instrument {iid}. Refusing to report the run."
                        )
                    final = (
                        combine(standalone, fit)
                        if args.standalone_weight is None
                        else combine(standalone, fit, standalone_weight=args.standalone_weight)
                    )
                    tally["scored"] += 1
                    if not fit.admissible:
                        nonlocal_sector = VetoReason.SECTOR_LIMIT_REACHED in fit.vetoes
                        if nonlocal_sector and sector == UNCLASSIFIED:
                            nonlocal sector_veto_pooled
                            sector_veto_pooled += 1
                        tally["vetoed"] += 1
                        for reason in fit.vetoes:
                            vetoes[str(reason)] += 1
                    fit_scores.append(fit.score)
                    reference = str(iid)
                    standalone_by_ref[reference] = standalone
                    contenders.append(
                        Contender(
                            reference=reference,
                            mandate=Mandate.SWING,
                            timeframe=Bartimeframe.D1,
                            score=final,
                        )
                    )
                if contenders:
                    slate = build_slate(
                        contenders, UnitOfStudy.single(Mandate.SWING, Bartimeframe.D1)
                    )
                    slates += 1
                    slate_sizes.append(len(contenders))
                    tally["for_capital"] += len(slate.for_capital)
                    tally["refused"] += len(slate.refused)
                    # The measurement the phase lives or dies by: does fit ever
                    # change who gets the capital?
                    by_standalone = sorted(standalone_by_ref, key=lambda r: -standalone_by_ref[r])
                    by_final = [c.reference for c in slate.ranked]
                    if by_standalone and by_final and by_standalone[0] != by_final[0]:
                        tally["top_changed"] += 1
                        reorderings.append((session_date, by_standalone[0], by_final[0]))
                    if by_standalone != by_final:
                        tally["order_changed"] += 1
            super()._decide(state, bars, session_date)

    run_data, _ = make_data()
    spec = BacktestSpec(
        name="ma_cross_baseline",
        start=start,
        end=end,
        universe=",".join(sorted(resolved)),
        initial_capital=Decimal(100000),
        strategy_config_digest=config.digest,
        cost_model="participation",
        fill_model="bar",
    )
    engine = ScoringEngine(
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
        data=run_data,
        analyzer=StandardPerformanceAnalyzer(
            annualisation_factor=config.indicators.annualisation_factor,
            risk_free_rate=0.02,
        ),
        delisting_after_sessions=10,
        delisting_recovery=Decimal(1),
        manifest=_manifest(config, dt.datetime.now(dt.UTC), run_data.bar_count),
    )
    t0 = time.time()
    result = engine.run(spec)
    print(f"ran {len(result.trades):,} trades [{time.time() - t0:.1f}s]")

    if not tally["scored"]:
        raise SystemExit(
            "zero candidates scored by a run that completed. The override did not fire, "
            "and reporting this as an exercise would be the empty-success failure again."
        )

    print(f"\n{tally['scored']:,} candidates scored over {slates:,} slates")
    print(f"  admitted to capital: {tally['for_capital']:,}")
    print(f"  refused by the fit:  {tally['refused']:,}  ({tally['vetoed']:,} vetoed)")
    if vetoes:
        print("\n  vetoes, by reason:")
        for reason, count in vetoes.most_common():
            print(f"    {reason:<28} {count:>6,}")
    else:
        print("\n  NO candidate was ever vetoed.")
    sector_vetoes = vetoes[str(VetoReason.SECTOR_LIMIT_REACHED)]
    if sector_vetoes:
        # The sector cap also limits the pooled UNCLASSIFIED bucket, so a veto
        # there is a coverage artefact and not a concentration finding. Reported
        # separately or it would be read as the latter.
        print(
            f"\n  of {sector_vetoes:,} sector vetoes, {sector_veto_pooled:,} fell on a\n"
            "    candidate in the pooled UNCLASSIFIED bucket -- those are a SIC coverage\n"
            f"    artefact, not sector concentration. {sector_vetoes - sector_veto_pooled:,} "
            "fell on a named sector."
        )
    if sum(vetoes.values()) > tally["vetoed"]:
        print(
            f"  ({sum(vetoes.values()):,} veto reasons across {tally['vetoed']:,} "
            "candidates: some tripped more than one)"
        )
    unseen = [r for r in VetoReason if str(r) not in vetoes]
    if unseen:
        print("\n  veto reasons that never fired, and how close they came:")
        for reason in unseen:
            if reason is VetoReason.ALREADY_AT_FULL_SIZE:
                print(f"    {reason!s:<24} closest was {closest_weight:.1%} of max_weight")
            elif reason is VetoReason.INSUFFICIENT_CASH:
                print(f"    {reason!s:<24} closest was {closest_cash:.1%} of available cash")
            else:
                print(f"    {reason!s:<24} (unit tests only)")
        # The verdict follows the margin rather than preceding it. A first
        # draft called both paths "unreachable", which is wrong for a path that
        # got to two thirds of its threshold.
        for label, margin in (
            ("already_at_full_size", closest_weight),
            ("insufficient_cash", closest_cash),
        ):
            if str(getattr(VetoReason, label.upper())) in vetoes:
                continue
            if label == "insufficient_cash" and margin >= 0.999:
                # Not a sampling fact. RiskBasedSizer carries
                # BindingConstraint.CASH and caps quantity at cash/entry_price,
                # so a notional taken FROM it can equal available cash and can
                # never exceed it. The veto tests `required_cash > cash`, which
                # is therefore unreachable on this path by construction.
                print(
                    f"    {label}: reached {margin:.1%} of available cash and stopped there.\n"
                    "      That is not an unsampled path -- it is UNREACHABLE BY\n"
                    "      CONSTRUCTION: RiskBasedSizer caps quantity at cash/entry_price\n"
                    "      (BindingConstraint.CASH), so a notional taken from it equals cash\n"
                    "      at most and never exceeds it. No sizing change fires this veto\n"
                    "      while the required cash comes from that sizer.\n"
                    "      The veto is correct defensive code for a caller that computes\n"
                    "      required cash some other way -- a fixed-notional sizer, or an\n"
                    "      externally placed order -- and dead on the current path."
                )
            elif margin >= 0.5:
                print(
                    f"    {label}: reached {margin:.1%} of its threshold, so it is WITHIN\n"
                    "      REACH and simply not reached by this strategy and sizing -- not a\n"
                    "      structural impossibility. A modest sizing change would fire it."
                )
            else:
                print(
                    f"    {label}: peaked at {margin:.1%} of its threshold, far enough below\n"
                    "      that this configuration cannot reach it at all. Firing it needs a\n"
                    "      sizing or exposure regime nobody has proposed."
                )
        print(
            f"    Context: risk-based sizing at {config.sizing.risk_per_trade_pct:.2%} risk per "
            f"trade over a {config.exits.max_initial_stop_pct:.0%}\n"
            f"    stop, against a {config.sizing.max_position_pct_of_equity:.0%} "
            "position cap."
        )

    if fit_scores:
        fit_scores.sort()
        print(
            f"\n  fit score: min {fit_scores[0]:.3f}  median "
            f"{fit_scores[len(fit_scores) // 2]:.3f}  max {fit_scores[-1]:.3f}"
        )

    # A slate of one cannot test a ranking, and reporting "fit changed nothing"
    # without this number would blame the fit score for a universe that never
    # gave it two candidates to choose between.
    contested = sum(1 for n in slate_sizes if n >= 2)
    print(
        f"\n  slates with TWO OR MORE contenders: {contested:,} of {slates:,}"
        f"  (largest {max(slate_sizes, default=0)})"
    )
    print(
        f"  slates where fit CHANGED the ranking: {tally['order_changed']:,} "
        f"of the {contested:,} that could change"
    )
    print(f"  slates where fit changed who ranked FIRST: {tally['top_changed']:,}")
    if not tally["top_changed"] and not contested:
        print(
            "\n  Fit changed nothing, and COULD not have: no slate ever held two\n"
            "  contenders. This run says nothing about the fit score at all -- it is a\n"
            "  fact about a universe that nominates one candidate at a time. Run a\n"
            "  universe wide enough to produce competition before concluding anything."
        )
    elif not tally["top_changed"]:
        print(
            f"\n  Fit never changed the top of a slate, across {contested:,} slates that\n"
            "  held real competition. The machinery ran and discriminated nothing. The\n"
            "  code is not wrong, but the phase's central claim -- that a great stock\n"
            "  and a great trade differ -- is STILL unexercised on this evidence."
        )
    else:
        for when, was, now in reorderings[:5]:
            print(f"    {when}: standalone ranked {was} first, fit promoted {now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
