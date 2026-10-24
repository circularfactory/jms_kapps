"""What the screwing resource keeps of each attempt in its memory, for its dashboard, offline.

The screwing resource is given stand-ins for the middleware's commit, the time-series store, the
attempt, the call of the detector and the read of an operation, so each test sets how an attempt
goes. `test_uc1_screwing_resource.py` runs the screwing resource on a live GraphDB and InfluxDB.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.classify_call import (
    Called,
    ClassifyAnswer,
    DetectorNotReached,
    NoDetector,
)
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.screwing_resource import (
    NOT_ATTEMPTED,
    NOT_REACHED,
    OPERATION_SCOPE,
    ScrewingResource,
    UnscrewingOperation,
    attempt_given_record,
    not_attempted_record,
    unscrewing_operation_from,
)
from jms_kapps.uc1.screwing_resource_memory import ScrewingMemory
from jms_kapps.uc1.seed import M4_VALUES
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, TYPE_LEVEL

RESOURCE = IRI("TestScrewingResource", base=JMS_NS)
OPERATION = IRI("TestUnscrewingOperation", base=JMS_NS)
SCREW = IRI("TestScrew", base=JMS_NS)
REFERENCE = "influxdb:///Tests/unscrewing?frame=f1"
ENDPOINT = "http://127.0.0.1:9/workflows/classify/execute"
AT = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)
QUEUED = UnscrewingOperation(OPERATION, OperationStatus.QUEUED, (SCREW,), (JMS.M4,))


class FakeCommit:
    """The middleware's commit: it yields a transition that keeps its updates, and runs the
    error transition when the body raises. `refuse` refuses the next commit at its entry."""

    def __init__(self) -> None:
        self.refuse = False
        # The entry transition of each commit, in order.
        self.entries: list = []

    @contextmanager
    def __call__(self, *, entry=(), error=None, exit=None):  # type: ignore[no-untyped-def]
        self.entries.append(list(entry))
        if self.refuse:
            self.refuse = False
            raise RuntimeError("the store refuses the write")
        transition = SimpleNamespace(update=lambda instance, data: None)
        try:
            yield transition
        except Exception as raised:
            if error is not None:
                error(raised)
            raise


class FakeWriter:
    def write(self, frame: Frame) -> str:
        return REFERENCE


def resource(
    *,
    classify: Callable[[IRI], Called],
    attempt: Callable[[IRI, Mapping[IRI, float]], Frame] = lambda screw, values: (
        example_frame("success")
    ),
    status_after: str | None = OperationStatus.RUNNING,
    commit: FakeCommit | None = None,
    read_after: UnscrewingOperation | None = None,
    reads: list[IRI] | None = None,
) -> tuple[ScrewingResource, ScrewingMemory]:
    memory = ScrewingMemory(clock=lambda: AT)

    def read(operation: IRI) -> UnscrewingOperation:
        if reads is not None:
            reads.append(operation)
        if read_after is not None:
            return read_after
        return UnscrewingOperation(operation, status_after, (SCREW,), (JMS.M4,))

    return (
        ScrewingResource(
            RESOURCE,
            commit=commit if commit is not None else FakeCommit(),
            writer=FakeWriter(),  # type: ignore[arg-type]
            attempt=attempt,
            classify=classify,
            read=read,
            memory=memory,
        ),
        memory,
    )


def raises(error: Exception):
    def call(*args):
        raise error

    return call


def the_one(memory: ScrewingMemory):
    state = memory.state()
    (record,) = state.attempts
    assert state.attempt_count == 1
    return record


def test_an_attempt_the_detector_ended_keeps_the_status_and_outcome_it_answered():
    answer = ClassifyAnswer(
        operation=str(OPERATION),
        ended=OperationStatus.DONE,
        outcome=f"{JMS_NS}ScrewIntact",
    )
    screwing, memory = resource(classify=lambda operation: Called(ENDPOINT, answer))

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert (record.at, record.operation, record.screw, record.screw_type) == (
        AT,
        OPERATION,
        SCREW,
        JMS.M4,
    )
    assert (record.reference, record.detector) == (REFERENCE, ENDPOINT)
    assert (record.status, record.outcome, record.why) == (
        OperationStatus.DONE,
        f"{JMS_NS}ScrewIntact",
        None,
    )


def test_the_attempt_is_given_the_type_values_and_the_record_keeps_them(caplog):
    given: list[tuple[IRI, Mapping[IRI, float]]] = []

    def attempt(screw: IRI, values: Mapping[IRI, float]) -> Frame:
        given.append((screw, values))
        return example_frame("success")

    screwing, memory = resource(
        classify=lambda operation: Called(ENDPOINT, None), attempt=attempt
    )
    values_of_m4 = UnscrewingOperation(
        OPERATION, OperationStatus.QUEUED, (SCREW,), (JMS.M4,), M4_VALUES
    )

    with caplog.at_level(logging.INFO, logger="jms_kapps.uc1.screwing_resource"):
        screwing.unscrew(values_of_m4)

    assert given == [(SCREW, M4_VALUES)]
    assert the_one(memory).type_values == M4_VALUES
    assert attempt_given_record(RESOURCE, OPERATION, SCREW, JMS.M4, M4_VALUES) in [
        entry.getMessage() for entry in caplog.records
    ]


def test_the_log_line_of_an_attempt_names_the_values_in_the_order_of_the_ontology():
    line = attempt_given_record(RESOURCE, OPERATION, SCREW, JMS.M4, M4_VALUES)

    assert line == (
        "TestScrewingResource: the attempt of TestUnscrewingOperation on TestScrew is given "
        "the values of M4: MinApproachAxialForce 0.5, MinCounterTorque 0.5, "
        "MinPreloadAxialForce 0.1, PreparationTurningAngle 30, MaxLooseTorque 3, "
        "MinLooseningAxialForce 1, MaxDynamicTorque 1.5"
    )


def test_an_attempt_after_the_screw_type_was_refined_is_given_the_refined_value():
    seen: list[float] = []

    def attempt(screw: IRI, values: Mapping[IRI, float]) -> Frame:
        seen.append(values[JMS.hasMaxDynamicTorque])
        return example_frame("success")

    screwing, _ = resource(
        classify=lambda operation: Called(ENDPOINT, None), attempt=attempt
    )
    refined = {**M4_VALUES, JMS.hasMaxDynamicTorque: 2.25}

    for values in (M4_VALUES, refined):
        screwing.unscrew(
            UnscrewingOperation(
                OPERATION, OperationStatus.QUEUED, (SCREW,), (JMS.M4,), values
            )
        )

    assert seen == [1.5, 2.25]


def test_an_attempt_the_detector_ended_in_an_anomaly_keeps_failed_the_outcome_and_why():
    answer = ClassifyAnswer(
        operation=str(OPERATION),
        ended=OperationStatus.FAILED,
        outcome=f"{JMS_NS}MissingScrew",
        why="ANOMALY: MissingScrew",
    )
    screwing, memory = resource(classify=lambda operation: Called(ENDPOINT, answer))

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert (record.status, record.outcome, record.why) == (
        OperationStatus.FAILED,
        f"{JMS_NS}MissingScrew",
        "ANOMALY: MissingScrew",
    )


def test_an_answer_that_does_not_say_the_status_keeps_none():
    screwing, memory = resource(classify=lambda operation: Called(ENDPOINT, None))

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert (record.detector, record.status, record.why) == (ENDPOINT, None, None)


def test_an_attempt_that_failed_keeps_why_and_calls_no_detector():
    screwing, memory = resource(
        classify=raises(AssertionError("no detector is called")),
        attempt=raises(ValueError("no recording for TestScrew")),
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert (record.reference, record.detector) == (None, None)
    assert record.status == OperationStatus.FAILED
    assert record.why == "the unscrewing attempt failed: no recording for TestScrew"


def test_a_detector_not_reached_keeps_the_endpoint_and_the_failed_it_committed():
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it answered 500", ENDPOINT))
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert (record.reference, record.detector) == (REFERENCE, ENDPOINT)
    assert record.status == OperationStatus.FAILED
    assert record.why == NOT_REACHED + "it answered 500"


def test_no_detector_found_keeps_no_endpoint():
    screwing, memory = resource(
        classify=raises(NoDetector("the knowledge graph names none"))
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.detector is None
    assert record.why == NOT_REACHED + "the knowledge graph names none"


def test_an_operation_that_ended_meanwhile_keeps_the_status_it_ended_with():
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it did not answer", ENDPOINT)),
        status_after=OperationStatus.DONE,
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status == OperationStatus.DONE
    assert record.why is None


def ended_by_the_detector(
    status: str, outcome: str, *results: str
) -> UnscrewingOperation:
    return UnscrewingOperation(
        OPERATION,
        status,
        (SCREW,),
        (JMS.M4,),
        results=results,
        outcome=f"{JMS_NS}{outcome}",
    )


def test_a_call_that_timed_out_on_an_operation_the_detector_failed_records_its_end():
    reads: list[IRI] = []
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it did not answer in time", ENDPOINT)),
        read_after=ended_by_the_detector(
            OperationStatus.FAILED, "MissingScrew", "ANOMALY: MissingScrew"
        ),
        reads=reads,
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status == OperationStatus.FAILED
    assert record.outcome == f"{JMS_NS}MissingScrew"
    assert record.why == "ANOMALY: MissingScrew"
    assert reads == [OPERATION]


def test_a_call_that_timed_out_on_an_operation_the_detector_ended_done_records_no_reason():
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it did not answer in time", ENDPOINT)),
        read_after=ended_by_the_detector(OperationStatus.DONE, "ScrewIntact"),
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status == OperationStatus.DONE
    assert record.outcome == f"{JMS_NS}ScrewIntact"
    assert record.why is None


def test_a_call_that_timed_out_on_a_running_operation_ends_it_failed_with_one_read():
    reads: list[IRI] = []
    commit = FakeCommit()
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it did not answer in time", ENDPOINT)),
        commit=commit,
        reads=reads,
    )

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status == OperationStatus.FAILED
    assert record.outcome is None
    assert record.why == NOT_REACHED + "it did not answer in time"
    assert reads == [OPERATION]
    assert len(commit.entries) == 2


def test_the_operation_read_holds_the_result_and_outcome_the_detector_left():
    chains = [
        [IRI(str(step)) for step in chain]
        for chain in OPERATION_SCOPE.to_property_chains()
    ]
    assert [SVC.hasExecutionResult] in chains
    assert [JMS.hasDetectionResult] in chains
    node = SimpleNamespace(
        id=str(OPERATION),
        data={
            SVC.hasOperationStatus: [OperationStatus.FAILED],
            SVC.hasExecutionResult: ["ANOMALY: MissingScrew"],
            JMS.hasDetectionResult: [SimpleNamespace(id=f"{JMS_NS}MissingScrew")],
        },
    )

    read = unscrewing_operation_from(node)  # type: ignore[arg-type]

    assert read.results == ("ANOMALY: MissingScrew",)
    assert read.outcome == f"{JMS_NS}MissingScrew"


def test_a_failed_that_did_not_reach_the_store_keeps_no_status():
    commit = FakeCommit()
    screwing, memory = resource(
        classify=raises(DetectorNotReached("it did not answer", ENDPOINT)),
        commit=commit,
    )

    def refuse_then_raise(operation: IRI) -> Called:
        commit.refuse = True
        raise DetectorNotReached("it did not answer", ENDPOINT)

    screwing.classify = refuse_then_raise
    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status is None
    assert record.why.startswith(NOT_REACHED + "it did not answer")
    assert "did not reach the store" in record.why


def test_an_operation_that_is_not_queued_and_a_refused_entry_keep_nothing():
    screwing, memory = resource(classify=raises(AssertionError("no call")))
    screwing.unscrew(
        UnscrewingOperation(OPERATION, OperationStatus.DONE, (SCREW,), (JMS.M4,))
    )
    commit = FakeCommit()
    commit.refuse = True
    refused, refused_memory = resource(
        classify=raises(AssertionError("no call")), commit=commit
    )
    refused.unscrew(QUEUED)

    assert memory.state().attempts == []
    assert refused_memory.state().attempts == []


def test_the_intake_reads_the_seven_values_of_the_screw_type_with_the_operation():
    chains = [
        [IRI(str(step)) for step in chain]
        for chain in OPERATION_SCOPE.to_property_chains()
    ]
    for prop in TYPE_LEVEL:
        assert [JMS.concernsScrew, JMS.hasScrewType, prop] in chains
    # The intake gives a linked value as a node with an `id` and what its chains ask for.
    screw_type = SimpleNamespace(
        id=str(JMS.M4), data={prop: [value] for prop, value in M4_VALUES.items()}
    )
    screw = SimpleNamespace(id=str(SCREW), data={JMS.hasScrewType: [screw_type]})
    node = SimpleNamespace(id=str(OPERATION), data={JMS.concernsScrew: [screw]})

    operation = unscrewing_operation_from(node)  # type: ignore[arg-type]

    assert operation.screws == (SCREW,)
    assert operation.screw_types == (JMS.M4,)
    assert operation.type_values == M4_VALUES


def _operation_whose_type_holds(values: dict) -> UnscrewingOperation:
    """The unscrewing operation, `queued`, as the intake reads it, whose Screw's type M4 holds
    `values`."""
    screw_type = SimpleNamespace(id=str(JMS.M4), data=values)
    screw = SimpleNamespace(id=str(SCREW), data={JMS.hasScrewType: [screw_type]})
    node = SimpleNamespace(
        id=str(OPERATION),
        data={
            JMS.concernsScrew: [screw],
            SVC.hasOperationStatus: [OperationStatus.QUEUED],
        },
    )
    return unscrewing_operation_from(node)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda values: values.pop(JMS.hasMaxDynamicTorque),
            "the screw type M4 holds no value of MaxDynamicTorque",
        ),
        (
            lambda values: values.update({JMS.hasMaxLooseTorque: [1.0, 2.0]}),
            "the screw type M4 holds 2 values of MaxLooseTorque, but allows one",
        ),
        (
            lambda values: values.update({JMS.hasMinCounterTorque: [float("nan")]}),
            "the screw type M4 holds nan as MinCounterTorque, not a finite number",
        ),
    ],
    ids=["missing", "duplicated", "not finite"],
)
def test_a_screw_type_without_a_usable_value_ends_the_operation_failed_before_the_attempt(
    change, problem, caplog
):
    values = {prop: [value] for prop, value in M4_VALUES.items()}
    change(values)
    attempted: list[IRI] = []

    def attempt(screw: IRI, given: Mapping[IRI, float]) -> Frame:
        attempted.append(screw)
        return example_frame("success")

    commit = FakeCommit()
    screwing, memory = resource(
        classify=raises(AssertionError("the detector is called")),
        attempt=attempt,
        commit=commit,
    )
    why = NOT_ATTEMPTED + problem

    with caplog.at_level(logging.INFO, logger="jms_kapps.uc1.screwing_resource"):
        screwing.unscrew(_operation_whose_type_holds(values))

    assert attempted == []
    # One commit, `queued` straight to `failed` with why: no `running`, no reference.
    ((update,),) = commit.entries
    assert update.instance_iri == OPERATION
    assert update.data == {
        str(SVC.hasOperationStatus): [OperationStatus.FAILED],
        str(SVC.hasExecutionResult): [why],
    }
    record = the_one(memory)
    assert (record.status, record.why, record.reference, record.detector) == (
        OperationStatus.FAILED,
        why,
        None,
        None,
    )
    assert record.type_values == {}
    assert not_attempted_record(RESOURCE, OPERATION, why) in [
        entry.getMessage() for entry in caplog.records
    ]


def test_each_unusable_value_of_the_screw_type_is_named_in_the_order_of_the_ontology():
    values = {prop: [value] for prop, value in M4_VALUES.items()}
    del values[JMS.hasMinCounterTorque]
    values[JMS.hasMaxDynamicTorque] = [float("inf")]

    operation = _operation_whose_type_holds(values)

    assert operation.type_problems == (
        "the screw type M4 holds no value of MinCounterTorque",
        "the screw type M4 holds inf as MaxDynamicTorque, not a finite number",
    )


def test_a_screw_type_that_gives_all_seven_values_names_no_problem():
    values = {prop: [value] for prop, value in M4_VALUES.items()}

    assert _operation_whose_type_holds(values).type_problems == ()


def test_a_screw_that_names_no_screw_type_ends_the_operation_failed_before_the_attempt():
    commit = FakeCommit()
    screwing, memory = resource(
        classify=raises(AssertionError("the detector is called")), commit=commit
    )
    screw = SimpleNamespace(id=str(SCREW), data={})
    node = SimpleNamespace(
        id=str(OPERATION),
        data={
            JMS.concernsScrew: [screw],
            SVC.hasOperationStatus: [OperationStatus.QUEUED],
        },
    )

    screwing.unscrew(unscrewing_operation_from(node))  # type: ignore[arg-type]

    assert the_one(memory).why == (
        NOT_ATTEMPTED + "the Screw TestScrew names no Screw type"
    )
    assert len(commit.entries) == 1


def test_a_refused_commit_of_failed_before_the_attempt_keeps_no_status():
    values = {prop: [value] for prop, value in M4_VALUES.items()}
    del values[JMS.hasMaxDynamicTorque]
    commit = FakeCommit()
    commit.refuse = True
    screwing, memory = resource(
        classify=raises(AssertionError("the detector is called")), commit=commit
    )

    screwing.unscrew(_operation_whose_type_holds(values))

    record = the_one(memory)
    assert record.status is None
    assert record.why == (
        NOT_ATTEMPTED
        + "the screw type M4 holds no value of MaxDynamicTorque"
        + "; the commit of failed did not reach the store"
    )


def test_an_operation_the_detector_left_as_it_was_keeps_why():
    answer = ClassifyAnswer(operation=str(OPERATION), why="it is failed, not running")
    screwing, memory = resource(classify=lambda operation: Called(ENDPOINT, answer))

    screwing.unscrew(QUEUED)

    record = the_one(memory)
    assert record.status is None
    assert record.why == "the detector left it as it was: it is failed, not running"


def test_a_robot_that_never_answers_ends_the_operation_failed_with_the_timeout_as_the_reason() -> None:
    """The MQTT attempt against a robot that answers nothing: its timeout is the operation's
    `svc:hasExecutionResult`, the record's why, and so the text of the Reason column of the page
    and of the Attempts box of the dashboard."""
    from jms_kapps.uc1.robot_attempt import mqtt_attempt
    from jms_kapps.uc1.robot_binding import Sink
    from jms_kapps.uc1.screwing_resource_dashboard import render

    screwing, memory = resource(
        classify=raises(AssertionError("no call")),
        attempt=mqtt_attempt(lambda parameter, value: None, Sink(), timeout=0.2),
    )

    screwing.unscrew(QUEUED)

    reason = (
        "the unscrewing attempt failed: the robot did not end the attempt within 0.2 s; "
        "it did not report done or failed"
    )
    (record,) = memory.state().attempts
    assert record.status == OperationStatus.FAILED
    assert record.why == reason
    assert reason in render(memory.state())
