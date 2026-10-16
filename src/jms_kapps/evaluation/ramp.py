"""The broken rule of the measurement harness, and how far a ramp went (iteration one; decided by
Etienne on 2026-10-11, the numbers decision, jms_kapps 56; the stop rule and the judgement changed
on 2026-10-12, jms_kapps 99, decided by Sören Weindel as the product owner of the tests,
jms_kapps 85).

**A ramp** is a sweep block marked as a ramp (`ramp = true`), whose only axis is its rising box
rates (`spec`). Each rate is one **load level**, made by one run. Its runs are heavy, so a ramp
runs only under an approval that covers its spec for the period (`approval`; any person may
approve, jms_kapps 96). The sweep runs a ramp one
rate per run, in rising order, judges each level with the broken rule after its run, and stops
the ramp at the first level it ends at (`sweep`).

**The broken rule.** A load level is broken at the first store-side signal, from its runs of use
case 2 with the wrapper (`uc2`), each over its measured window but the first:

- a refusal for want of heap (`heap refusal`), under the load: in the warm-up, the window and
  the drain (`summary`);
- more than 1 % of the store requests refused or unanswered, timed out or with their connection
  dropped (`refused or unanswered`; until 2026-10-14 named `refused or timed out`, the same sum,
  jms_kapps 109);
- a p95 commit latency more than 10 times its value at the ramp's lowest rate, the lowest box
  rate of the same configuration (`p95 commit latency`).

A fourth signal, a plateau (`plateau`: the completed writes per minute rose by less than 5 % from
the rate step before), is still found and shown in the record of the level, but it breaks no
level and stops no ramp: a plateau alone cannot tell whether the store, KAPPS or UC2 is the limit.

Stuck or failed boxes alone never break a level: they are counted for each level, beside it, and
UC2's failures are no store-side signal. A level whose runs hold several repetitions pools them:
the shares and the p95 over the requests and the commits of all of them, the writes per minute as
their median.

**UC2-limited.** A level is UC2-limited when fewer than 90 % of the spawns offered in its window
are accepted (`ACCEPTED_SHARE`, a judgement setting outside the configuration's key). Offered
spawns are the spawned and the refused ones, counted in the measured window from the
WarehouseManagementSystem's time-stamped output lines (`record.spawns_in_window`); the accepted
share is the accepted over the offered. A UC2-limited level breaks nothing: the ramp goes on.

**The end of a ramp.** A ramp ends at its first broken level, and its **scale** is the level
before it; none where the ramp broke at its lowest rate. A ramp also ends at a level that has a
run but no run that counts (a run that failed, or one that a check found invalid): such a level
cannot be judged, and the ramp sends no higher rate to the store. A level whose only run a stop
signal ended has not run yet, and runs again. A ramp none of whose levels broke has no scale yet:
it was not broken up to its highest rate. Each repetition of a ramp, a repetition on request in a
spec with the precision rule (`heavy_parts`, jms_kapps 114), is judged apart, from its own runs.

**The judgement of a ramp** is one of three. *UC2-limited at level X* when the first UC2-limited
level comes before the first broken level, or when no level broke. *Broken at level Y (signal)*
when a level broke before any UC2-limited level. *Cannot tell* when neither happened (only a
plateau, or the top level reached, or no spawn counts in the records), or both happened at the
same level. The table compares a UC2-limited ramp with the replay of the same configuration
(`ramp_comparison`).

(Added on 2026-10-12, jms_kapps 49.)

(Changed on 2026-10-12, jms_kapps 99, decided by Sören Weindel as the product owner of the tests,
jms_kapps 85. Until then a level was broken on four signals, a plateau of the completed writes
among them, and the ramp stopped at its first broken level, so a UC2-limited level, which usually
plateaus, stopped the ramp and its plateau said nothing about the store or KAPPS. The plateau is
now only shown; the judgement and the accepted share are new.)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jms_kapps.evaluation.bench import INTERRUPTED
from jms_kapps.evaluation.record import RUN_KIND
from jms_kapps.evaluation.spec import SpecConfiguration, ramps_of
from jms_kapps.evaluation.stats import quantile
from jms_kapps.evaluation.summary import RunSummary, repetition_of, repetitions_made

# The four store-side signals of the broken rule. `refused or unanswered` counts the requests that
# the store refused, that timed out and whose connection dropped; until 2026-10-14 it was named
# `refused or timed out`, for the same sum (jms_kapps 109, decided by Sören on jms_kapps 85, note
# 1866296, decision 2; interim names).
HEAP_REFUSAL = "heap refusal"
REFUSED_OR_UNANSWERED = "refused or unanswered"
P95_COMMIT_LATENCY = "p95 commit latency"
PLATEAU = "plateau"
SIGNALS = (HEAP_REFUSAL, REFUSED_OR_UNANSWERED, P95_COMMIT_LATENCY, PLATEAU)
# The signals that break a level: the plateau is found and shown, but breaks none (jms_kapps 99).
BREAKING_SIGNALS = (HEAP_REFUSAL, REFUSED_OR_UNANSWERED, P95_COMMIT_LATENCY)

# The names that a record written before keeps for a signal, each with its name now: a reader
# shows the name now (`signal_name`), and no record file is changed (jms_kapps 109).
EARLIER_SIGNAL_NAMES = {"refused or timed out": REFUSED_OR_UNANSWERED}

# Their bounds: more than 1 % refused or unanswered; a p95 commit latency more than 10 times the
# lowest rate's; less than 5 % more completed writes per minute than the step before.
REFUSED_OR_UNANSWERED_SHARE = 0.01
P95_COMMIT_LATENCY_FACTOR = 10.0
PLATEAU_RISE = 0.05

# A level is UC2-limited when fewer than 90 % of its offered spawns are accepted (jms_kapps 99,
# interim name): a judgement setting outside the configuration's key.
ACCEPTED_SHARE = 0.9

# The three judgements of a ramp (jms_kapps 99, interim names).
UC2_LIMITED = "UC2-limited"
BROKEN = "broken"
CANNOT_TELL = "cannot tell"


@dataclass(frozen=True)
class LevelNumbers:
    """What the broken rule reads of one load level, over its runs that count."""

    heap_refusals: int
    store_requests: int
    refused_or_unanswered: int
    p95_commit_latency: float | None
    completed_writes_per_minute: float | None
    # The spawns of the window (jms_kapps 99): None where no run of the level holds a count.
    spawns_offered: int | None = None
    spawns_accepted: int | None = None
    # The store requests per minute of the window the runs pooled, or None.
    store_requests_per_minute: float | None = None

    @property
    def accepted_share(self) -> float | None:
        if not self.spawns_offered:
            return None
        return (self.spawns_accepted or 0) / self.spawns_offered

    @property
    def uc2_limited(self) -> bool | None:
        """Whether fewer than `ACCEPTED_SHARE` of the offered spawns were accepted; None where
        no spawn was offered, or the runs hold no count."""
        share = self.accepted_share
        return None if share is None else share < ACCEPTED_SHARE

    @property
    def refused_or_unanswered_share(self) -> float | None:
        if not self.store_requests:
            return None
        return self.refused_or_unanswered / self.store_requests

    def record(self) -> dict[str, Any]:
        return {
            "heap_refusals_under_load": self.heap_refusals,
            "store_requests_in_window": self.store_requests,
            "refused_or_unanswered_in_window": self.refused_or_unanswered,
            "refused_or_unanswered_share": self.refused_or_unanswered_share,
            "p95_commit_latency_seconds": self.p95_commit_latency,
            "completed_writes_per_minute": self.completed_writes_per_minute,
            "spawns_offered": self.spawns_offered,
            "spawns_accepted": self.spawns_accepted,
            "spawns_accepted_share": self.accepted_share,
            "store_requests_per_minute": self.store_requests_per_minute,
        }


def level_numbers(runs: Sequence[RunSummary]) -> LevelNumbers:
    """The numbers of a load level from `runs`, the runs of use case 2 with the wrapper that count
    at that level: the counts summed, the commit latencies pooled, and the completed writes per
    minute as the median of the runs."""
    latencies = [value for run in runs for value in (run.commit_latencies or [])]
    rates = [
        run.completed_writes_per_minute
        for run in runs
        if run.completed_writes_per_minute is not None
    ]
    counted = [run for run in runs if run.spawns_offered_in_window is not None]
    timed = [
        run
        for run in runs
        if run.window_minutes and run.store_requests_in_window is not None
    ]
    minutes = sum(run.window_minutes or 0 for run in timed)
    return LevelNumbers(
        spawns_offered=sum(run.spawns_offered_in_window or 0 for run in counted)
        if counted
        else None,
        spawns_accepted=sum(run.spawns_accepted_in_window or 0 for run in counted)
        if counted
        else None,
        store_requests_per_minute=sum(
            run.store_requests_in_window or 0 for run in timed
        )
        / minutes
        if minutes
        else None,
        heap_refusals=sum(run.heap_refusals_under_load or 0 for run in runs),
        store_requests=sum(run.store_requests_in_window or 0 for run in runs),
        refused_or_unanswered=sum(
            run.refused_or_unanswered_in_window or 0 for run in runs
        ),
        p95_commit_latency=quantile(latencies, 0.95),
        completed_writes_per_minute=quantile(rates, 0.5),
    )


def signal_name(name: str) -> str:
    """The name now of a signal of the broken rule that a record names `name`: a record written
    before jms_kapps 109 says `refused or timed out` (`EARLIER_SIGNAL_NAMES`)."""
    return EARLIER_SIGNAL_NAMES.get(name, name)


def not_judged(
    level: LevelNumbers,
    *,
    lowest: LevelNumbers | None,
    before: LevelNumbers | None,
) -> list[str]:
    """The signals of the broken rule that `level` cannot show, for want of a number, each with
    why: no store request in the window, or no commit or no completed write at this level, at the
    ramp's lowest rate or at the step before. The lowest level is compared with none, so its p95
    and its plateau are not judged at all, and are not named."""
    found = []
    if level.refused_or_unanswered_share is None:
        found.append(f"{REFUSED_OR_UNANSWERED}: no store request in the window")
    if lowest is not None and (
        level.p95_commit_latency is None or lowest.p95_commit_latency is None
    ):
        found.append(
            f"{P95_COMMIT_LATENCY}: no commit in the window"
            + (" at the lowest rate" if lowest.p95_commit_latency is None else "")
        )
    if before is not None and (
        level.completed_writes_per_minute is None
        or before.completed_writes_per_minute is None
    ):
        found.append(f"{PLATEAU}: no completed writes per minute to compare")
    return found


def signals(
    level: LevelNumbers,
    *,
    lowest: LevelNumbers | None,
    before: LevelNumbers | None,
) -> list[dict[str, Any]]:
    """Each signal of the broken rule that `level` shows, with the numbers that show it.

    Args:
        level: The numbers of the level judged.
        lowest: Those of the ramp's lowest rate, or None where `level` is it.
        before: Those of the rate step before, or None where `level` is the lowest.
    """
    found: list[dict[str, Any]] = []
    if level.heap_refusals > 0:
        found.append({"signal": HEAP_REFUSAL, "heap_refusals": level.heap_refusals})
    share = level.refused_or_unanswered_share
    if share is not None and share > REFUSED_OR_UNANSWERED_SHARE:
        found.append({"signal": REFUSED_OR_UNANSWERED, "share": share})
    if (
        lowest is not None
        and level.p95_commit_latency is not None
        and lowest.p95_commit_latency is not None
        and level.p95_commit_latency
        > P95_COMMIT_LATENCY_FACTOR * lowest.p95_commit_latency
    ):
        found.append(
            {
                "signal": P95_COMMIT_LATENCY,
                "p95_commit_latency_seconds": level.p95_commit_latency,
                "at_the_lowest_rate": lowest.p95_commit_latency,
            }
        )
    if (
        before is not None
        and level.completed_writes_per_minute is not None
        and before.completed_writes_per_minute is not None
        and level.completed_writes_per_minute
        < (1 + PLATEAU_RISE) * before.completed_writes_per_minute
    ):
        found.append(
            {
                "signal": PLATEAU,
                "completed_writes_per_minute": level.completed_writes_per_minute,
                "at_the_step_before": before.completed_writes_per_minute,
            }
        )
    return found


@dataclass(frozen=True)
class Step:
    """One load level of a ramp: its box rate, the number of its configuration in the spec, and
    the summaries of its runs, of any run kind and outcome."""

    boxes_per_minute: float
    configuration: int
    runs: Sequence[RunSummary] = ()


def breaks(found: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the signals `found` (`signals`) break a level: any but the plateau."""
    return any(signal["signal"] in BREAKING_SIGNALS for signal in found)


@dataclass(frozen=True)
class RampJudgement:
    """The judgement of a ramp (jms_kapps 99, interim names): `UC2_LIMITED` at the box rate
    `level`, `BROKEN` at `level` by `signals`, or `CANNOT_TELL`."""

    kind: str = CANNOT_TELL
    level: float | None = None
    signals: Sequence[str] = ()

    @property
    def text(self) -> str:
        if self.kind == UC2_LIMITED:
            return f"{UC2_LIMITED} at {_rate(self.level)}"
        if self.kind == BROKEN:
            return f"{BROKEN} at {_rate(self.level)} ({', '.join(self.signals)})"
        return CANNOT_TELL

    def record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "level": self.level,
            "signals": list(self.signals),
            "text": self.text,
        }


def judgement_of(
    uc2_limited_at: float | None,
    broken_at: float | None,
    broken_by: Sequence[str],
) -> RampJudgement:
    """The judgement of a ramp from the box rate of its first UC2-limited level, that of its first
    broken level and the signals that broke it, each None where no level was: UC2-limited when
    the limited level comes before the broken one or none broke, broken when a level broke before
    any limited one, and cannot tell when neither happened or both at the same level."""
    if uc2_limited_at is not None and (broken_at is None or uc2_limited_at < broken_at):
        return RampJudgement(UC2_LIMITED, uc2_limited_at)
    if broken_at is not None and (uc2_limited_at is None or broken_at < uc2_limited_at):
        return RampJudgement(BROKEN, broken_at, tuple(broken_by))
    return RampJudgement()


@dataclass(frozen=True)
class Judgement:
    """How far a ramp went: each level judged, in rising order, with its numbers and the signals
    it showed, and where the ramp ended, if it did. `uc2_limited_at` is the box rate of its
    first UC2-limited level, and `judgement` the ramp's judgement (jms_kapps 99, interim)."""

    levels: Sequence[Mapping[str, Any]] = field(default_factory=list)
    ended: bool = False
    broken_at: float | None = None
    scale: float | None = None
    stopped_at: float | None = None
    why: str = ""
    uc2_limited_at: float | None = None
    judgement: RampJudgement = field(default_factory=RampJudgement)

    def record(self) -> dict[str, Any]:
        return {
            "levels": list(self.levels),
            "ended": self.ended,
            "broken_at": self.broken_at,
            "scale": self.scale,
            "stopped_at": self.stopped_at,
            "why": self.why,
            "uc2_limited_at": self.uc2_limited_at,
            "judgement": self.judgement.record(),
        }


def judge(steps: Sequence[Step]) -> Judgement:
    """Judge the levels of a ramp, `steps` in rising order of their box rates, with the broken
    rule, up to the first level the ramp ends at, or the first that has not run yet. A plateau
    and a UC2-limited level break nothing and end nothing."""
    levels: list[dict[str, Any]] = []
    lowest: LevelNumbers | None = None
    before: LevelNumbers | None = None
    before_rate: float | None = None
    limited_at: float | None = None
    for step in steps:
        made = [
            run
            for run in step.runs
            if run.run_kind == RUN_KIND and run.outcome != INTERRUPTED
        ]
        if not made:
            return Judgement(
                levels,
                why=f"{_rate(step.boxes_per_minute)} has not run yet",
                uc2_limited_at=limited_at,
                judgement=judgement_of(limited_at, None, ()),
            )
        counting = [run for run in made if run.counts]
        shown: dict[str, Any] = {
            "boxes_per_minute": step.boxes_per_minute,
            "configuration": step.configuration,
            "runs": [run.run_id for run in counting],
        }
        if not counting:
            levels.append(
                shown
                | {
                    "judged": False,
                    "broken": None,
                    "uc2_limited": None,
                    "why": "no run that counts: "
                    + "; ".join(str(run.why_left_out()) for run in made),
                }
            )
            return Judgement(
                levels,
                ended=True,
                scale=None,
                stopped_at=step.boxes_per_minute,
                why=(
                    f"it stopped at {_rate(step.boxes_per_minute)}, which has no run that "
                    "counts, so the level cannot be judged and no higher rate is sent"
                ),
                uc2_limited_at=limited_at,
                judgement=judgement_of(limited_at, None, ()),
            )
        numbers = level_numbers(counting)
        found = signals(numbers, lowest=lowest, before=before)
        broken = breaks(found)
        limited = numbers.uc2_limited
        levels.append(
            shown
            | {
                "judged": True,
                "broken": broken,
                "uc2_limited": limited,
                "signals": found,
                "not_judged": not_judged(numbers, lowest=lowest, before=before),
                "numbers": numbers.record(),
            }
        )
        if limited and limited_at is None:
            limited_at = step.boxes_per_minute
        if broken:
            breaking = [
                str(signal["signal"])
                for signal in found
                if signal["signal"] in BREAKING_SIGNALS
            ]
            return Judgement(
                levels,
                ended=True,
                broken_at=step.boxes_per_minute,
                scale=before_rate,
                why=(
                    f"it broke at {_rate(step.boxes_per_minute)} ({', '.join(breaking)}); "
                    + (
                        f"its scale is {_rate(before_rate)}"
                        if before_rate is not None
                        else "that is its lowest rate, so it has no scale"
                    )
                ),
                uc2_limited_at=limited_at,
                judgement=judgement_of(limited_at, step.boxes_per_minute, breaking),
            )
        if lowest is None:
            lowest = numbers
        before, before_rate = numbers, step.boxes_per_minute
    return Judgement(
        levels,
        why=(
            f"it was not broken up to {_rate(before_rate)}, its highest rate"
            if before_rate is not None
            else "it has no level"
        ),
        uc2_limited_at=limited_at,
        judgement=judgement_of(limited_at, None, ()),
    )


def steps_of(
    levels: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    *,
    repetition: int = 1,
) -> list[Step]:
    """The steps of a ramp, its `levels` in rising order, each with the summaries of its runs
    among `summaries` that are of the ramp's repetition `repetition`: each repetition of a ramp is
    judged apart (jms_kapps 114, interim). A run whose record names no repetition is of the
    first."""
    return [
        Step(
            float(level.values["boxes_per_minute"]),
            level.number,
            [
                summary
                for summary in summaries
                if summary.key == level.key and repetition_of(summary) == repetition
            ],
        )
        for level in levels
    ]


def ramp_judgements(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    *,
    repetition: int = 1,
) -> dict[int, Judgement]:
    """How far repetition `repetition` of each ramp among the configurations `found` went, by the
    number of its block, from the runs `summaries`."""
    return {
        block: judge(steps_of(levels, summaries, repetition=repetition))
        for block, levels in ramps_of(found).items()
    }


def ramp_repetitions(
    levels: Sequence[SpecConfiguration], summaries: Sequence[RunSummary]
) -> list[int]:
    """The repetitions of the ramp of `levels` that have a run among `summaries`, in rising order:
    1 alone where none has (jms_kapps 114, interim). A run that a stop signal ended is none."""
    made = repetitions_made(
        summaries, run_kind=RUN_KIND, keys={level.key for level in levels}
    )
    return sorted(made) or [1]


def _rate(boxes_per_minute: float | None) -> str:
    return f"{boxes_per_minute:g} boxes per minute"
