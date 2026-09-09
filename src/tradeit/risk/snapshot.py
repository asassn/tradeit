"""Building the daily risk record.

``risk_snapshots`` is written whether or not anything traded, and that is the
whole point of it: the value is in the quiet periods, when it answers *"was
that drawdown a risk-control failure or an ordinary run of losses?"* months
later, once nobody remembers.

A snapshot measures the portfolio as it stands. It needs no proposal, because
a limit can be breached without anybody trading — prices move, and a book
inside its heat limit last night can be outside it this morning without a
single order. ``limit_breaches`` therefore records what is *currently* true,
not what was refused today.

Two values are reported as unknown rather than approximated:

* **the largest sector**, when no classification covers the holdings — the
  pooled ``UNCLASSIFIED`` bucket is reported under its own name so a reader can
  see the coverage gap rather than mistake it for a sector
* **the worst pairwise correlation**, when any held pair is unmeasured. The
  maximum over the pairs that happen to be known reads as the maximum over the
  book, and it is systematically too low. ``None`` is the honest answer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

from tradeit.portfolio.base import PortfolioState
from tradeit.risk.base import RiskSnapshot
from tradeit.risk.contextual import UNCLASSIFIED, CorrelationSource
from tradeit.strategy.config import RiskConfig

__all__ = ["build_snapshot", "sector_exposures"]


def sector_exposures(portfolio: PortfolioState, sectors: Mapping[int, str]) -> dict[str, float]:
    """Each sector's share of equity, with unclassified holdings pooled."""
    if portfolio.equity <= 0:
        return {}
    exposures: dict[str, Decimal] = {}
    for position in portfolio.open_positions:
        sector = sectors.get(position.instrument_id, UNCLASSIFIED)
        price = portfolio.last_prices.get(position.instrument_id, position.average_entry_price)
        exposures[sector] = exposures.get(sector, Decimal(0)) + position.market_value(price)
    return {sector: float(value / portfolio.equity) for sector, value in sorted(exposures.items())}


def _worst_correlation(portfolio: PortfolioState, source: CorrelationSource | None) -> float | None:
    if source is None:
        return None
    ids = [position.instrument_id for position in portfolio.open_positions]
    if len(ids) < 2:
        return None
    worst: float | None = None
    for index, first in enumerate(ids):
        for second in ids[index + 1 :]:
            value = source.correlation(first, second)
            if value is None:
                # One unmeasured pair makes the maximum over the rest
                # an understatement dressed as a measurement.
                return None
            worst = value if worst is None else max(worst, value)
    return worst


def build_snapshot(
    portfolio: PortfolioState,
    config: RiskConfig,
    *,
    session_date: dt.date,
    peak_equity: Decimal | None = None,
    sectors: Mapping[int, str] | None = None,
    correlation: CorrelationSource | None = None,
    strategy_config_digest: str | None = None,
) -> RiskSnapshot:
    """Measure the portfolio and record which limits it is currently outside."""
    equity = portfolio.equity
    exposures = sector_exposures(portfolio, sectors or {})
    gross = portfolio.invested_fraction()
    heat = portfolio.heat()

    largest = Decimal(0)
    for position in portfolio.open_positions:
        price = portfolio.last_prices.get(position.instrument_id, position.average_entry_price)
        if equity > 0:
            largest = max(largest, position.market_value(price) / equity)

    drawdown = Decimal(0)
    if peak_equity is not None and peak_equity > 0:
        drawdown = max((peak_equity - equity) / peak_equity, Decimal(0))

    worst_correlation = _worst_correlation(portfolio, correlation)

    breaches: list[str] = []
    if heat > Decimal(str(config.max_portfolio_heat_pct)):
        breaches.append("portfolio_heat")
    if portfolio.position_count > config.max_positions:
        breaches.append("max_positions")
    if gross > Decimal(str(config.max_gross_exposure_pct)):
        breaches.append("gross_exposure")
    if any(share > config.max_sector_exposure_pct for share in exposures.values()):
        breaches.append("sector_exposure")
    if (
        worst_correlation is not None
        and worst_correlation > config.max_correlation_for_new_position
    ):
        breaches.append("correlation_cluster")
    if peak_equity is not None and drawdown >= Decimal(str(config.drawdown_halt_pct)):
        breaches.append("max_drawdown")

    return RiskSnapshot(
        portfolio_id=portfolio.portfolio_id,
        as_of=portfolio.as_of,
        session_date=session_date,
        equity=equity,
        cash=portfolio.cash,
        open_risk=portfolio.total_open_risk(),
        heat=heat,
        position_count=portfolio.position_count,
        largest_position_pct=largest,
        gross_exposure_pct=gross,
        sector_exposures=exposures,
        max_pairwise_correlation=worst_correlation,
        drawdown_from_peak=drawdown,
        limit_breaches=tuple(breaches),
        strategy_config_digest=strategy_config_digest,
    )
