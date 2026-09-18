"""The cross-sectional IC and the two defects it exists to correct.

Each test is written against a failure that actually happened, per §32 of
``SIGNAL_SCOREBOARD.md``: a pooled correlation rewarding market timing, and a
standard error that counted rows -- or densely packed dates -- as independent.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from tradeit.signals.cross_section import (
    MIN_PER_DATE,
    _newey_west_inflation,
    cross_sectional_ic,
    spearman,
)

START = dt.date(2010, 1, 4)


def _panel(
    dates: list[dt.date], names: int, rng: np.random.Generator, *, timing: bool
) -> tuple[np.ndarray, np.ndarray, list[dt.date]]:
    """A panel whose signal either times the market or ranks the securities.

    ``timing=True``: every security on a date shares one signal value (plus a
    whisper of noise) and that value predicts the date's COMMON return. There is
    nothing to choose between securities -- only a question of when to hold.

    ``timing=False``: the signal ranks securities within each date and carries
    no information about the market's direction.
    """
    signals, outcomes, stamps = [], [], []
    for date in dates:
        common = rng.normal()
        if timing:
            signal = common + rng.normal(0.0, 1e-3, names)
            outcome = 0.5 * common + rng.normal(0.0, 1.0, names)
        else:
            signal = rng.normal(0.0, 1.0, names)
            outcome = 0.3 * signal + rng.normal(0.0, 1.0, names) + rng.normal()
        signals.append(signal)
        outcomes.append(outcome)
        stamps.extend([date] * names)
    return np.concatenate(signals), np.concatenate(outcomes), stamps


def _every(days: int, count: int) -> list[dt.date]:
    return [START + dt.timedelta(days=days * i) for i in range(count)]


class TestTheQuestionItAnswers:
    def test_market_timing_is_rewarded_when_pooled_and_not_across_sections(self) -> None:
        """The defect §32's positive control caught, reproduced on purpose.

        A signal that only says WHEN the market will rise reads a large pooled
        correlation, because across dates it lines up with the common move. It
        must read nothing within a date, because it cannot tell one security
        from another -- and a portfolio choosing among today's securities
        cannot use it.
        """
        rng = np.random.default_rng(1)
        signal, outcome, dates = _panel(_every(91, 60), 50, rng, timing=True)
        pooled = spearman(signal, outcome)
        assert pooled is not None and pooled > 0.2
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None
        assert abs(result.ic) < 0.05
        assert result.t is None or abs(result.t) < 2.0

    def test_a_genuine_cross_sectional_signal_is_found(self) -> None:
        rng = np.random.default_rng(2)
        signal, outcome, dates = _panel(_every(91, 40), 60, rng, timing=False)
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None
        assert result.ic > 0.2
        assert result.t is not None and result.t > 5.0


class TestWhatItCountsAsIndependent:
    def test_sampling_densely_does_not_manufacture_independence(self) -> None:
        """The flaw in §32's first correction, stated as an invariant.

        The same decade sampled every calendar day rather than every quarter
        contains no more non-overlapping 63-session windows. The block count
        must be set by calendar time, not by how many dates the grid produced.
        """
        rng = np.random.default_rng(3)
        sparse, s_out, s_dates = _panel(_every(91, 40), 30, rng, timing=False)
        dense, d_out, d_dates = _panel(_every(1, 40 * 91), 30, rng, timing=False)
        a = cross_sectional_ic(sparse, s_out, s_dates, 63)
        b = cross_sectional_ic(dense, d_out, d_dates, 63)
        assert a is not None and b is not None
        assert b.dates > 50 * a.dates
        assert abs(b.blocks - a.blocks) <= 1

    def test_the_block_count_is_the_decade_not_the_rows(self) -> None:
        rng = np.random.default_rng(4)
        span = 3650
        signal, outcome, dates = _panel(_every(7, span // 7), 25, rng, timing=False)
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None
        # A decade holds about forty 63-session windows, whatever the row count.
        assert 35 <= result.blocks <= 45
        assert len(signal) > 10_000

    def test_thin_dates_are_not_scored(self) -> None:
        rng = np.random.default_rng(5)
        signal, outcome, dates = _panel(_every(91, 20), MIN_PER_DATE - 1, rng, timing=False)
        assert cross_sectional_ic(signal, outcome, dates, 63) is None

    def test_too_little_time_is_an_answer_not_an_error(self) -> None:
        rng = np.random.default_rng(6)
        signal, outcome, dates = _panel(_every(1, 30), 40, rng, timing=False)
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None
        assert result.blocks < 3
        assert result.t is None


class TestTheCorrectionOnlyWidens:
    def test_newey_west_never_shrinks_the_standard_error(self) -> None:
        """A negative lag-1 autocorrelation would otherwise narrow the interval,
        and this correction exists only to stop it being too narrow."""
        alternating = np.array([1.0, -1.0] * 20)
        assert _newey_west_inflation(alternating) == pytest.approx(1.0)
        trending = np.cumsum(np.ones(40))
        assert _newey_west_inflation(trending) > 1.0

    def test_resolution_is_reported_on_the_same_standard_error_as_t(self) -> None:
        rng = np.random.default_rng(7)
        signal, outcome, dates = _panel(_every(91, 40), 40, rng, timing=False)
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None and result.t is not None
        smallest = result.detectable(abs(result.t))
        assert smallest is not None
        # Exact, not approximate: at a hurdle equal to its own |t|, the smallest
        # detectable IC is precisely the block mean the t was built from. If
        # ``detectable`` used a different standard error from ``t`` -- dropping
        # the Newey-West term, say -- this is where it would show.
        by_block: dict[int, list[float]] = {}
        for date in sorted(set(dates)):
            rows = [i for i, d in enumerate(dates) if d == date]
            ic = spearman(signal[rows], outcome[rows])
            assert ic is not None
            by_block.setdefault((date - dates[0]).days // round(63 * 365.25 / 252), []).append(ic)
        block_mean = float(np.mean([np.mean(v) for v in by_block.values()]))
        assert smallest == pytest.approx(abs(block_mean), rel=1e-9)


class TestRanks:
    def test_ties_do_not_depend_on_input_order(self) -> None:
        x = np.array([1.0, 1.0, 2.0, 3.0])
        y = np.array([4.0, 3.0, 2.0, 1.0])
        forward = spearman(x, y)
        reverse = spearman(x[::-1].copy(), y[::-1].copy())
        assert forward == pytest.approx(reverse)

    def test_a_constant_side_has_no_correlation(self) -> None:
        assert spearman(np.ones(10), np.arange(10.0)) is None


class TestBreadth:
    def test_uneven_breadth_is_visible_in_the_result(self) -> None:
        """The §26 pitfall, made impossible to miss rather than fixed silently.

        Many thin dates carrying a strong effect and a few broad dates carrying
        a weak one: the unweighted mean follows the thin dates. Changing the
        estimator would be a methodology decision; reporting the breadth it rests
        on is not, and it is what lets a reader see the headline is the thin
        population's.
        """
        rng = np.random.default_rng(8)
        thin, t_out, t_dates = _panel(_every(9, 400), 20, rng, timing=False)
        broad_dates = _every(91, 40)
        broad = rng.normal(0.0, 1.0, 40 * 800)
        b_out = 0.02 * broad + rng.normal(0.0, 1.0, 40 * 800)
        b_stamps = [d for d in broad_dates for _ in range(800)]
        mixed = cross_sectional_ic(
            np.concatenate([thin, broad]),
            np.concatenate([t_out, b_out]),
            [*t_dates, *b_stamps],
            63,
        )
        assert mixed is not None
        assert mixed.median_breadth == pytest.approx(20.0)
        # Most observations are on the broad dates; the estimate follows the thin.
        assert len(broad) > len(thin)
        assert mixed.ic > 0.2


class TestTheEstimateAndItsStandardErrorDescribeOneNumber:
    def test_ic_and_t_agree_when_sampling_density_is_uneven(self) -> None:
        """The defect that manufactured a pass in §32's third reading.

        It only appears when calendar blocks hold DIFFERENT numbers of dates --
        with even spacing the mean over dates and the mean over blocks are the
        same number, which is why a first version of this test built on evenly
        spaced random panels passed against the broken code (0 disagreements in
        58). The real panels are uneven: each security samples on its own grid.

        So this builds the separation on purpose. Alternate calendar blocks:
        twenty dates reading a mild positive IC, then one date reading a strong
        negative one. Over DATES the mild positives outvote (+0.08); over BLOCKS
        the two kinds weigh equally (-0.10). The broken code reported the first
        and built its t on the second.
        """
        rng = np.random.default_rng(9)
        width = round(63 * 365.25 / 252)
        signals, outcomes, stamps = [], [], []
        for block in range(40):
            first = START + dt.timedelta(days=block * width)
            dense = block % 2 == 0
            for k in range(20 if dense else 1):
                date = first + dt.timedelta(days=k)
                x = rng.normal(0.0, 1.0, 400)
                y = (0.12 if dense else -0.8) * x + rng.normal(0.0, 1.0, 400)
                signals.append(x)
                outcomes.append(y)
                stamps.extend([date] * 400)
        signal, outcome = np.concatenate(signals), np.concatenate(outcomes)
        per_date_mean = float(
            np.mean(
                [
                    spearman(signal[i : i + 400], outcome[i : i + 400])
                    for i in range(0, len(signal), 400)
                ]
            )
        )
        result = cross_sectional_ic(signal, outcome, stamps, 63)
        assert result is not None and result.t is not None
        # The panel really does separate the two averages ...
        assert per_date_mean > 0.0
        assert result.ic < 0.0
        # ... and the estimate reported is the one its t describes.
        assert np.sign(result.ic) == np.sign(result.t)

    def test_t_is_exactly_the_estimate_over_its_standard_error(self) -> None:
        rng = np.random.default_rng(10)
        signal, outcome, dates = _panel(_every(5, 700), 30, rng, timing=False)
        result = cross_sectional_ic(signal, outcome, dates, 63)
        assert result is not None and result.t is not None
        smallest = result.detectable(abs(result.t))
        assert smallest == pytest.approx(abs(result.ic), rel=1e-9)
