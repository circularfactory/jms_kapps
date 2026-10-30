"""The statistics of the commit time figure (`commit_time/figure.py`): a
quantile, the median and the interquartile range, and a bootstrap interval that resamples whole
repetitions.

**A quantile** is read by linear interpolation between the two closest ranks, as numpy and R
read it by default (R's type 7): of n sorted values x[0] to x[n-1], the quantile p lies at
h = (n - 1) p, and is x[floor h] + (h - floor h) (x[floor h + 1] - x[floor h]). So the median of
1, 2, 3, 4 is 2.5, and the 95th percentile of 1 to 9 is 8.6.

**The median and the interquartile range** of the values of the repetitions, one value each, as
the throughput is reported: the median, the first and the third quartile, both as above, and the
range between them.

**The bootstrap** gives a 95 % interval of a pooled quantile, as the time per commit is reported.
The latencies of a repetition are not independent of each other, so it resamples whole repetitions:
of R repetitions, each with its values, one resample draws R repetitions with replacement and
pools their values, and the statistic is the quantile of that pool. The interval is the 2.5 % and
the 97.5 % quantile of the statistics of all resamples (the percentile interval). The draws come
from a random generator seeded with the recorded seed and the name of what is drawn for, so the
same seed gives the same interval, and an interval does not depend on the other intervals a table
holds. A single repetition gives an interval of one point: there is nothing to resample.

**The difference of two means** (`difference_of_means`), as the commit time figures
report SHACL on minus SHACL off: the mean of the pooled values of one set of repetitions minus that
of another, with a bootstrap interval in which each set's repetitions are resampled independently,
because the two are not paired.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

# The seed of the bootstrap, and how many resamples it draws, unless the command is given others.
DEFAULT_SEED = 0
DEFAULT_RESAMPLES = 10_000

# The interval of the bootstrap: 95 %.
INTERVAL = 0.95


def quantile(values: Sequence[float], p: float) -> float | None:
    """The quantile `p` of `values`, by linear interpolation between the two closest ranks; None
    where there are no values."""
    return _quantile_of_sorted(sorted(values), p)


def mean(values: Sequence[float]) -> float | None:
    """The mean of `values`, or None where there are none."""
    return math.fsum(values) / len(values) if values else None


def bootstrap_intervals(
    repetitions: Sequence[Sequence[float]],
    quantiles: Sequence[float],
    *,
    seed: int,
    drawn_for: str,
    resamples: int,
) -> list[list[float] | None]:
    """The 95 % bootstrap interval of each pooled quantile of `quantiles`, `[low, high]`, from
    `resamples` resamples of whole repetitions; None where no resample has a value."""
    if resamples < 1:
        raise ValueError(f"a bootstrap draws at least one resample, not {resamples}")
    count = len(repetitions)
    generator = random.Random(f"{seed}/{drawn_for}")
    # A resample is a multiset of repetitions: one drawn again is pooled once.
    pooled_of: dict[tuple[int, ...], list[float | None]] = {}
    statistics: list[list[float]] = [[] for _ in quantiles]
    for _ in range(resamples):
        drawn = tuple(sorted(generator.randrange(count) for _ in range(count)))
        found = pooled_of.get(drawn)
        if found is None:
            pool = sorted(value for index in drawn for value in repetitions[index])
            found = [_quantile_of_sorted(pool, p) for p in quantiles]
            pooled_of[drawn] = found
        for drawn_statistics, statistic in zip(statistics, found, strict=True):
            if statistic is not None:
                drawn_statistics.append(statistic)
    tail = (1 - INTERVAL) / 2
    intervals: list[list[float] | None] = []
    for drawn_statistics in statistics:
        low = quantile(drawn_statistics, tail)
        high = quantile(drawn_statistics, 1 - tail)
        intervals.append(None if low is None or high is None else [low, high])
    return intervals


def difference_of_means(
    first: Sequence[Sequence[float]],
    second: Sequence[Sequence[float]],
    *,
    seed: int,
    drawn_for: str,
    resamples: int,
) -> dict[str, Any] | None:
    """The mean of the pooled values of the repetitions `first` minus that of `second`, with its
    bootstrap interval, as `{"value": ..., "interval": [low, high]}`; None where either has no
    value. The two are not paired, so a resample draws the repetitions of each with replacement,
    independently, and the statistic is the difference of the two pooled means.

    Args:
        first: The values of each repetition of the first.
        second: The values of each repetition of the second.
        seed: The recorded seed.
        drawn_for: The name of what is drawn for, with the seed the generator's seed.
        resamples: How many resamples the interval draws.
    """
    if resamples < 1:
        raise ValueError(f"a bootstrap draws at least one resample, not {resamples}")
    first_mean = mean([value for repetition in first for value in repetition])
    second_mean = mean([value for repetition in second for value in repetition])
    if first_mean is None or second_mean is None:
        return None
    generator = random.Random(f"{seed}/{drawn_for}")
    differences = []
    for _ in range(resamples):
        drawn_first = [first[generator.randrange(len(first))] for _ in first]
        drawn_second = [second[generator.randrange(len(second))] for _ in second]
        first_drawn = mean(
            [value for repetition in drawn_first for value in repetition]
        )
        second_drawn = mean(
            [value for repetition in drawn_second for value in repetition]
        )
        if first_drawn is not None and second_drawn is not None:
            differences.append(first_drawn - second_drawn)
    tail = (1 - INTERVAL) / 2
    low = quantile(differences, tail)
    high = quantile(differences, 1 - tail)
    return {
        "value": first_mean - second_mean,
        "interval": None if low is None or high is None else [low, high],
    }


def _quantile_of_sorted(ordered: Sequence[float], p: float) -> float | None:
    if not 0 <= p <= 1:
        raise ValueError(f"a quantile is from 0 to 1, not {p}")
    if not ordered:
        return None
    at = (len(ordered) - 1) * p
    below = math.floor(at)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (at - below) * (ordered[above] - ordered[below])
