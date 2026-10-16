"""The planner's memory: one entry per Screw that was pressed, and what became of it.

These tests build no middleware and no store. The memory is told what a press told it, and the
state it answers is what the dashboard box renders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_memory import (
    DISPATCHED,
    DONE,
    FAILED,
    REFUSED,
    TIMED_OUT,
    WAITING,
    PlannerMemory,
)
from jms_kapps.uc1.vocabulary import JMS_NS

SCREW_1 = IRI("Screw1", base=JMS_NS)
SCREW_2 = IRI("Screw2", base=JMS_NS)
OPERATION_1 = IRI("UnscrewingOperation1", base=JMS_NS)
OPERATION_2 = IRI("UnscrewingOperation2", base=JMS_NS)

START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def ticking() -> PlannerMemory:
    """A memory whose clock moves one second at every call, so an `at` can be compared."""
    moments = iter(START + timedelta(seconds=n) for n in range(1000))
    return PlannerMemory(clock=lambda: next(moments))


def test_a_new_memory_knows_of_no_press() -> None:
    assert PlannerMemory().state().screws == []


def test_a_press_adds_its_screw_waiting() -> None:
    memory = ticking()

    memory.pressed(SCREW_1)

    entry = memory.state().screws[0]
    assert (entry.screw, entry.status) == (SCREW_1, WAITING)
    assert entry.operation is None and entry.outcome is None
    assert entry.at == START


def test_the_entries_keep_the_order_of_the_first_press() -> None:
    memory = ticking()

    memory.pressed(SCREW_2)
    memory.pressed(SCREW_1)
    memory.pressed(SCREW_2)

    assert [entry.screw for entry in memory.state().screws] == [SCREW_2, SCREW_1]


def test_a_dispatched_screw_names_its_operation() -> None:
    memory = ticking()
    memory.pressed(SCREW_1)

    memory.dispatched(SCREW_1, OPERATION_1)

    entry = memory.state().screws[0]
    assert (entry.status, entry.operation) == (DISPATCHED, OPERATION_1)


def test_an_operation_that_ended_done_shows_its_outcome() -> None:
    memory = ticking()
    memory.pressed(SCREW_1)
    memory.dispatched(SCREW_1, OPERATION_1)

    memory.ended(SCREW_1, DONE, outcome="ScrewIntact")

    entry = memory.state().screws[0]
    assert (entry.status, entry.outcome) == (DONE, "ScrewIntact")
    assert entry.operation == OPERATION_1


def test_a_failed_operation_keeps_its_entry_with_the_reason() -> None:
    memory = ticking()
    memory.pressed(SCREW_1)
    memory.dispatched(SCREW_1, OPERATION_1)

    memory.ended(SCREW_1, FAILED, why="the detector could not reach the curves")

    entry = memory.state().screws[0]
    assert entry.status == FAILED
    assert entry.why == "the detector could not reach the curves"


def test_a_dispatch_that_finds_no_resource_is_refused_with_its_reason() -> None:
    memory = ticking()

    memory.refused(SCREW_1, why="no live service realizes the capability")

    entry = memory.state().screws[0]
    assert entry.status == REFUSED
    assert entry.operation is None
    assert entry.why == "no live service realizes the capability"


def test_a_wait_that_passed_its_timeout_is_recorded_as_timed_out() -> None:
    memory = ticking()
    memory.pressed(SCREW_1)
    memory.dispatched(SCREW_1, OPERATION_1)

    memory.timed_out(SCREW_1, why="no end within 300.0s")

    entry = memory.state().screws[0]
    assert entry.status == TIMED_OUT
    assert entry.operation == OPERATION_1, "the operation stays in the graph"


def test_a_screw_that_was_never_pressed_gains_its_entry() -> None:
    """A refusal is recorded before anything else was: the entry is created by what it is told."""
    memory = ticking()

    memory.refused(SCREW_1, why="nobody is live")

    assert [entry.screw for entry in memory.state().screws] == [SCREW_1]


def test_a_second_press_of_one_screw_starts_its_entry_over() -> None:
    """The entry says what became of that Screw last, not what became of it the first time."""
    memory = ticking()
    memory.pressed(SCREW_1)
    memory.dispatched(SCREW_1, OPERATION_1)
    memory.ended(SCREW_1, DONE, outcome="ScrewIntact")

    memory.pressed(SCREW_1)

    entry = memory.state().screws[0]
    assert (entry.status, entry.operation, entry.outcome) == (WAITING, None, None)
    assert len(memory.state().screws) == 1

    memory.dispatched(SCREW_1, OPERATION_2)
    assert memory.state().screws[0].operation == OPERATION_2


def test_the_state_is_a_snapshot_that_a_later_change_does_not_alter() -> None:
    """A render that already took the state keeps what it read."""
    memory = ticking()
    memory.pressed(SCREW_1)
    taken = memory.state()

    memory.ended(SCREW_1, DONE, outcome="ScrewIntact")

    assert taken.screws[0].status == WAITING
    assert memory.state().screws[0].status == DONE
