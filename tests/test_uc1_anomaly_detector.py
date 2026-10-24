"""The anomaly detector's classify call, offline: what it commits for one unscrewing operation.

The detector is given the middleware's own `commit` and `create`, on an object-graph mapper that
is faked at the one call they write through, `commit(changes=...)`. The fake keeps each write
that the store admits, and refuses one when a test tells it to. The reads are stand-ins too: the
operation as the detector reads it, the Screw's effective values, and the frame. So each test
sees exactly the writes of one classify call. `test_uc1_anomaly_detector_live.py` runs the same
call on a live GraphDB and a live InfluxDB.

The values of each test are example values, and the frames are replayed from the example recordings.
`_uc1_example.py` says how the values that the frame of `success` passes were chosen.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from _uc1_example import (
    EXAMPLE_M4_VALUES,
    RECORDING_PLACEMENT_VALUES,
    SUCCESS_PASSING_M4_VALUES,
)
from kapps_ogm import Create, Update
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI, PreconditionFailedError
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.anomaly_detector import (
    ANOMALY,
    NOT_CLASSIFIED,
    AnomalyDetector,
    OperationToClassify,
    left_as_it_is_record,
    outcome_iri,
)
from jms_kapps.uc1.anomaly_detector_memory import DetectorMemory
from jms_kapps.uc1.decision_tree import DETECTION_RESULTS
from jms_kapps.uc1.effective_values import (
    EffectiveValuesWithOrigin,
    as_properties,
    resolve_with_origin,
)
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.timeseries import FrameNotFound, InfluxDBError
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PROV

DETECTOR = IRI("TestAnomalyDetector", base=JMS_NS)
OPERATION = IRI("TestUnscrewingOperation", base=JMS_NS)
SCREW = IRI("TestScrew", base=JMS_NS)
REFERENCE = "influxdb:///jms-kapps/unscrewing?frame=f1&start=2026-10-05T00:00:00.000000000Z&stop=2026-10-05T00:00:07.000000000Z"


def effective(type_values: dict[IRI, float]) -> EffectiveValuesWithOrigin:
    """The effective values of a Screw of a type with `type_values`, placed as `RECORDING_PLACEMENT_VALUES`."""
    return resolve_with_origin(
        SCREW,
        {prop: [value] for prop, value in RECORDING_PLACEMENT_VALUES.items()},
        JMS.M4,
        {prop: [value] for prop, value in type_values.items()},
    )


class FakeOGM:
    """The object-graph mapper at the call the middleware's commit writes through.

    `written` holds each write that the store admitted, as its list of changes. `refuse`, when
    set, refuses the next write with that error, and is then cleared.
    """

    def __init__(self) -> None:
        self.written: list[list[Create | Update]] = []
        self.refuse: Exception | None = None

    def commit(self, *, changes, named_graph=None):
        changes = list(changes)
        if self.refuse is not None:
            refusal, self.refuse = self.refuse, None
            raise refusal
        self.written.append(changes)
        return [None for _ in changes]


class Reads:
    """The detector's reads, as a test arranges them.

    A read of the operation gives the status and the result of the last write of the store that
    set them. Before any such write, it gives `statuses` in turn, one for each read, and the last
    one stays: so a test makes another party end the operation meanwhile. `values` and `frame`
    give the Screw's effective values and the frame, or raise.
    """

    def __init__(
        self,
        *,
        statuses: list[str],
        values: Callable[[IRI], EffectiveValuesWithOrigin],
        frame: Callable[[str], Frame],
    ) -> None:
        self.statuses = list(statuses)
        self.values = values
        self.frame = frame
        self.frames_read: list[str] = []

    def operation(self, operation: IRI, ogm: FakeOGM) -> OperationToClassify:
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        results: list[str] = []
        for write in ogm.written:
            for change in write:
                if IRI(str(change.instance_iri)) != operation:
                    continue
                status = change.data.get(str(SVC.hasOperationStatus), [status])[0]
                results = change.data.get(str(SVC.hasExecutionResult), results)
        return OperationToClassify(
            operation=operation,
            status=status,
            results=tuple(results),
            screws=(SCREW,),
            references=(REFERENCE,),
        )

    def read_frame(self, reference: str) -> Frame:
        self.frames_read.append(reference)
        return self.frame(reference)


@pytest.fixture
def ogm() -> FakeOGM:
    return FakeOGM()


@pytest.fixture
def middleware(ogm: FakeOGM) -> SemanticMiddleware:
    return SemanticMiddleware(
        mode="resource",
        resource_iri=str(DETECTOR),
        service_class=str(JMS.AnomalyDetectorService),
        ogm=ogm,
        heartbeat_interval=None,
    )


# When a test's classifications end, by the detector's clock.
ENDED = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)


def detector_with(
    middleware: SemanticMiddleware,
    reads: Reads,
    memory: DetectorMemory | None = None,
) -> AnomalyDetector:
    return AnomalyDetector(
        DETECTOR,
        commit=middleware.commit,
        create=middleware.create,
        read=lambda operation: reads.operation(operation, middleware.ogm),
        values=reads.values,
        frame=reads.read_frame,
        clock=lambda: ENDED,
        memory=memory if memory is not None else DetectorMemory(),
    )


def created(write: list[Create | Update], class_iri: IRI) -> Create:
    """The one instance of `class_iri` that `write` creates."""
    (creation,) = [
        c for c in write if isinstance(c, Create) and c.class_iri == class_iri
    ]
    return creation


def returns(value):
    return lambda *args: value


def raises(error: Exception):
    def read(*args):
        raise error

    return read


def the_one_write(ogm: FakeOGM) -> list[Create | Update]:
    (write,) = ogm.written
    return write


def change_of(write: list[Create | Update], instance: IRI) -> Create | Update:
    (change,) = [c for c in write if IRI(str(c.instance_iri)) == instance]
    return change


def reference_to(iri: IRI) -> dict:
    return {"id": str(iri)}


# --- A running operation is classified, and ends done ----------------------------------------


def test_a_running_operation_ends_done_with_its_outcome_and_its_decision_snapshot_in_one_commit(
    middleware, ogm
):
    values = effective(SUCCESS_PASSING_M4_VALUES)
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(values),
        frame=returns(example_frame("success")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    write = the_one_write(ogm)
    snapshot = created(write, JMS.ParameterCopy)
    classification = created(write, PROV.Activity)
    assert snapshot.data == {
        str(prop): [value] for prop, value in as_properties(values.values).items()
    } | {
        str(PROV.wasGeneratedBy): [reference_to(IRI(str(classification.instance_iri)))]
    }
    assert change_of(write, OPERATION).data == {
        str(SVC.hasOperationStatus): [OperationStatus.DONE],
        str(JMS.hasDetectionResult): [reference_to(IRI("ScrewIntact", base=JMS_NS))],
        str(JMS.classifiedWith): [reference_to(IRI(str(snapshot.instance_iri)))],
    }
    assert answer.ended == OperationStatus.DONE
    assert answer.outcome == str(IRI("ScrewIntact", base=JMS_NS))
    assert reads.frames_read == [REFERENCE]


@pytest.mark.paper_claim
def test_the_classification_is_a_prov_activity_of_the_detector_in_the_same_commit(
    middleware, ogm
):
    """Paper claim 16, the PROV witness on the decision: the commit that ends the operation `done`
    also creates the classification, with the detector's resource as its agent and the clock's
    time as its end, and the snapshot names it."""
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=returns(example_frame("success")),
    )

    detector_with(middleware, reads).classify(OPERATION)

    write = the_one_write(ogm)
    classification = created(write, PROV.Activity)
    assert classification.data == {
        str(PROV.wasAssociatedWith): [reference_to(DETECTOR)],
        str(PROV.endedAtTime): [ENDED],
    }
    snapshot = created(write, JMS.ParameterCopy)
    assert snapshot.data[str(PROV.wasGeneratedBy)] == [
        reference_to(IRI(str(classification.instance_iri)))
    ]


def test_each_classification_has_its_own_iri_in_the_use_cases_namespace(
    middleware, ogm
):
    """Not one that the middleware mints from the class, which would be in PROV-O's namespace."""
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=returns(example_frame("success")),
    )
    detector = detector_with(middleware, reads)

    detector.classify(OPERATION)
    detector.classify(IRI("TestUnscrewingOperation2", base=JMS_NS))

    first, second = (str(created(w, PROV.Activity).instance_iri) for w in ogm.written)
    assert first.startswith(f"{JMS_NS}Classification_"), first
    assert second.startswith(f"{JMS_NS}Classification_"), second
    assert first != second


# --- A failure outcome ends the operation failed, and names the outcome --------------------


def test_a_failure_outcome_ends_the_operation_failed_naming_it_in_the_classifications_commit(
    middleware, ogm
):
    """Every outcome other than ScrewIntact is a failure outcome: the commit that records the
    classification, the snapshot that it generated and the outcome ends the operation `failed`,
    and its result names the outcome. The measured end of the approach goes in the same commit."""
    values = effective(EXAMPLE_M4_VALUES)
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(values),
        frame=returns(example_frame("missing_screw")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    write = the_one_write(ogm)
    classification = created(write, PROV.Activity)
    snapshot = created(write, JMS.ParameterCopy)
    assert snapshot.data[str(PROV.wasGeneratedBy)] == [
        reference_to(IRI(str(classification.instance_iri)))
    ]
    assert change_of(write, OPERATION).data == {
        str(SVC.hasOperationStatus): [OperationStatus.FAILED],
        str(SVC.hasExecutionResult): ["ANOMALY: MissingScrew"],
        str(JMS.hasDetectionResult): [reference_to(IRI("MissingScrew", base=JMS_NS))],
        str(JMS.classifiedWith): [reference_to(IRI(str(snapshot.instance_iri)))],
        str(JMS.hasMeasuredApproachEndPosition): [0.0],
    }
    assert answer.ended == OperationStatus.FAILED
    assert answer.outcome == str(IRI("MissingScrew", base=JMS_NS))
    assert answer.why == "ANOMALY: MissingScrew"


def test_the_reason_of_a_failure_outcome_is_told_apart_from_an_attempt_not_classified():
    assert not NOT_CLASSIFIED.startswith(ANOMALY)


def test_a_failure_outcome_whose_end_the_store_refuses_leaves_an_operation_that_ended(
    middleware, ogm, caplog
):
    reads = Reads(
        statuses=[
            OperationStatus.RUNNING,
            OperationStatus.RUNNING,
            OperationStatus.FAILED,
        ],
        values=returns(effective(EXAMPLE_M4_VALUES)),
        frame=returns(example_frame("missing_screw")),
    )
    ogm.refuse = PreconditionFailedError("the status the write removes is gone")

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [
        left_as_it_is_record(DETECTOR, OPERATION, OperationStatus.FAILED)
    ]
    assert answer.ended is None


# --- An operation that is not running is left as it is ---------------------------------------


def detector_log(caplog) -> list[str]:
    """The detector's own records, without the lines that name the class scope it uses
    (`scope_log`), which every read and write prints."""
    return [
        entry.getMessage()
        for entry in caplog.records
        if entry.name == "jms_kapps.uc1.anomaly_detector"
        and not entry.getMessage().startswith("Anomaly detector ")
    ]


@pytest.mark.parametrize("status", [OperationStatus.FAILED, OperationStatus.DONE])
def test_an_operation_that_is_not_running_is_left_as_it_is(
    middleware, ogm, caplog, status
):
    reads = Reads(
        statuses=[status],
        values=raises(AssertionError("no values are read")),
        frame=raises(AssertionError("no frame is read")),
    )

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [left_as_it_is_record(DETECTOR, OPERATION, status)]
    assert answer.ended is None


def test_an_operation_that_ended_while_it_was_classified_is_left_as_it_is(
    middleware, ogm, caplog
):
    # Running when the call came, failed by the time the detector ends it: the screwing
    # resource stopped waiting for the answer, and ended it itself.
    reads = Reads(
        statuses=[OperationStatus.RUNNING, OperationStatus.FAILED],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=returns(example_frame("success")),
    )

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [
        left_as_it_is_record(DETECTOR, OPERATION, OperationStatus.FAILED)
    ]
    assert answer.ended is None
    assert answer.outcome is None


def test_an_end_that_the_store_refuses_because_the_status_changed_leaves_the_operation(
    middleware, ogm, caplog
):
    # Running at the detector's last read, and ended before the write: the store refuses the
    # write, whose status is gone, and the detector reads the operation ended.
    reads = Reads(
        statuses=[
            OperationStatus.RUNNING,
            OperationStatus.RUNNING,
            OperationStatus.FAILED,
        ],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=returns(example_frame("success")),
    )
    ogm.refuse = PreconditionFailedError("the status the write removes is gone")

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [
        left_as_it_is_record(DETECTOR, OPERATION, OperationStatus.FAILED)
    ]
    assert answer.ended is None


# --- An attempt that cannot be classified ends its operation failed, with why ----------------


def assert_failed_with_nothing_else(ogm: FakeOGM, answer) -> str:
    """The one write is the operation's `failed` with why, and nothing else. Gives the why."""
    (change,) = the_one_write(ogm)
    assert isinstance(change, Update)
    assert IRI(str(change.instance_iri)) == OPERATION
    assert set(change.data) == {
        str(SVC.hasOperationStatus),
        str(SVC.hasExecutionResult),
    }
    assert change.data[str(SVC.hasOperationStatus)] == [OperationStatus.FAILED]
    (why,) = change.data[str(SVC.hasExecutionResult)]
    assert why.startswith(NOT_CLASSIFIED)
    assert answer.ended == OperationStatus.FAILED
    assert answer.why == why
    return why


def test_a_frame_that_the_store_lacks_ends_the_operation_failed_with_why(
    middleware, ogm
):
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound(f"the store holds no row of {REFERENCE}")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    why = assert_failed_with_nothing_else(ogm, answer)
    assert f"the store holds no row of {REFERENCE}" in why


def test_a_frame_read_that_fails_ends_the_operation_failed_with_why(middleware, ogm):
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(InfluxDBError("POST /api/v3/query_sql answered 500: disk full")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    why = assert_failed_with_nothing_else(ogm, answer)
    assert "answered 500: disk full" in why


def test_a_screw_without_placement_values_ends_the_operation_failed_and_nothing_else(
    middleware, ogm
):
    def unplaced(screw: IRI) -> EffectiveValuesWithOrigin:
        return resolve_with_origin(
            screw,
            {},
            JMS.M4,
            {prop: [value] for prop, value in SUCCESS_PASSING_M4_VALUES.items()},
        )

    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=unplaced,
        frame=returns(example_frame("success")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    why = assert_failed_with_nothing_else(ogm, answer)
    assert "hasMinApproachPosition" in why
    assert "hasMinScrewHeadPosition" in why
    assert "hasMaxScrewHeadPosition" in why


def test_curves_that_the_decision_tree_refuses_end_the_operation_failed_with_why(
    middleware, ogm
):
    # The missing screw's approach ends at 0.0 mm without contact. With the depth limit far
    # below that, its frame stops before the tree reaches an exit of the approach.
    deep = {**RECORDING_PLACEMENT_VALUES, JMS.hasMinApproachPosition: -10.0}
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(
            resolve_with_origin(
                SCREW,
                {prop: [value] for prop, value in deep.items()},
                JMS.M4,
                {prop: [value] for prop, value in EXAMPLE_M4_VALUES.items()},
            )
        ),
        frame=returns(example_frame("missing_screw")),
    )

    answer = detector_with(middleware, reads).classify(OPERATION)

    why = assert_failed_with_nothing_else(ogm, answer)
    assert "ends before contact and above the depth limit" in why


def test_an_operation_that_ended_before_a_failed_read_is_left_as_it_is(
    middleware, ogm, caplog
):
    reads = Reads(
        statuses=[OperationStatus.RUNNING, OperationStatus.FAILED],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound("the store holds no row of the frame")),
    )

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [
        left_as_it_is_record(DETECTOR, OPERATION, OperationStatus.FAILED)
    ]
    assert answer.ended is None


def test_an_operation_that_cannot_be_read_refuses_the_call(middleware, ogm):
    def unreadable(operation: IRI) -> OperationToClassify:
        raise ValueError(f"the store holds no class for {operation}")

    detector = AnomalyDetector(
        DETECTOR,
        commit=middleware.commit,
        create=middleware.create,
        read=unreadable,
        values=raises(AssertionError("no values are read")),
        frame=raises(AssertionError("no frame is read")),
    )

    with pytest.raises(ValueError, match="holds no class"):
        detector.classify(OPERATION)
    assert ogm.written == []


# --- The detector's classes --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "superclass"),
    [(JMS.AnomalyDetectorService, SVC.Service), (JMS.ClassifyWorkflow, SVC.Workflow)],
)
def test_each_class_of_the_detector_is_a_direct_subclass_of_its_service_class(
    cls, superclass
) -> None:
    """The middleware checks a class of a service or a workflow by one `rdfs:subClassOf`
    triple, and does not follow a chain."""
    graph = Graph().parse(
        data=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        format="turtle",
    )

    assert (URIRef(str(cls)), RDF.type, OWL.Class) in graph
    assert set(graph.objects(URIRef(str(cls)), RDFS.subClassOf)) == {
        URIRef(str(superclass))
    }


def test_a_failed_that_the_store_refuses_leaves_an_operation_that_ended_meanwhile(
    middleware, ogm, caplog
):
    # Running at the detector's read on the error path, and ended before its `failed` reaches
    # the store: the store refuses the `failed`, and the detector reads the operation ended.
    reads = Reads(
        statuses=[
            OperationStatus.RUNNING,
            OperationStatus.RUNNING,
            OperationStatus.FAILED,
        ],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound("the store holds no row of the frame")),
    )
    ogm.refuse = PreconditionFailedError("the status the write removes is gone")

    with caplog.at_level(logging.INFO):
        answer = detector_with(middleware, reads).classify(OPERATION)

    assert ogm.written == []
    assert detector_log(caplog) == [
        left_as_it_is_record(DETECTOR, OPERATION, OperationStatus.FAILED)
    ]
    assert answer.ended is None


def test_a_failed_that_the_store_refuses_on_a_running_operation_refuses_the_call(
    middleware, ogm
):
    # The store refuses the `failed`, and the operation is still running: the detector could
    # not end it, so the call fails, and the screwing resource ends the operation itself.
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound("the store holds no row of the frame")),
    )
    ogm.refuse = RuntimeError("the store does not answer")

    with pytest.raises(Exception, match="the store holds no row of the frame"):
        detector_with(middleware, reads).classify(OPERATION)
    assert ogm.written == []


def test_each_outcome_of_the_tree_is_an_unscrewing_outcome_of_the_ontology() -> None:
    """The detector writes an outcome as the individual of the tree's exit by name."""
    graph = Graph().parse(
        data=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        format="turtle",
    )

    assert {
        str(subject)
        for subject in graph.subjects(RDF.type, URIRef(str(JMS.DetectionResult)))
    } == {str(outcome_iri(outcome)) for outcome in DETECTION_RESULTS}


# --- What the detector keeps in its memory, for its dashboard ----------------------------------
#
# A classification goes into the memory once its commit has reached the store; an attempt that
# could not be classified, once its `failed` has. What a Screw type holds goes in at each read
# of the effective values, a true read even when the commit that follows does not reach the store.


def test_a_classification_that_reached_the_store_is_kept_with_its_values_and_whether_the_tree_asked(
    middleware, ogm
):
    memory = DetectorMemory(clock=lambda: ENDED)
    values = effective(EXAMPLE_M4_VALUES)
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(values),
        frame=returns(example_frame("missing_screw")),
    )

    detector_with(middleware, reads, memory).classify(OPERATION)

    state = memory.state()
    (kept,) = state.classifications
    assert state.classification_count == 1
    assert (kept.at, kept.operation, kept.screw, kept.screw_type) == (
        ENDED,
        OPERATION,
        SCREW,
        JMS.M4,
    )
    assert (kept.outcome, kept.why) == ("MissingScrew", "ANOMALY: MissingScrew")
    assert {used.prop: used.value for used in kept.values} == as_properties(
        values.values
    )
    assert {used.prop for used in kept.values if used.asked} == {
        JMS.hasMinApproachAxialForce,
        JMS.hasMinApproachPosition,
    }
    (type_values,) = state.type_values
    assert (type_values.screw_type, type_values.values) == (JMS.M4, EXAMPLE_M4_VALUES)


def test_an_attempt_it_could_not_classify_is_kept_with_why(middleware, ogm):
    memory = DetectorMemory(clock=lambda: ENDED)
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound("the store holds no row of the frame")),
    )

    answer = detector_with(middleware, reads, memory).classify(OPERATION)

    (kept,) = memory.state().classifications
    assert (kept.operation, kept.screw, kept.outcome) == (OPERATION, SCREW, None)
    assert kept.why == answer.why
    assert len(kept.values) == 10
    assert not any(used.asked for used in kept.values)


def test_an_operation_left_as_it_is_is_not_kept(middleware, ogm):
    memory = DetectorMemory()
    reads = Reads(
        statuses=[OperationStatus.FAILED],
        values=raises(AssertionError("no values are read")),
        frame=raises(AssertionError("no frame is read")),
    )

    detector_with(middleware, reads, memory).classify(OPERATION)

    assert memory.state().classifications == []


def test_a_classification_whose_commit_did_not_reach_the_store_is_not_kept(
    middleware, ogm
):
    memory = DetectorMemory()
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=raises(FrameNotFound("the store holds no row of the frame")),
    )
    ogm.refuse = RuntimeError("the store does not answer")

    with pytest.raises(Exception, match="the store holds no row of the frame"):
        detector_with(middleware, reads, memory).classify(OPERATION)

    state = memory.state()
    assert state.classifications == []
    assert [values.screw_type for values in state.type_values] == [JMS.M4]


def test_the_values_of_a_screw_type_are_those_of_the_last_read(middleware, ogm):
    """A value that the learner wrote to the Screw type shows at the detector's next read."""
    memory = DetectorMemory()
    learned = {**SUCCESS_PASSING_M4_VALUES, JMS.hasMaxDynamicTorque: 0.84}
    # What the graph holds now: the seeded values, then the learner's.
    held = [effective(SUCCESS_PASSING_M4_VALUES)]
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=lambda screw: held[0],
        frame=returns(example_frame("success")),
    )
    detector = detector_with(middleware, reads, memory)
    detector.classify(OPERATION)
    held[0] = effective(learned)
    ogm.written.clear()

    detector.classify(OPERATION)

    (type_values,) = memory.state().type_values
    assert type_values.values[JMS.hasMaxDynamicTorque] == 0.84
    newest = memory.state().classifications[0]
    assert {used.prop: used.value for used in newest.values}[
        JMS.hasMaxDynamicTorque
    ] == 0.84
