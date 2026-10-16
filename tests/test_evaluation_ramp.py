"""The broken rule of the measurement harness and how far a ramp goes (jms_kapps 49, the numbers
decision of jms_kapps 56): a load level is broken at the first of four store-side signals, and
stuck or failed boxes alone never break it. Judged on fabricated records with known numbers; no
store, no run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from _evaluation_records import record, request

from jms_kapps.evaluation.ramp import (
    ACCEPTED_SHARE,
    CANNOT_TELL,
    HEAP_REFUSAL,
    P95_COMMIT_LATENCY,
    PLATEAU,
    REFUSED_OR_UNANSWERED,
    UC2_LIMITED,
    Step,
    judge,
    level_numbers,
    signals,
)
from jms_kapps.evaluation.summary import summary_of

RATES = (10.0, 20.0, 40.0, 80.0, 160.0)


def healthy(run_id: str, rate: float, **changes: Any) -> dict[str, Any]:
    """A record of a level that shows no signal: no heap refusal, nothing refused, a commit
    latency of about 0.1 s, and completed writes that double with the rate."""
    numbers: dict[str, Any] = {
        "commit_latencies": [0.08, 0.1, 0.12] * 10,
        "requests": [request(0.01)] * 200,
        "completed_writes_per_minute": 10 * rate,
        "transports_ended_per_minute": rate,
    }
    return record(run_id, **(numbers | changes))


def ramp(*levels: dict[str, Any] | None) -> list[Step]:
    """The steps of a ramp at `RATES`, each with its record, or none where it has not run."""
    return [
        Step(rate, number, [] if found is None else [summary_of(found)])
        for number, (rate, found) in enumerate(zip(RATES, levels, strict=False), 1)
    ]


def numbers_of(found: dict[str, Any]):
    return level_numbers([summary_of(found)])


def named(found: Sequence[dict[str, Any]]) -> list[str]:
    return [signal["signal"] for signal in found]


def test_a_healthy_level_shows_no_signal():
    lowest = numbers_of(healthy("a", 10))

    assert signals(numbers_of(healthy("b", 20)), lowest=lowest, before=lowest) == []


def test_a_refusal_for_want_of_heap_alone_breaks_a_level():
    lowest = numbers_of(healthy("a", 10))
    level = numbers_of(healthy("b", 20, heap_refusals_in_window=1))

    assert named(signals(level, lowest=lowest, before=lowest)) == [HEAP_REFUSAL]


@pytest.mark.parametrize(
    ("refused", "timed_out", "broken"),
    [(2, 0, False), (3, 0, True), (0, 3, True), (1, 2, True), (1, 1, False)],
)
def test_more_than_one_percent_refused_or_unanswered_alone_breaks_a_level(
    refused, timed_out, broken
):
    """Of 200 store requests in the window, 2 is 1 %, which does not break; 3 does."""
    lowest = numbers_of(healthy("a", 10))
    level = numbers_of(
        healthy("b", 20, refused_in_window=refused, timed_out_in_window=timed_out)
    )

    found = named(signals(level, lowest=lowest, before=lowest))

    assert found == ([REFUSED_OR_UNANSWERED] if broken else [])


@pytest.mark.parametrize(("latency", "broken"), [(1.0, False), (1.3, True)])
def test_a_p95_commit_latency_over_ten_times_the_lowest_rate_s_alone_breaks_a_level(
    latency, broken
):
    """The lowest rate's p95 commit latency is 0.12 s: 1.0 s is under ten times that, 1.3 s
    over it."""
    lowest = numbers_of(healthy("a", 10))
    level = numbers_of(healthy("b", 20, commit_latencies=[0.1] * 10 + [latency] * 10))

    found = named(signals(level, lowest=lowest, before=lowest))

    assert lowest.p95_commit_latency == pytest.approx(0.12)
    assert found == ([P95_COMMIT_LATENCY] if broken else [])


@pytest.mark.parametrize(
    ("writes", "broken"), [(104.0, True), (105.0, False), (60.0, True)]
)
def test_a_plateau_of_the_completed_writes_is_still_shown_as_a_signal(writes, broken):
    """The step before completed 100 writes a minute: 104 is a rise of less than 5 %, 105 is
    5 %. Since jms_kapps 99 a plateau breaks no level (see below), but the record shows it."""
    before = numbers_of(healthy("a", 10, completed_writes_per_minute=100.0))
    level = numbers_of(healthy("b", 20, completed_writes_per_minute=writes))

    found = named(signals(level, lowest=before, before=before))

    assert found == ([PLATEAU] if broken else [])


def test_stuck_boxes_alone_do_not_break_a_level():
    stuck = [
        {"transport": f"Transport{n}", "status": "running", "place": "Module3"}
        for n in range(9)
    ]
    lowest = numbers_of(healthy("a", 10))

    assert (
        signals(numbers_of(healthy("b", 20, stuck=stuck)), lowest=lowest, before=lowest)
        == []
    )
    judged = judge(ramp(healthy("a", 10), healthy("b", 20, stuck=stuck)))
    assert not judged.ended


def test_failed_boxes_and_refused_spawns_alone_do_not_break_a_level():
    failed = [
        {"transport": "Transport4", "place": "Module2", "reason": "no live route on"}
    ]
    refused = [{"reason": "no free origin", "origin": None, "destination": None}] * 5

    judged = judge(
        ramp(
            healthy("a", 10),
            healthy("b", 20, failed=failed, refused_spawns=refused),
        )
    )

    assert [level["broken"] for level in judged.levels] == [False, False]
    assert not judged.ended


def test_a_ramp_stops_at_its_first_broken_level_and_its_scale_is_the_level_before():
    judged = judge(
        ramp(
            healthy("a", 10),
            healthy("b", 20),
            healthy("c", 40, heap_refusals_in_window=2),
            healthy("d", 80, heap_refusals_in_window=5),
        )
    )

    assert judged.ended
    assert judged.broken_at == 40
    assert judged.scale == 20
    assert [level["boxes_per_minute"] for level in judged.levels] == [10, 20, 40]
    assert named(judged.levels[-1]["signals"]) == [HEAP_REFUSAL]


def test_a_plateau_alone_no_longer_breaks_a_level_or_stops_a_ramp():
    """jms_kapps 99, Sören's decision of 2026-10-12: the old rule stopped the ramp here. Now the
    ramp goes on to its top level, and the plateau stays in the record of the level."""
    judged = judge(
        ramp(
            healthy("a", 10),
            healthy("b", 20, completed_writes_per_minute=101.0),
            healthy("c", 40, completed_writes_per_minute=102.0),
            healthy("d", 80, completed_writes_per_minute=103.0),
            healthy("e", 160, completed_writes_per_minute=104.0),
        )
    )

    assert not judged.ended
    assert judged.broken_at is None
    assert [level["boxes_per_minute"] for level in judged.levels] == list(RATES)
    assert [level["broken"] for level in judged.levels] == [False] * 5
    assert named(judged.levels[1]["signals"]) == [PLATEAU]


def test_a_ramp_stops_at_a_heap_refusal_after_a_plateau():
    judged = judge(
        ramp(
            healthy("a", 10),
            healthy("b", 20, completed_writes_per_minute=101.0),
            healthy(
                "c", 40, completed_writes_per_minute=102.0, heap_refusals_in_window=1
            ),
            healthy("d", 80),
        )
    )

    assert judged.ended
    assert judged.broken_at == 40
    assert judged.scale == 20
    assert len(judged.levels) == 3
    assert named(judged.levels[-1]["signals"]) == [HEAP_REFUSAL, PLATEAU]
    assert judged.levels[-1]["broken"] is True


def test_a_ramp_broken_at_its_lowest_rate_has_no_scale():
    judged = judge(ramp(healthy("a", 10, timed_out_in_window=50), healthy("b", 20)))

    assert judged.broken_at == 10
    assert judged.scale is None


def test_a_ramp_that_has_not_reached_a_broken_level_has_not_ended():
    judged = judge(ramp(healthy("a", 10), healthy("b", 20), None, None, None))

    assert not judged.ended
    assert judged.scale is None
    assert len(judged.levels) == 2


def test_a_ramp_none_of_whose_levels_broke_has_no_scale_yet():
    judged = judge(ramp(*(healthy(str(rate), rate) for rate in RATES)))

    assert not judged.ended
    assert judged.scale is None
    assert "160" in judged.why


def test_a_level_with_a_run_but_none_that_counts_stops_the_ramp_without_a_scale():
    """It cannot be judged, so no higher rate goes to the store."""
    invalid = healthy(
        "b",
        20,
        valid=False,
        failed_checks=[{"check": "deadlock alarm", "module": "Module2"}],
    )

    judged = judge(ramp(healthy("a", 10), invalid, healthy("c", 40)))

    assert judged.ended
    assert judged.stopped_at == 20
    assert judged.broken_at is None
    assert judged.scale is None
    assert "deadlock alarm" in judged.levels[-1]["why"]


def test_a_level_whose_only_run_was_interrupted_has_not_run_yet():
    judged = judge(ramp(healthy("a", 10), record("b", outcome="interrupted")))

    assert not judged.ended
    assert len(judged.levels) == 1


def test_the_p95_of_a_level_is_compared_with_the_lowest_rate_not_the_step_before():
    """Each step's p95 is under ten times the step before it, but the fourth is over ten times
    the lowest rate's."""
    judged = judge(
        ramp(
            healthy("a", 10, commit_latencies=[0.1] * 20),
            healthy("b", 20, commit_latencies=[0.3] * 20),
            healthy("c", 40, commit_latencies=[0.9] * 20),
            healthy("d", 80, commit_latencies=[2.0] * 20),
        )
    )

    assert judged.broken_at == 80
    assert named(judged.levels[-1]["signals"]) == [P95_COMMIT_LATENCY]
    assert judged.scale == 40


def test_a_refusal_for_want_of_heap_in_the_drain_breaks_a_level_and_one_before_the_load_not():
    """The load runs from its start to the run's end, the drain included; a refusal at the
    participants' start, before the load, says how large the store is, not how loaded."""
    lowest = numbers_of(healthy("a", 10))

    in_the_drain = numbers_of(healthy("b", 20, heap_refusals_in_the_drain=1))
    before_the_load = numbers_of(healthy("c", 20, heap_refusals_before_the_load=3))

    assert named(signals(in_the_drain, lowest=lowest, before=lowest)) == [HEAP_REFUSAL]
    assert signals(before_the_load, lowest=lowest, before=lowest) == []


def test_a_signal_that_a_level_cannot_show_is_named_as_not_judged():
    """With no commit in the lowest rate's window, the p95 signal cannot fire: the level says
    so."""
    judged = judge(
        ramp(
            healthy("a", 10, commit_latencies=[]),
            healthy("b", 20, requests=[], store_requests_in_window=0),
        )
    )

    [lowest, second] = judged.levels
    assert lowest["not_judged"] == []
    assert second["not_judged"] == [
        "refused or unanswered: no store request in the window",
        "p95 commit latency: no commit in the window at the lowest rate",
    ]


def limited(run_id: str, rate: float, accepted: int = 80, refused: int = 20, **changes):
    """A level of which fewer than 90 % of the offered spawns were accepted (80 of 100)."""
    return healthy(
        run_id, rate, spawns_accepted=accepted, spawns_refused=refused, **changes
    )


def accepted_all(run_id: str, rate: float, **changes):
    return healthy(run_id, rate, spawns_accepted=100, spawns_refused=0, **changes)


def test_a_level_holds_its_offered_and_accepted_spawns_and_the_accepted_share():
    judged = judge(ramp(accepted_all("a", 10), limited("b", 20, 180, 20)))

    [first, second] = (level["numbers"] for level in judged.levels)
    assert (first["spawns_offered"], first["spawns_accepted"]) == (100, 100)
    assert first["spawns_accepted_share"] == 1.0
    assert (second["spawns_offered"], second["spawns_accepted"]) == (200, 180)
    assert second["spawns_accepted_share"] == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("accepted", "refused", "marked"),
    [(90, 10, False), (89, 11, True), (100, 0, False), (0, 50, True)],
)
def test_a_level_below_the_accepted_share_threshold_is_marked_uc2_limited(
    accepted, refused, marked
):
    """Fewer than 90 % accepted marks the level; exactly 90 % does not."""
    assert ACCEPTED_SHARE == 0.9
    judged = judge(
        ramp(healthy("a", 10, spawns_accepted=accepted, spawns_refused=refused))
    )

    assert judged.levels[0]["uc2_limited"] is marked


def test_a_level_of_a_record_with_no_spawn_counts_is_neither_limited_nor_unlimited():
    """Records before jms_kapps 99 hold no counts in the window: the level says it cannot tell."""
    judged = judge(ramp(healthy("a", 10)))

    [level] = judged.levels
    assert level["uc2_limited"] is None
    assert level["numbers"]["spawns_accepted_share"] is None


def test_a_uc2_limited_level_does_not_break_a_level_and_the_ramp_goes_on_past_it():
    judged = judge(
        ramp(
            accepted_all("a", 10),
            accepted_all("b", 20),
            limited("c", 40, completed_writes_per_minute=205.0),
            limited("d", 80, completed_writes_per_minute=206.0),
            limited("e", 160, completed_writes_per_minute=207.0),
        )
    )

    assert not judged.ended
    assert [level["uc2_limited"] for level in judged.levels] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert [level["broken"] for level in judged.levels] == [False] * 5
    assert named(judged.levels[2]["signals"]) == [PLATEAU]
    assert judged.uc2_limited_at == 40


def test_the_judgement_is_uc2_limited_at_the_first_limited_level_when_no_level_broke():
    judged = judge(ramp(accepted_all("a", 10), limited("b", 20), limited("c", 40)))

    assert judged.judgement.kind == UC2_LIMITED
    assert judged.judgement.level == 20
    assert judged.judgement.text == "UC2-limited at 20 boxes per minute"


def test_the_judgement_is_uc2_limited_when_the_first_limited_level_comes_before_the_broken_one():
    judged = judge(
        ramp(
            accepted_all("a", 10),
            limited("b", 20),
            limited("c", 40, heap_refusals_in_window=1),
        )
    )

    assert judged.broken_at == 40
    assert judged.judgement.kind == UC2_LIMITED
    assert judged.judgement.level == 20


def test_the_judgement_is_broken_when_a_level_broke_before_any_limited_one():
    judged = judge(
        ramp(
            accepted_all("a", 10),
            accepted_all("b", 20, refused_in_window=50),
            limited("c", 40),
        )
    )

    assert judged.ended
    assert judged.judgement.kind == "broken"
    assert judged.judgement.level == 20
    assert (
        judged.judgement.text == "broken at 20 boxes per minute (refused or unanswered)"
    )


def test_the_judgement_cannot_tell_when_a_level_is_limited_and_broken_at_once():
    judged = judge(
        ramp(accepted_all("a", 10), limited("b", 20, heap_refusals_in_window=1))
    )

    assert judged.judgement.kind == CANNOT_TELL
    assert judged.judgement.text == "cannot tell"


def test_the_judgement_cannot_tell_when_only_a_plateau_or_the_top_level_came():
    plateau = judge(
        ramp(
            accepted_all("a", 10),
            accepted_all("b", 20, completed_writes_per_minute=101.0),
        )
    )
    top = judge(ramp(*(accepted_all(str(rate), rate) for rate in RATES)))

    assert plateau.judgement.kind == top.judgement.kind == CANNOT_TELL
    assert named(plateau.levels[1]["signals"]) == [PLATEAU]
