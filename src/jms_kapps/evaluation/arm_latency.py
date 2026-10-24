"""The commit latency of two arms of one comparison, from the run records alone (interim name).

An arm is a folder of run records, one folder for each run, as `summary.read_summaries` reads them.
The comparison of the middleware's operation-status shape runs the same configuration in two
arms, once with the shape and once without it, and this reports each arm's commit latency: the
median and the 95th percentile of the latencies of the commits the store admitted, in the
measured window, pooled over the runs of the arm that count (completed and not invalid). Pure
functions apart from the folder read; no store, no clock.

Run it as `python -m jms_kapps.evaluation.arm_latency WITH_DIR WITHOUT_DIR`. It prints one block
for each arm: the number of runs that count, those left out with why, the number of commits, and
the two latencies in milliseconds.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from jms_kapps.evaluation.record import RUN_KIND
from jms_kapps.evaluation.stats import quantile
from jms_kapps.evaluation.summary import RunSummary, read_summaries


@dataclass(frozen=True)
class ArmLatency:
    """The commit latency of one arm, in seconds. `median` and `p95` are None where no counting
    run holds a commit."""

    runs: int
    left_out: Sequence[str]
    commits: int
    median: float | None
    p95: float | None


def arm_latency(summaries: Iterable[RunSummary]) -> ArmLatency:
    """The commit latency of the runs of use case 2 among `summaries`, pooled over those that
    count."""
    uc2 = [run for run in summaries if run.run_kind == RUN_KIND]
    counting = [run for run in uc2 if run.counts]
    left_out = [f"{run.run_id}: {run.why_left_out()}" for run in uc2 if not run.counts]
    pooled = [latency for run in counting for latency in run.commit_latencies or ()]
    return ArmLatency(
        runs=len(counting),
        left_out=left_out,
        commits=len(pooled),
        median=quantile(pooled, 0.5),
        p95=quantile(pooled, 0.95),
    )


def render(name: str, arm: ArmLatency) -> str:
    """The block of one arm: its runs, those left out, its commits and its two latencies."""
    lines = [f"{name}: {arm.runs} runs that count, {arm.commits} commits"]
    lines += [f"  left out, {why}" for why in arm.left_out]
    lines.append(f"  median {_ms(arm.median)}, 95th percentile {_ms(arm.p95)}")
    return "\n".join(lines)


def _ms(seconds: float | None) -> str:
    return "none" if seconds is None else f"{seconds * 1000:.1f} ms"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The commit latency of the arm with the middleware's operation-status shape "
        "and of the arm without it, from the run records of each."
    )
    parser.add_argument(
        "with_shape", type=Path, help="the folder of the arm with the shape"
    )
    parser.add_argument(
        "without_shape", type=Path, help="the folder of the arm without it"
    )
    args = parser.parse_args(argv)
    print(render("with the shape", arm_latency(read_summaries(args.with_shape))))
    print(render("without the shape", arm_latency(read_summaries(args.without_shape))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
