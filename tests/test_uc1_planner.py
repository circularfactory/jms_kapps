"""The planner's press: one unscrewing operation for one Screw, and the wait for its end.

These tests need no store and no middleware. `mw.issue_operation()` is faked,
because what the planner does with them is the thing under test: it names the Screw and the
workpiece on the draft, it writes the Screw's back-link in the same commit, it records what the
call returns, and it records a refusal when no service realizes the capability.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from kapps_semantic_middleware.errors import OperationResolutionError
from kapps_triplestore_interface import IRI, ShaclValidationError

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

# A report as the store sent it when it refused an unscrewing operation whose Screw's Screw type
# holds none of the seven values.
REPORT = (Path(__file__).parent / "reports" / "infeasible-operation.nq").read_text(
    encoding="utf-8"
)


class Draft:
    """The draft `mw.issue_operation()` yields in its block: `data` takes the Operation's own
    properties, and `iri` is readable inside the block."""

    def __init__(self, iri: IRI) -> None:
        self.data: dict[str, object] = {}
        self.iri = iri
        self.updates: list[tuple[str, dict[str, object]]] = []

    def update(self, instance_iri: str, data: dict[str, object]) -> None:
        self.updates.append((instance_iri, data))


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
    """A middleware whose `issue_operation()` yields a handle and commits what the block staged.

    Each write transaction is one entry of `commits`: the operation's own data and the updates of
    existing instances, together; a `commit()` of its own is another entry.
    A block that raises, or a refused commit, adds none.

    `refuse` makes the issuing call raise `OperationResolutionError` before the block runs, as the
    real one does when no live service realizes the capability; nothing is written then.
    `commit_raises` makes the issuing commit fail, as a refused write would.
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
        self.commits: list[tuple[dict[str, object], list[object]]] = []

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
        # The block ended cleanly: the Operation and the staged updates commit as one.
        if self._commit_raises is not None:
            raise self._commit_raises
        self.commits.append((dict(self._handle.data), list(self._handle.updates)))

    @contextmanager
    def commit(self, *instances, entry=(), **rest):  # type: ignore[no-untyped-def]
        """A commit of its own: the planner makes none, so a press that did would count twice."""
        self.commits.append(({}, list(entry)))
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
        str(JMS.concernsScrew): [{"id": str(SCREW)}],
        str(JMS.concernsWorkpiece): [{"id": str(PRODUCT)}],
    }


def test_one_press_is_one_write_transaction_carrying_the_back_link() -> None:
    """`:hasOperation` belongs to the Screw, which already exists: the planner writes it with the
    issuing call's update, so the operation and the back-link are the one commit, and there is no
    second one."""
    middleware = FakeMiddleware()

    dispatch(middleware, SCREW, PRODUCT, PlannerMemory())

    assert len(middleware.commits) == 1
    data, updates = middleware.commits[0]
    assert data[str(JMS.concernsScrew)] == [{"id": str(SCREW)}]
    assert updates == [(str(SCREW), {str(JMS.hasOperation): [OPERATION]})]


def test_a_refused_issuing_commit_leaves_neither_the_operation_nor_the_back_link() -> (
    None
):
    """The back-link is staged in the block, so the store refuses it with the operation: the press
    raises, nothing is committed, and the planner records no operation for the Screw."""
    middleware = FakeMiddleware(
        commit_raises=RuntimeError("the store refused the write")
    )
    memory = PlannerMemory()

    with pytest.raises(RuntimeError, match="refused"):
        dispatch(middleware, SCREW, PRODUCT, memory)

    assert middleware.commits == []
    assert memory.state().screws == []


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


def test_a_commit_the_store_refuses_is_recorded_as_refused_with_its_rules_and_returns_nothing() -> (
    None
):
    """The store's shapes refuse the operation: nothing was written, the Screw has no operation,
    and the entry carries the rules the report names, their messages and the report itself, so
    the dashboard and the record route can show why."""
    refusal = ShaclValidationError("the store refused the write", report=REPORT)
    middleware = FakeMiddleware(commit_raises=refusal)
    memory = PlannerMemory()

    operation = dispatch(middleware, SCREW, PRODUCT, memory)

    assert operation is None
    assert middleware.commits == []
    entry = memory.state().screws[0]
    assert entry.status == REFUSED
    assert entry.operation is None
    assert entry.rules == (
        "UnscrewingOperationScrewShape at UnscrewingOperation_8542a7d053ca",
    )
    assert len(entry.messages) == 1
    assert entry.messages[0].startswith(
        "An unscrewing operation concerns exactly one Screw"
    )
    assert entry.report == REPORT
    assert entry.why is not None and "refused" in entry.why


def test_a_refusal_for_want_of_a_service_carries_no_rules() -> None:
    memory = PlannerMemory()

    dispatch(FakeMiddleware(refuse=True), SCREW, PRODUCT, memory)

    entry = memory.state().screws[0]
    assert (entry.rules, entry.messages, entry.report) == ((), (), None)


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


def test_a_failed_operation_records_its_failure_outcome_and_the_reason() -> None:
    """The planner reads the outcome on every end: a failure outcome ends the operation failed,
    and the outcome tells which."""
    memory = PlannerMemory()
    asked: list[IRI] = []

    def read_outcome(operation: IRI) -> str:
        asked.append(operation)
        return "MissingScrew"

    unscrew_one(
        FakeMiddleware(handle=Handle(OPERATION, status=FAILED)),
        SCREW,
        PRODUCT,
        memory,
        read_outcome=read_outcome,
        read_why=lambda operation: "ANOMALY: MissingScrew",
    )

    assert asked == [OPERATION]
    entry = memory.state().screws[0]
    assert (entry.status, entry.outcome, entry.why) == (
        FAILED,
        "MissingScrew",
        "ANOMALY: MissingScrew",
    )


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
