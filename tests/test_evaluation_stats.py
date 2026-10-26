"""The statistics of the table of a spec: a quantile by linear interpolation, the
median and the interquartile range, and a bootstrap interval that resamples whole repetitions
from a recorded seed. Each value here is computed by hand.
"""

from __future__ import annotations

import pytest

from jms_kapps.evaluation.stats import (
    bootstrap_intervals,
    difference_of_means,
    mean,
    median_and_iqr,
    pooled_quantiles,
    quantile,
)


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        # h = (n - 1) p: of 1..9, p95 is at h = 7.6, between 8 and 9.
        (list(range(1, 10)), 0.95, 8.6),
        (list(range(1, 10)), 0.99, 8.92),
        (list(range(1, 10)), 0.5, 5.0),
        ([4, 1, 3, 2], 0.5, 2.5),
        ([7.0], 0.99, 7.0),
        ([1, 2], 0.0, 1.0),
        ([1, 2], 1.0, 2.0),
    ],
)
def test_a_quantile_interpolates_linearly_between_the_two_closest_ranks(
    values, p, expected
):
    assert quantile(values, p) == pytest.approx(expected)


def test_no_values_have_no_quantile_and_no_mean():
    assert quantile([], 0.5) is None
    assert mean([]) is None
    assert median_and_iqr([]) is None


def test_the_median_and_the_interquartile_range_of_five_repetitions():
    """Of 10, 20, 30, 40, 100: the first quartile at h = 1, the third at h = 3."""
    assert median_and_iqr([40, 10, 100, 30, 20]) == {
        "median": 30,
        "q1": 20,
        "q3": 40,
        "iqr": 20,
        "runs": 5,
    }


def test_the_median_and_the_interquartile_range_of_four_repetitions():
    """Of 10, 20, 40, 80: Q1 at h = 0.75 is 17.5, the median 30, Q3 at h = 2.25 is 50."""
    found = median_and_iqr([80, 10, 40, 20])

    assert found == pytest.approx(
        {"median": 30, "q1": 17.5, "q3": 50, "iqr": 32.5, "runs": 4}
    )


def test_the_pooled_quantile_pools_the_values_of_every_repetition():
    found = pooled_quantiles(
        [[1, 2, 3], [4, 5], [6, 7, 8, 9]],
        [0.5, 0.95, 0.99],
        seed=0,
        drawn_for="test",
        resamples=50,
    )

    assert [quantile_found["value"] for quantile_found in found] == pytest.approx(
        [5.0, 8.6, 8.92]
    )


def test_the_bootstrap_resamples_whole_repetitions_so_one_repetition_gives_one_point():
    """One repetition of four latencies: a resample draws that repetition, whole, every time, so
    its median never varies. A bootstrap of the single latencies would draw them one by one, and
    its interval would span from 0.1 to 0.4."""
    [interval] = bootstrap_intervals(
        [[0.1, 0.2, 0.3, 0.4]], [0.5], seed=7, drawn_for="test", resamples=1000
    )

    assert interval == pytest.approx([0.25, 0.25])


def test_the_interval_is_the_2_5_and_the_97_5_percent_quantile_of_the_drawn_statistics():
    """Two repetitions, [1] and [3]. A resample draws two of them: both [1] a quarter of the
    time, median 1; both [3] a quarter, median 3; one of each half the time, median 2. So the
    2.5 % quantile of the drawn medians is 1 and the 97.5 % quantile 3."""
    for seed in range(5):
        [interval] = bootstrap_intervals(
            [[1.0], [3.0]], [0.5], seed=seed, drawn_for="test", resamples=2000
        )

        assert interval == [1.0, 3.0]


def test_the_same_seed_gives_the_same_interval_and_another_seed_another():
    repetitions = [
        [0.1, 0.5, 0.9],
        [0.2, 0.3],
        [1.5, 0.4, 0.6, 0.8],
        [0.7],
        [2.0, 0.05],
    ]

    first = bootstrap_intervals(
        repetitions, [0.5, 0.95], seed=49, drawn_for="cell", resamples=500
    )
    again = bootstrap_intervals(
        repetitions, [0.5, 0.95], seed=49, drawn_for="cell", resamples=500
    )
    other_seeds = {
        tuple(
            map(
                tuple,
                bootstrap_intervals(  # type: ignore[arg-type]
                    repetitions, [0.5, 0.95], seed=seed, drawn_for="cell", resamples=500
                ),
            )
        )
        for seed in range(50, 60)
    }

    assert first == again
    assert len(other_seeds | {tuple(map(tuple, first))}) > 1  # type: ignore[arg-type]


def test_an_interval_does_not_depend_on_the_other_intervals_drawn():
    """Each interval draws from a generator of its own, seeded with the seed and the name of what
    it is drawn for: an interval drawn after another is the one drawn alone."""
    repetitions = [[0.1, 0.5], [0.2, 0.3], [1.5, 0.4]]
    alone = bootstrap_intervals(
        repetitions, [0.95], seed=3, drawn_for="b", resamples=300
    )
    bootstrap_intervals(repetitions, [0.95], seed=3, drawn_for="a", resamples=300)

    assert (
        bootstrap_intervals(repetitions, [0.95], seed=3, drawn_for="b", resamples=300)
        == alone
    )


def test_the_difference_of_means_is_of_the_pooled_values():
    """(1 + 2 + 3) / 3 minus (1 + 1) / 2 is 1."""
    found = difference_of_means(
        [[1.0, 2.0], [3.0]], [[1.0], [1.0]], seed=0, drawn_for="d", resamples=100
    )

    assert found is not None
    assert found["value"] == pytest.approx(1.0)


def test_one_repetition_each_gives_an_interval_of_one_point():
    found = difference_of_means(
        [[2.0, 4.0]], [[1.0]], seed=0, drawn_for="d", resamples=50
    )

    assert found == {"value": 2.0, "interval": [2.0, 2.0]}


def test_the_difference_resamples_each_side_independently():
    """With one repetition on the second side, only the first side's draws move the
    difference: the interval runs from the first side's lowest mean to its highest, less 0."""
    found = difference_of_means(
        [[1.0], [5.0]], [[0.0]], seed=0, drawn_for="d", resamples=2000
    )

    assert found is not None
    assert found["interval"] == pytest.approx([1.0, 5.0])


def test_the_difference_is_none_where_a_side_has_no_value_and_is_seeded():
    assert (
        difference_of_means([[1.0]], [[]], seed=0, drawn_for="d", resamples=10) is None
    )
    first = [[0.1, 0.5], [0.2, 0.3], [1.5, 0.4]]
    second = [[0.1], [0.05, 0.2]]
    once = difference_of_means(first, second, seed=3, drawn_for="d", resamples=300)

    assert (
        difference_of_means(first, second, seed=3, drawn_for="d", resamples=300) == once
    )
