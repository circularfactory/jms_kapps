"""The precision rule of a sweep: when a configuration has run often enough. Interim names.

A configuration **meets the rule** when all of these hold, over its valid runs:

- it has at least `minimum_runs` of them (10);
- the bootstrap 95 % interval of its pooled commit p95 lies within `latency_tolerance` (10 %) of
  the p95 on both sides: both ends are at most 10 % of the p95 away from it;
- the IQR of its transports ended per minute is at most `throughput_spread` (10 %) of their median.

A configuration without the wrapper (`uc2-unwrapped`) has no commit latency and is judged on the
throughput alone. A median of 0 meets no spread: a ratio to it means nothing.

**A throughput median of 0 stops the configuration** (interim names). Where the median of its transports ended per minute is 0 while it has at least
`minimum_runs` valid runs, it can never meet the rule, so it needs no more runs
(`Verdict.zero_median`, `Verdict.stopped`), and its status is `stopped: throughput median 0`,
also where it made `maximum_runs` before the stop. This holds with the wrapper and without it.


**The numbers are the table's.** `judge` reads a cell of the configurations table (`table`): its
pooled commit p95 with its interval and its transports' median and IQR, which `table.rule_numbers`
makes with the table's functions and defaults (seed 0, 10 000 resamples), so the rule never holds
a second version of a statistic.

**Counting.** Only valid runs count toward the rule and the minimum (they are the runs of the
table's cell). Every run made counts toward the maximum, valid or not, a failed one too; a run that
a stop signal interrupted does not (`made_repetitions`): the next call makes that repetition again.
A configuration that has made `maximum_runs` without meeting the rule stops, and the table marks it
"precision not reached".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from jms_kapps.evaluation.bench import INTERRUPTED
from jms_kapps.evaluation.spec import PrecisionRule
from jms_kapps.evaluation.summary import RunSummary


def made_repetitions(summaries: Iterable[RunSummary]) -> set[int]:
    """The repetitions that have been made: those with a record that is not interrupted. A run that
    a stop signal ended is made again, so it is not among them."""
    return {
        summary.repetition
        for summary in summaries
        if summary.repetition is not None and summary.outcome != INTERRUPTED
    }


@dataclass(frozen=True)
class Verdict:
    """How a configuration stands against the rule."""

    met: bool
    made: int
    valid: int
    maximum: int
    # The conditions that do not hold, each as a sentence.
    unmet: tuple[str, ...] = ()
    commit_p95: float | None = None
    commit_p95_interval: tuple[float, float] | None = None
    throughput_median: float | None = None
    throughput_iqr: float | None = None
    # Whether the throughput median of its valid runs is 0 while it has at least the minimum of
    # them: it stops there.
    zero_median: bool = False

    @property
    def stopped(self) -> bool:
        """Whether the configuration needs no more runs: it meets the rule, its throughput
        median is 0 at its minimum of valid runs or later, or it has made its maximum."""
        return self.met or self.zero_median or self.made >= self.maximum

    @property
    def status(self) -> str:
        """The verdict in the words a sweep's line shows. It is `met`, or
        `stopped: throughput median 0` where the median is 0 at the minimum of valid runs or
        later, `precision not reached (n runs)` where the maximum is made without it, or
        `pending (n runs)`."""
        if self.met:
            return "met"
        if self.zero_median:
            return "stopped: throughput median 0"
        if self.made >= self.maximum:
            return f"precision not reached ({self.made} runs)"
        return f"pending ({self.made} runs)"

    def record(self) -> dict[str, Any]:
        """The verdict for the table's JSON."""
        return {
            "status": self.status,
            "met": self.met,
            "runs_made": self.made,
            "valid_runs": self.valid,
            "maximum_runs": self.maximum,
            "unmet": list(self.unmet),
            "commit_p95": self.commit_p95,
            "commit_p95_interval": None
            if self.commit_p95_interval is None
            else list(self.commit_p95_interval),
            "throughput_median": self.throughput_median,
            "throughput_iqr": self.throughput_iqr,
        }


def judge(
    numbers: Mapping[str, Any] | None,
    *,
    made: int,
    wrapper: bool,
    rule: PrecisionRule,
) -> Verdict:
    """The verdict on a configuration of the table's `numbers` for its valid runs (a cell of the
    table, or `table.rule_numbers`; None where no run is valid), with `made` runs made, and with
    the wrapper or without it. It stops at a throughput median of 0 once there are at least
    `minimum_runs` valid runs (`Verdict.zero_median`)."""
    valid = len(numbers["runs"]) if numbers else 0
    unmet: list[str] = []
    if valid < rule.minimum_runs:
        unmet.append(f"{valid} valid runs, {rule.minimum_runs} needed")
    p95: float | None = None
    interval: tuple[float, float] | None = None
    if wrapper:
        latency = (numbers or {}).get("commit_latency_seconds")
        found = None if latency is None else latency.get("p95")
        if found is None or found.get("interval") is None:
            unmet.append("no commit p95")
        else:
            p95 = found["value"]
            interval = (found["interval"][0], found["interval"][1])
            assert p95 is not None
            if (
                p95 - interval[0] > rule.latency_tolerance * p95
                or interval[1] - p95 > rule.latency_tolerance * p95
            ):
                unmet.append(
                    f"the commit p95 {p95:g} s has the interval {interval[0]:g} to "
                    f"{interval[1]:g} s, beyond {rule.latency_tolerance:.0%}"
                )
    spread = (numbers or {}).get("transports_ended_per_minute")
    median = None if spread is None else spread["median"]
    iqr = None if spread is None else spread["iqr"]
    if median is None or iqr is None:
        unmet.append("no throughput")
    elif median <= 0 or iqr > rule.throughput_spread * median:
        unmet.append(
            f"the throughput has the median {median:g} and the IQR {iqr:g} per minute, "
            f"beyond {rule.throughput_spread:.0%}"
        )
    return Verdict(
        met=not unmet,
        made=made,
        valid=valid,
        maximum=rule.maximum_runs,
        unmet=tuple(unmet),
        commit_p95=p95,
        commit_p95_interval=interval,
        throughput_median=median,
        throughput_iqr=iqr,
        zero_median=valid >= rule.minimum_runs and median is not None and median <= 0,
    )
