"""The walk-forward grid, and the mean that must never appear alone.

`edge_retained` is a ratio whose denominator is an in-sample CAGR. A window
that barely made money in sample produces an enormous ratio from an ordinary
out-of-sample year, and an arithmetic mean is dominated by exactly those
windows — so every test here protects the distribution that stops the mean
being read as a verdict.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradeit.api.evidence_route import EDGE_RETAINED_BAR, walkforward_payload
from tradeit.readiness.criteria import PROMOTION_CRITERIA
from tradeit.storage.tables import ArtifactVersion, BacktestRun, Base, RunManifest

STAMP = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def record(tmp_path: object) -> sessionmaker[Session]:
    dsn = f"sqlite:///{tmp_path / 'runs.sqlite'}"  # type: ignore[operator]
    engine = create_engine(dsn, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        for digest, kind in (("sc", "strategy_config"), ("ds", "data_snapshot")):
            session.add(
                ArtifactVersion(digest=digest, kind=kind, name="t", payload={}, created_at=STAMP)
            )
        session.flush()
        session.add(
            RunManifest(
                run_id="r",
                run_kind="backtest",
                as_of=STAMP,
                manifest_digest="m",
                strategy_config_digest="sc",
                data_snapshot_digest="ds",
                code_version="t",
                created_at=STAMP,
            )
        )
        session.commit()
    return factory


def _run(factory: sessionmaker[Session], name: str, cagr: float | None, oos: bool) -> None:
    with factory() as session:
        session.add(
            BacktestRun(
                name=name,
                run_manifest_id=1,
                status="completed",
                start_date=dt.date(2010, 1, 1),
                end_date=dt.date(2011, 1, 1),
                universe="X",
                initial_capital=100_000,
                cost_model="participation",
                fill_model="bar",
                is_out_of_sample=oos,
                cagr=cagr,
            )
        )
        session.commit()


def _read(factory: sessionmaker[Session]) -> dict[str, object]:
    with factory() as session:
        return walkforward_payload(session)


def test_windows_are_paired_by_name_because_nothing_else_joins_them(
    record: sessionmaker[Session],
) -> None:
    """There is no column joining a window to its own out-of-sample test.

    The pairing lives in the run name and nowhere else, which is why a flat
    list of 53 runs showed none of the structure that matters.
    """
    _run(record, "s/wf-anchored-0-is", 0.10, False)
    _run(record, "s/wf-anchored-0-oos", 0.06, True)
    body = _read(record)
    assert body["window_count"] == 1
    window = body["windows"][0]  # type: ignore[index]
    assert window["window"] == "wf-anchored-0"
    assert window["edge_retained"] == pytest.approx(0.6)


def test_a_window_with_no_in_sample_edge_is_unresolved_not_zero(
    record: sessionmaker[Session],
) -> None:
    """Fail-closed rather than tidy.

    A strategy with no in-sample edge has no edge to retain, so the ratio
    would be an artefact of dividing by something near zero. Scoring it zero
    would read as "the edge vanished" when the truth is there was none.
    """
    _run(record, "s/wf-0-is", -0.02, False)
    _run(record, "s/wf-0-oos", 0.10, True)
    window = _read(record)["windows"][0]  # type: ignore[index]
    assert window["edge_retained"] is None
    assert "no in-sample edge" in window["unresolved"]


def test_unresolved_windows_are_skipped_from_the_mean_not_counted_as_zero(
    record: sessionmaker[Session],
) -> None:
    _run(record, "s/wf-0-is", 0.10, False)
    _run(record, "s/wf-0-oos", 0.08, True)
    _run(record, "s/wf-1-is", 0.0, False)  # no edge to retain
    _run(record, "s/wf-1-oos", 0.20, True)
    body = _read(record)
    assert body["resolved_count"] == 1
    assert body["unresolved_count"] == 1
    # 0.8, not 0.4 — the unresolved window contributes nothing.
    assert body["mean_edge_retained"] == pytest.approx(0.8)


def test_the_distribution_travels_with_the_mean(record: sessionmaker[Session]) -> None:
    """**The point of the whole module.**

    One window with a near-zero in-sample CAGR dominates an arithmetic mean of
    ratios. Measured on the real robustness record: `wf-rolling-7` made 0.51%
    in sample and 21.78% out, a ratio of 42.4, and it alone pulls the mean of
    21 windows from 0.70 to 4.21 — while eight of those windows lost money out
    of sample. "Mean 4.21, clears the 0.5 bar" is true and dangerously
    flattering, so the mean may never be served without its distribution.
    """
    _run(record, "s/wf-0-is", 0.0001, False)  # barely any edge
    _run(record, "s/wf-0-oos", 0.20, True)  # ordinary year -> enormous ratio
    _run(record, "s/wf-1-is", 0.10, False)
    _run(record, "s/wf-1-oos", -0.05, True)  # lost money out of sample
    body = _read(record)

    assert body["mean_edge_retained"] > 100, "the fixture should produce a wild mean"
    assert body["median_edge_retained"] is not None
    assert body["windows_negative"] == 1
    assert body["windows_clearing_bar"] == 1
    assert body["smallest_in_sample_cagr"] == pytest.approx(0.0001)
    note = body["distribution_note"]
    assert "median" in note and "dominated" in note


def test_the_bar_is_read_from_the_readiness_criteria_not_restated(
    record: sessionmaker[Session],
) -> None:
    """A number copied into a second place will disagree with itself.

    ADR-0034 approved this threshold; the API must quote it, not keep its own.
    """
    declared = next(c.threshold.value for c in PROMOTION_CRITERIA if c.metric == "edge_retained")
    assert declared == EDGE_RETAINED_BAR
    assert _read(record)["bar"] == declared


def test_a_half_pair_is_reported_rather_than_dropped(
    record: sessionmaker[Session],
) -> None:
    """A missing out-of-sample leg is the one most worth noticing.

    Silently dropping it would shrink the denominator of every summary above
    and make an incomplete grid look complete.
    """
    _run(record, "s/wf-0-is", 0.10, False)
    body = _read(record)
    assert body["window_count"] == 0
    assert any("only the in-sample leg" in name for name in body["unpaired"])  # type: ignore[union-attr]


def test_a_record_with_no_walk_forward_runs_returns_no_windows(
    record: sessionmaker[Session],
) -> None:
    """Not an error. A single-run record legitimately has no grid."""
    _run(record, "ma_cross_baseline", 0.02, False)
    body = _read(record)
    assert body["window_count"] == 0
    assert body["mean_edge_retained"] is None
    assert body["unpaired"] == ["ma_cross_baseline"]


def test_each_window_is_judged_against_the_bar_individually(
    record: sessionmaker[Session],
) -> None:
    """`clears_bar` is what the screen marks each row with.

    Without it the reader gets a column of ratios and has to hold 0.5 in their
    head down twenty-six rows, which is how a window that failed gets read as
    one that passed.
    """
    _run(record, "s/wf-0-is", 0.10, False)
    _run(record, "s/wf-0-oos", 0.08, True)  # 0.80 — clears
    _run(record, "s/wf-1-is", 0.10, False)
    _run(record, "s/wf-1-oos", 0.03, True)  # 0.30 — does not
    _run(record, "s/wf-2-is", 0.10, False)
    _run(record, "s/wf-2-oos", -0.05, True)  # negative — does not
    by_window = {w["window"]: w for w in _read(record)["windows"]}  # type: ignore[union-attr]
    assert by_window["wf-0"]["clears_bar"] is True
    assert by_window["wf-1"]["clears_bar"] is False
    assert by_window["wf-2"]["clears_bar"] is False
