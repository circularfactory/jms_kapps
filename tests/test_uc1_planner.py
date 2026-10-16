"""The planner's press: one unscrewing operation for one Screw, and the wait for its end.

These tests need no store and no middleware. `mw.issue_operation()` and `mw.commit()` are faked,
because what the planner does with them is the thing under test: it names the Screw and the
workpiece on the draft, it writes the Screw's back-link in a second commit, it records what the
call returns, and it records a refusal when no service realizes the capability.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from kapps_semantic_middleware.errors import OperationResolutionError
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner import dispatch, unscrew_one
from jms_kapps.uc1.planner_memory import (
    DISPATCHED,
    DONE,
    FAILED,
    REFUSED,
    TIMED_OUT,
    PlannerMemory,
)
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

PRODUCT = IRI("AngleGrinder1", base=JMS_NS)
SCREW = IRI("Screw1", base=JMS_NS)
OPERATION = IRI("UnscrewingOperation1", base=JMS_NS)


class Draft:
    """The draft `mw.issue_operation()` yields in its block: `data` takes the Operation's own
    properties, and `iri` is readable inside the block."""

    def __init__(self, iri: IRI) -> None:
        self.data: dict[str, object] = {}
        self.iri = iri


class Handle(Draft):
    """A draft whose wait returns `status`, or raises what it was given."""

    def __init__(
        self,
        iri: IRI,
        status: str = DONE,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__(iri)
        self._status = status
        self._raises = raises
        self.waits: list[float | None] = []

    def wait_ended(self, timeout: float | None = None) -> str:
        self.waits.append(timeout)
        if self._raises is not None:
            raise self._raises
        return self._status


class FakeMiddleware:
    """A middleware whose `issue_operation()` yields a handle and whose `commit()` records the
    changes it was given.

    `refuse` makes the issuing call raise `OperationResolutionError` before the block runs, as the
    real one does when no live service realizes the capability; nothing is written then.
    `commit_raises` makes the back-link's commit fail, as a refused write would.
    """

    def __init__(
        self,
        *,
        handle: Handle | None = None,
        refuse: bool = False,
        commit_raises: BaseException | None = None,
    ) -> None:
        self._handle = handle or Handle(OPERATION)
        self._refuse = refuse
        self._commit_raises = commit_raises
        self.calls: list[dict[str, object]] = []
        self.drafts: list[Draft] = []
        self.commits: list[list[object]] = []

    @contextmanager
    def issue_operation(self, *, capability_class, operation_class, **rest):  # type: ignore[no-untyped-def]
        self.calls.append(
            dict(
                capability_class=capability_class,
                operation_class=operation_class,
                **rest,
            )
        )
        if self._refuse:
            raise OperationResolutionError("no live service realizes the capability")
        self.drafts.append(self._handle)
        yield self._handle

    @contextmanager
    def commit(self, *instances, entry=(), **rest):  # type: ignore[no-untyped-def]
        if self._commit_raises is not None:
            raise self._commit_raises
        self.commits.append(list(entry))
        yield None


# --- The dispatch -----------------------------------------------------------------------------


def test_the_dispatch_asks_for_the_screwing_resource_s_capability() -> None:
    """Not the planner's own: the planner opens the work, the screwing resource performs it."""
    middleware = FakeMiddleware()

    dispatch(middleware, SCREW, PRODUCT, PlannerMemory())

    assert middleware.calls == [
        {
            "capability_class": JMS.UnscrewingCapability,
            "operation_class": JMS.UnscrewingOperation,
        }
    ]


def test_the_dispatch_names_the_screw_and_the_workpiece_on_the_operation() -> None:
    """Both are the operation's own properties, so both go in the one commit the issuing call
    makes."""
    middleware = FakeMiddleware()

    dispatch(middleware, SCREW, PRODUCT, PlannerMemory())

    assert middleware.drafts[0].data == {
        str(JMS.hasScrew): [{"id": str(SCREW)}],
        str(JMS.hasWorkpiece): [{"id": str(PRODUCT)}],
    }


def test_the_dispatch_writes_the_screw_s_back_link_after_the_operation() -> None:
    """`:hasOperation` belongs to the Screw, which already exists, and the issuing call joins only
    creations nested in its block. So it is a second commit, and it names the operation that the
    first one wrote."""
    middleware = FakeMiddleware()

    dispatch(middleware, SCREW, PRODUCT, PlannerMemory())

    assert len(middleware.commits) == 1
    change = middleware.commits[0][0]
    assert change.instance_iri == SCREW
    assert change.data == {str(JMS.hasOperation): [{"id": str(OPERATION)}]}


def test_a_refused_back_link_does_not_raise_and_keeps_the_operation() -> None:
    """The operation is already in the graph and the screwing resource has it, so a press that
    cannot write the back-link goes on rather than raising."""
    middleware = FakeMiddleware(
        commit_raises=RuntimeError("the store refused the write")
    )
    memory = PlannerMemory()

    operation = dispatch(middleware, SCREW, PRODUCT, memory)

    assert operation is not None
    assert memory.state().screws[0].status == DISPATCHED


def test_the_dispatch_returns_the_operation_and_records_it() -> None:
    memory = PlannerMemory()

    operation = dispatch(FakeMiddleware(), SCREW, PRODUCT, memory)

    assert operation is not None and operation.iri == OPERATION
    entry = memory.state().screws[0]
    assert (entry.screw, entry.status, entry.operation) == (
        SCREW,
        DISPATCHED,
        OPERATION,
    )


def test_a_dispatch_that_finds_no_service_is_recorded_as_refused_and_returns_nothing() -> (
    None
):
    """Nothing was written, so the Screw has no operation, and the press must not raise."""
    memory = PlannerMemory()

    operation = dispatch(FakeMiddleware(refuse=True), SCREW, PRODUCT, memory)

    assert operation is None
    entry = memory.state().screws[0]
    assert entry.status == REFUSED
    assert entry.operation is None
    assert entry.why is not None


def test_an_error_that_is_not_a_refusal_is_not_swallowed() -> None:
    """A fault of this press belongs to the caller."""

    class Breaking(FakeMiddleware):
        @contextmanager
        def issue_operation(self, **rest):  # type: ignore[no-untyped-def]
            raise RuntimeError("the store is gone")
            yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="the store is gone"):
        dispatch(Breaking(), SCREW, PRODUCT, PlannerMemory())


# --- One press, end to end --------------------------------------------------------------------


def test_a_press_records_the_screw_before_it_dispatches() -> None:
    """So the page shows the Screw as waiting from the moment of the press."""
    seen: list[str] = []
    memory = PlannerMemory()

    class Watching(FakeMiddleware):
        @contextmanager
        def issue_operation(self, **rest):  # type: ignore[no-untyped-def]
            seen.append(memory.state().screws[0].status)
            self.drafts.append(self._handle)
            yield self._handle

    unscrew_one(Watching(), SCREW, PRODUCT, memory)

    assert seen == ["waiting"]


def test_a_press_that_ends_done_records_the_outcome_that_was_read_for_it() -> None:
    memory = PlannerMemory()
    asked: list[IRI] = []

    def read_outcome(operation: IRI) -> str:
        asked.append(operation)
        return "ScrewIntact"

    unscrew_one(FakeMiddleware(), SCREW, PRODUCT, memory, read_outcome=read_outcome)

    assert asked == [OPERATION]
    entry = memory.state().screws[0]
    assert (entry.status, entry.outcome) == (DONE, "ScrewIntact")


def test_a_press_without_a_reader_records_no_outcome() -> None:
    memory = PlannerMemory()

    unscrew_one(FakeMiddleware(), SCREW, PRODUCT, memory)

    assert memory.state().screws[0].outcome is None


def test_a_failed_operation_is_recorded_and_does_not_raise() -> None:
    memory = PlannerMemory()

    unscrew_one(
        FakeMiddleware(handle=Handle(OPERATION, status=FAILED)), SCREW, PRODUCT, memory
    )

    assert memory.state().screws[0].status == FAILED


def test_a_failed_operation_records_the_reason_that_was_read_for_it() -> None:
    memory = PlannerMemory()
    asked: list[IRI] = []

    def read_why(operation: IRI) -> str:
        asked.append(operation)
        return "NOT_CLASSIFIED: no curves"

    unscrew_one(
        FakeMiddleware(handle=Handle(OPERATION, status=FAILED)),
        SCREW,
        PRODUCT,
        memory,
        read_why=read_why,
    )

    assert asked == [OPERATION]
    entry = memory.state().screws[0]
    assert (entry.status, entry.why) == (FAILED, "NOT_CLASSIFIED: no curves")


def test_a_done_operation_reads_no_reason() -> None:
    memory = PlannerMemory()
    asked: list[IRI] = []

    unscrew_one(FakeMiddleware(), SCREW, PRODUCT, memory, read_why=asked.append)

    assert asked == []
    assert memory.state().screws[0].why is None


def test_a_refused_dispatch_is_recorded_and_does_not_raise() -> None:
    memory = PlannerMemory()

    unscrew_one(FakeMiddleware(refuse=True), SCREW, PRODUCT, memory)

    assert memory.state().screws[0].status == REFUSED


def test_a_wait_that_times_out_is_recorded_and_does_not_raise() -> None:
    """The operation stays in the graph; the planner only stops waiting for it."""
    memory = PlannerMemory()
    handle = Handle(OPERATION, raises=TimeoutError("no end within 300.0s"))

    unscrew_one(FakeMiddleware(handle=handle), SCREW, PRODUCT, memory)

    entry = memory.state().screws[0]
    assert entry.status == TIMED_OUT
    assert entry.why is not None


def test_the_wait_passes_the_timeout_it_was_given() -> None:
    """A lost notification leaves a wait without a limit open forever."""
    handle = Handle(OPERATION)

    unscrew_one(
        FakeMiddleware(handle=handle), SCREW, PRODUCT, PlannerMemory(), timeout=12.5
    )

    assert handle.waits == [12.5]
