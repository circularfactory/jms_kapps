"""The replay levels of the store-only baseline: a recorded run replayed compressed in time, one
factor at a time (jms_kapps 98, decided by Sören on 2026-10-12, as the product owner of the tests,
jms_kapps 85; interim names, jms_kapps 83). Pure functions: no store, no clock.

**A level** is one replay of one recorded run at one **factor**: the same requests in the same
order, with every offset from the window's start, and so every gap between two of them, divided by
the factor, and the window as short. A factor of 1 is the recorded timeline, the replay as it has
always been. The factors are a setting of a sweep entry of a spec (`spec`: `replay_factors`),
outside the configuration's key. A level has a key of its own, the replayed configuration's key
and the factor (`level_key`).

**The rates, both per minute.** The *offered rate* is the number of the replayed window's requests
divided by the window's length in minutes over the factor. The *achieved rate* is the number of
answered requests, those the store gave an answer, a refusal too and a request that timed out or
whose connection dropped not, divided by the
level's *replay span* in minutes, but **the replay span is at least the window**: the larger of
the span and the window's length over the factor (jms_kapps 107, decided by Sören on 2026-10-13 as
the product owner of the tests, jms_kapps 85, note 1866296, the changed reading "The replay span is
read literally"). The span runs from the level's window start to its last answer, the drain
included, and the record keeps it as it is (`replay_span_seconds`). So the achieved rate is never
more than the offered rate, and 100 % means that the store kept up: where every request is
answered and the last answer comes before the window's end, the achieved rate is the offered rate.
A level whose last answer comes after the window's end has the rate it had before. (Until
2026-10-14 the span was read literally, so a fast last answer before the window's end gave an
achieved rate above the offered rate, such as 103 % for a last answer at 290 s of 300.)

**Broken** (`signals`): a level is broken when the store refuses a request for want of heap, when
more than 1 % of the window's requests are refused or unanswered, timed out or with their connection
dropped (the share of the ramp's broken rule, `ramp`; the signal was named `refused or timed out`
until 2026-10-14, jms_kapps 109, for the same sum), or when the achieved rate is below 90 % of the
offered rate. A replay has no
commits, so the ramp's commit-latency signal and its plateau do not apply. The thresholds are
judgement settings outside the configuration's key. The sweep stops the replay of a configuration
at its first broken level (`sweep`). The level's record names the signals that broke it.

**Heavy.** Every factor above 1 is a heavy run, and runs only under an approval that covers the
spec and the replayed configuration (`approval`). A factor of 1 is a replay at normal load.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from jms_kapps.evaluation.ramp import (
    HEAP_REFUSAL,
    REFUSED_OR_UNANSWERED,
    REFUSED_OR_UNANSWERED_SHARE,
)
from jms_kapps.evaluation.replay import Request, Sent

# The one signal of the broken rule that only a replay has.
ACHIEVED_BELOW_OFFERED = "achieved rate below offered"

# Its bound: an achieved rate below 90 % of the offered rate.
ACHIEVED_SHARE_OF_OFFERED = 0.9

# The key of a record that holds a level's numbers (`level_record`).
RECORD_KEY = "replay_level"


def is_heavy(factor: float) -> bool:
    """Whether a replay at `factor` is a heavy run: every factor above 1."""
    return factor > 1


def why_heavy(configuration_heavy: bool, factor: float) -> str:
    """What makes a replay heavy, for a refusal to name (`approval.approved`): the replayed
    configuration, or the factor."""
    if configuration_heavy:
        return "heavy = true"
    return f"a replay compressed by the factor {factor:g}"


def level_key(configuration_key: str, factor: float) -> str:
    """The key of the level that replays the configuration of `configuration_key` at `factor`:
    that key and the factor, such as `3f9a1c2b7d4e5f60@x4`."""
    return f"{configuration_key}@x{factor:g}"


def compressed(requests: Sequence[Request], factor: float) -> list[Request]:
    """`requests` with each offset from the window's start divided by `factor`, in the same order:
    the gaps between them are divided by it too. A request without an offset keeps none."""
    return [
        request
        if request.offset_seconds is None
        else replace(request, offset_seconds=request.offset_seconds / factor)
        for request in requests
    ]


def answered_at(sent: Sent) -> datetime | None:
    """When the store answered `sent`, or None for a request without an answer."""
    if sent.status_code is None:
        return None
    return sent.at + timedelta(seconds=sent.seconds)


def signals(level: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each signal of the broken rule that the numbers of `level` (`level_record`) show."""
    found: list[dict[str, Any]] = []
    if level["heap_refusals_in_window"] > 0:
        found.append(
            {
                "signal": HEAP_REFUSAL,
                "heap_refusals": level["heap_refusals_in_window"],
            }
        )
    requests = level["requests_in_window"]
    if requests:
        share = (
            level["refused_in_window"]
            + level["timed_out_in_window"]
            + level.get("connection_dropped_in_window", 0)
        ) / requests
        if share > REFUSED_OR_UNANSWERED_SHARE:
            found.append({"signal": REFUSED_OR_UNANSWERED, "share": share})
    offered, achieved = level["offered_per_minute"], level["achieved_per_minute"]
    if (
        offered is not None
        and achieved is not None
        and achieved < ACHIEVED_SHARE_OF_OFFERED * offered
    ):
        found.append(
            {
                "signal": ACHIEVED_BELOW_OFFERED,
                "achieved_per_minute": achieved,
                "offered_per_minute": offered,
                "share": achieved / offered,
            }
        )
    return found


def level_record(
    *,
    factor: float,
    recorded_window_seconds: float,
    window_start: datetime,
    window: Sequence[Sent],
    refused: int,
    timed_out: int,
    heap_refusals: int,
    connection_dropped: int = 0,
) -> dict[str, Any]:
    """The record of one replay level: its factor, its offered and achieved rates per minute (the
    answered requests over the larger of the replay span and the window, jms_kapps 107), the
    refusals, the timeouts and the dropped connections in the window (jms_kapps 109), the
    refusals for want of heap, the replay span, and whether it is broken, with the signals that
    broke it.

    Args:
        factor: The replay's factor.
        recorded_window_seconds: How long the recorded run's window lasted.
        window_start: When the replay's window started, by its clock.
        window: Every request the replay sent in the window, with its answer or its error.
        refused: How many of them the store refused.
        timed_out: How many got no answer because the time ran out.
        heap_refusals: How many the store refused for want of heap.
        connection_dropped: How many got no answer because their connection dropped.
    """
    count = len(window)
    minutes = recorded_window_seconds / factor / 60
    offered = count / minutes if minutes > 0 else None
    answers = [
        moment
        for moment in (answered_at(sent) for sent in window)
        if moment is not None
    ]
    span = (max(answers) - window_start).total_seconds() if answers else None
    # The replay span is at least the window (jms_kapps 107); the record keeps the literal span.
    over = None if span is None else max(span, minutes * 60)
    if not count:
        achieved: float | None = None
    elif not answers:
        achieved = 0.0
    elif over is not None and over > 0:
        achieved = len(answers) / (over / 60)
    else:
        achieved = None
    level = {
        "factor": factor,
        "offered_per_minute": offered,
        "achieved_per_minute": achieved,
        "requests_in_window": count,
        "answered_in_window": len(answers),
        "refused_in_window": refused,
        "timed_out_in_window": timed_out,
        "connection_dropped_in_window": connection_dropped,
        "heap_refusals_in_window": heap_refusals,
        "replay_span_seconds": span,
    }
    found = signals(level)
    return level | {
        "broken": bool(found),
        "signals": found,
        "thresholds": {
            "refused_or_unanswered_share": REFUSED_OR_UNANSWERED_SHARE,
            "achieved_share_of_offered": ACHIEVED_SHARE_OF_OFFERED,
        },
    }


def signal_names(level: Mapping[str, Any]) -> str:
    """The signals that broke `level`, named for a person to read."""
    return ", ".join(str(signal["signal"]) for signal in level.get("signals") or [])
