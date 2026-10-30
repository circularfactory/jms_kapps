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
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _uc1_example import (
    EXAMPLE_M4_VALUES,
    RECORDING_PLACEMENT_VALUES,
    SUCCESS_PASSING_M4_VALUES,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_ogm import Create, Node, Update
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI, PreconditionFailedError
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.anomaly_detector import (
    ANOMALY,
    MODE_ROUTE,
    NOT_CLASSIFIED,
    OPERATION_SCOPE,
    SAVE_UQ_OPTION,
    UQ_OPERATION_SCOPE,
    AnomalyDetector,
    OperationToClassify,
    approach_end_position,
    attempt_signals,
    build,
    left_as_it_is_record,
    mount_mode,
    operation_to_classify_from,
    outcome_iri,
    parse_args,
)
from jms_kapps.uc1.anomaly_detector_memory import (
    APPROACH_END,
    DetectorMemory,
    MeasuredPosition,
)
from jms_kapps.uc1.decision_tree import DETECTION_RESULTS, contact_position
from jms_kapps.uc1.effective_values import (
    EffectiveValuesWithOrigin,
    as_properties,
    resolve_with_origin,
)
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.seed import POSITION_STANDARD_DEVIATION
from jms_kapps.uc1.timeseries import FrameNotFound, InfluxDBError
from jms_kapps.uc1.uncertainty import GMM_LITERAL, read_measurement
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PROV, UQ

DETECTOR = IRI("TestAnomalyDetector", base=JMS_NS)
OPERATION = IRI("TestUnscrewingOperation", base=JMS_NS)
SCREW = IRI("TestScrew", base=JMS_NS)
SCREWING_RESOURCE = IRI("TestScrewingResource", base=JMS_NS)
OPERATION_2 = IRI("TestUnscrewingOperation2", base=JMS_NS)
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
        # Whether each read of the operation asked for what saving measurements as uncertainty
        # quantifications needs.
        self.uq_reads: list[bool] = []

    def operation(
        self, operation: IRI, ogm: FakeOGM, *, saves_uq: bool = False
    ) -> OperationToClassify:
        """With `saves_uq`, also the resource whose capability it implements, and the head-position variable that
        the Screw holds by the last write that linked one."""
        self.uq_reads.append(saves_uq)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        results: list[str] = []
        variables: list[dict] = []
        for write in ogm.written:
            for change in write:
                if IRI(str(change.instance_iri)) == SCREW:
                    variables = change.data.get(
                        str(JMS.hasScrewHeadPositionVariable), variables
                    )
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
            resources=(SCREWING_RESOURCE,) if saves_uq else (),
            head_position_variables=(
                tuple(IRI(v["id"]) for v in variables) if saves_uq else ()
            ),
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
    standard_deviations: Callable[[IRI], list[object]] = lambda resource: [
        POSITION_STANDARD_DEVIATION
    ],
) -> AnomalyDetector:
    return AnomalyDetector(
        DETECTOR,
        commit=middleware.commit,
        create=middleware.create,
        read=lambda operation, **kw: reads.operation(operation, middleware.ogm, **kw),
        values=reads.values,
        frame=reads.read_frame,
        clock=lambda: ENDED,
        memory=memory if memory is not None else DetectorMemory(),
        standard_deviations=standard_deviations,
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


def test_the_classification_is_a_prov_activity_of_the_detector_in_the_same_commit(
    middleware, ogm
):
    """The PROV witness on the decision: the commit that ends the operation `done`
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


# --- Save measurements as uncertainty quantifications ------------------------------------------
#
# Every expected position and variance is derived from the recordings and the seed's standard
# deviation, not typed in.


def zero_zero_two(operation: IRI = OPERATION) -> list[tuple[str, str]]:
    """What a classification of 0.0.2 changes: it creates its activity and its parameter copy,
    and updates `operation`, and nothing else."""
    return sorted(
        [
            ("create", str(PROV.Activity)),
            ("create", str(JMS.ParameterCopy)),
            ("update", str(operation)),
        ]
    )


def what_changes(write: list[Create | Update]) -> list[tuple[str, str]]:
    return sorted(
        ("create", str(c.class_iri))
        if isinstance(c, Create)
        else ("update", str(c.instance_iri))
        for c in write
    )


def signals_of(recording: str, values: EffectiveValuesWithOrigin):
    return attempt_signals(example_frame(recording)), values.values


def variable_created(write: list[Create | Update], link: list[dict]) -> Create:
    """The random variable that `write` creates, which `link` names."""
    (reference,) = link
    variable = created(write, UQ.RandomVariable)
    assert str(variable.instance_iri) == reference["id"]
    return variable


def measurement_of(data: dict) -> tuple[float, float]:
    (literal,) = data[str(UQ.hasDistribution)]
    assert isinstance(literal, Literal)
    assert literal.datatype == GMM_LITERAL
    return read_measurement(literal)


def on() -> DetectorMemory:
    return DetectorMemory(saves_uq=True)


def classify_once(middleware, recording, values, memory, operation=OPERATION, **kw):
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(values),
        frame=returns(example_frame(recording)),
    )
    answer = detector_with(middleware, reads, memory, **kw).classify(operation)
    return reads, answer


@pytest.mark.parametrize(
    ("recording", "type_values", "outcome"),
    [
        ("missing_screw", EXAMPLE_M4_VALUES, "MissingScrew"),
        ("success", SUCCESS_PASSING_M4_VALUES, "ScrewIntact"),
    ],
)
def test_with_the_mode_off_the_detector_writes_exactly_what_0_0_2_writes(
    middleware, ogm, recording, type_values, outcome
):
    values = effective(type_values)

    reads, _ = classify_once(middleware, recording, values, DetectorMemory())

    write = the_one_write(ogm)
    classification = created(write, PROV.Activity)
    snapshot = created(write, JMS.ParameterCopy)
    assert what_changes(write) == zero_zero_two()
    expected = {
        str(SVC.hasOperationStatus): [
            OperationStatus.DONE if outcome == "ScrewIntact" else OperationStatus.FAILED
        ],
        str(JMS.hasDetectionResult): [reference_to(outcome_iri(outcome))],
        str(JMS.classifiedWith): [reference_to(IRI(str(snapshot.instance_iri)))],
    }
    if outcome != "ScrewIntact":
        signals, _ = signals_of(recording, values)
        expected[str(SVC.hasExecutionResult)] = [ANOMALY + outcome]
        expected[str(JMS.hasMeasuredApproachEndPosition)] = [
            approach_end_position(signals)
        ]
    assert change_of(write, OPERATION).data == expected
    assert classification.data == {
        str(PROV.wasAssociatedWith): [reference_to(DETECTOR)],
        str(PROV.endedAtTime): [ENDED],
    }
    assert True not in reads.uq_reads


def test_with_the_mode_on_a_missing_screw_writes_its_end_position_and_its_variable(
    middleware, ogm
):
    values = effective(EXAMPLE_M4_VALUES)
    signals, tree_values = signals_of("missing_screw", values)
    end = approach_end_position(signals)

    reads, answer = classify_once(middleware, "missing_screw", values, on())

    write = the_one_write(ogm)
    operation = change_of(write, OPERATION).data
    assert operation[str(JMS.hasMeasuredApproachEndPosition)] == [end]
    variable = variable_created(
        write, operation[str(JMS.hasApproachEndPositionVariable)]
    )
    assert measurement_of(variable.data) == (end, POSITION_STANDARD_DEVIATION**2)
    assert variable.data[str(UQ.hasDomain)] == [reference_to(UQ.double)]
    assert contact_position(signals, tree_values) is None
    assert [c for c in write if IRI(str(c.instance_iri)) == SCREW] == []
    assert answer.why == ANOMALY + "MissingScrew"
    assert reads.uq_reads[0] is True


def test_with_the_mode_on_an_intact_screw_writes_its_head_position_and_its_variable(
    middleware, ogm
):
    values = effective(SUCCESS_PASSING_M4_VALUES)
    signals, tree_values = signals_of("success", values)
    at_contact = contact_position(signals, tree_values)
    assert at_contact is not None

    classify_once(middleware, "success", values, on())

    write = the_one_write(ogm)
    screw = change_of(write, SCREW).data
    assert screw[str(JMS.hasMeasuredScrewHeadPosition)] == [at_contact]
    variable = variable_created(write, screw[str(JMS.hasScrewHeadPositionVariable)])
    assert measurement_of(variable.data) == (at_contact, POSITION_STANDARD_DEVIATION**2)
    operation = change_of(write, OPERATION).data
    assert str(JMS.hasMeasuredApproachEndPosition) not in operation
    assert str(JMS.hasApproachEndPositionVariable) not in operation


def test_with_the_mode_on_a_failure_after_contact_writes_both_positions_and_variables(
    middleware, ogm
):
    """HeadOccluded: the band is moved below the contact of `success`, so the attempt reaches
    contact and fails; the commit holds the end of the approach and the head position, each with
    its own variable."""
    signals = attempt_signals(example_frame("success"))
    at_contact = contact_position(signals, effective(SUCCESS_PASSING_M4_VALUES).values)
    assert at_contact is not None
    placed = {
        **RECORDING_PLACEMENT_VALUES,
        JMS.hasMinScrewHeadPosition: at_contact - 1.0,
        JMS.hasMaxScrewHeadPosition: at_contact - 0.5,
    }
    values = resolve_with_origin(
        SCREW,
        {prop: [value] for prop, value in placed.items()},
        JMS.M4,
        {prop: [value] for prop, value in SUCCESS_PASSING_M4_VALUES.items()},
    )
    end = approach_end_position(signals)

    _, answer = classify_once(middleware, "success", values, on())

    write = the_one_write(ogm)
    assert answer.why == ANOMALY + "HeadOccluded"
    operation = change_of(write, OPERATION).data
    screw = change_of(write, SCREW).data
    assert operation[str(JMS.hasMeasuredApproachEndPosition)] == [end]
    assert screw[str(JMS.hasMeasuredScrewHeadPosition)] == [at_contact]
    variables = {
        str(c.instance_iri): measurement_of(c.data)
        for c in write
        if isinstance(c, Create) and c.class_iri == UQ.RandomVariable
    }
    ((end_link,),) = [operation[str(JMS.hasApproachEndPositionVariable)]]
    ((head_link,),) = [screw[str(JMS.hasScrewHeadPositionVariable)]]
    variance = POSITION_STANDARD_DEVIATION**2
    assert variables == {
        end_link["id"]: (end, variance),
        head_link["id"]: (at_contact, variance),
    }


def test_a_second_measurement_of_a_head_position_replaces_the_literal_and_the_estimate(
    middleware, ogm
):
    """In the one commit of the second classification, on the variable the Screw holds: no
    second variable, and nothing fused."""
    values = effective(SUCCESS_PASSING_M4_VALUES)
    signals, tree_values = signals_of("success", values)
    at_contact = contact_position(signals, tree_values)
    memory = on()
    classify_once(middleware, "success", values, memory)
    (first,) = ogm.written
    (link,) = change_of(first, SCREW).data[str(JMS.hasScrewHeadPositionVariable)]

    classify_once(middleware, "success", values, memory, operation=OPERATION_2)

    second = ogm.written[1]
    assert [
        c for c in second if isinstance(c, Create) and c.class_iri == UQ.RandomVariable
    ] == []
    held = change_of(second, IRI(link["id"]))
    assert isinstance(held, Update)
    assert set(held.data) == {str(UQ.hasDistribution)}
    assert measurement_of(held.data) == (at_contact, POSITION_STANDARD_DEVIATION**2)
    assert change_of(second, SCREW).data == {
        str(JMS.hasMeasuredScrewHeadPosition): [at_contact]
    }


def test_switching_the_mode_off_leaves_the_head_position_values_as_they_are(
    middleware, ogm
):
    values = effective(SUCCESS_PASSING_M4_VALUES)
    memory = on()
    classify_once(middleware, "success", values, memory)

    memory.set_saves_uq(False)
    reads, _ = classify_once(
        middleware, "success", values, memory, operation=OPERATION_2
    )

    second = ogm.written[1]
    assert what_changes(second) == zero_zero_two(OPERATION_2)
    assert reads.uq_reads == [False, False]


@pytest.mark.parametrize(
    "held", [[], [POSITION_STANDARD_DEVIATION, 0.2], [float("nan")], ["0.1"]]
)
def test_with_the_mode_on_a_robot_without_one_standard_deviation_ends_failed_with_why(
    middleware, ogm, held
):
    memory = on()

    _, answer = classify_once(
        middleware,
        "success",
        effective(SUCCESS_PASSING_M4_VALUES),
        memory,
        standard_deviations=returns(held),
    )

    why = assert_failed_with_nothing_else(ogm, answer)
    assert "position standard deviation" in why
    (shown,) = memory.state().classifications
    assert shown.outcome is None
    assert shown.saves_uq is True


def test_with_the_mode_on_an_operation_that_names_no_resource_of_its_capability_ends_failed(
    middleware, ogm
):
    reads = Reads(
        statuses=[OperationStatus.RUNNING],
        values=returns(effective(SUCCESS_PASSING_M4_VALUES)),
        frame=returns(example_frame("success")),
    )
    detector = detector_with(middleware, reads, on())
    detector.read = lambda operation, **kw: replace(
        reads.operation(operation, middleware.ogm, **kw), resources=()
    )

    answer = detector.classify(OPERATION)

    why = assert_failed_with_nothing_else(ogm, answer)
    assert "0 resources whose capability it implements" in why


def test_the_memory_keeps_the_mode_and_the_positions_of_each_classification(
    middleware, ogm
):
    values = effective(EXAMPLE_M4_VALUES)
    signals, _ = signals_of("missing_screw", values)
    memory = on()
    classify_once(middleware, "missing_screw", values, memory)
    memory.set_saves_uq(False)
    classify_once(middleware, "missing_screw", values, memory, operation=OPERATION_2)

    off, saved = memory.state().classifications
    assert (off.saves_uq, off.positions) == (False, ())
    assert saved.saves_uq is True
    assert saved.positions == (
        MeasuredPosition(
            APPROACH_END, approach_end_position(signals), POSITION_STANDARD_DEVIATION
        ),
    )


def test_the_operation_read_with_the_mode_on_carries_the_resource_and_the_held_variable():
    """One projection: the resource by the capability that the operation implements, once
    although two registrations reach it, and the variable by the Screw."""
    resource = Node(id=str(SCREWING_RESOURCE), data={})
    service = Node(id=f"{JMS_NS}TestService", data={SVC.isServiceOf: [resource]})
    workflow = Node(id=f"{JMS_NS}TestWorkflow", data={SVC.isWorkflowOf: [service]})
    again = Node(id=f"{JMS_NS}TestService2", data={SVC.isServiceOf: [resource]})
    workflow_again = Node(id=f"{JMS_NS}TestWorkflow2", data={SVC.isWorkflowOf: [again]})
    capability = Node(
        id=f"{JMS_NS}TestCapability",
        data={SVC.realizedByWorkflow: [workflow, workflow_again]},
    )
    variable = Node(id=f"{JMS_NS}TestVariable", data={})
    screw = Node(id=str(SCREW), data={JMS.hasScrewHeadPositionVariable: [variable]})
    node = Node(
        id=str(OPERATION),
        data={
            SVC.hasOperationStatus: [OperationStatus.RUNNING],
            JMS.concernsScrew: [screw],
            JMS.hasTimeSeriesReference: [REFERENCE],
            CFC.implementsCapability: [capability],
        },
    )

    found = operation_to_classify_from(node)

    assert found.screws == (SCREW,)
    assert found.resources == (SCREWING_RESOURCE,)
    assert found.head_position_variables == (IRI(f"{JMS_NS}TestVariable"),)
    chains = [tuple(c) for c in UQ_OPERATION_SCOPE.to_property_chains()]
    assert (
        CFC.implementsCapability,
        SVC.realizedByWorkflow,
        SVC.isWorkflowOf,
        SVC.isServiceOf,
    ) in chains
    assert (JMS.concernsScrew, JMS.hasScrewHeadPositionVariable) in chains
    for chain in OPERATION_SCOPE.to_property_chains():
        assert any(c[: len(chain)] == tuple(chain) for c in chains), chain


def test_the_process_takes_the_mode_option_off_by_default():
    assert parse_args(["--dataset", "d"]).save_measurements_as_uq is False
    assert (
        parse_args(["--dataset", "d", SAVE_UQ_OPTION]).save_measurements_as_uq is True
    )


def mode_client(memory: DetectorMemory) -> TestClient:
    """A client of an app that serves only the detector's mode route over `memory`."""
    app = FastAPI()
    mount_mode(app, memory)
    return TestClient(app)


def test_the_mode_route_reads_the_mode_and_sets_it():
    memory = DetectorMemory()
    client = mode_client(memory)

    assert client.get(MODE_ROUTE).json() == {"on": False}
    assert client.put(MODE_ROUTE, json={"on": True}).json() == {"on": True}
    assert memory.saves_uq is True
    assert client.get(MODE_ROUTE).json() == {"on": True}
    assert client.put(MODE_ROUTE, json={"on": False}).json() == {"on": False}
    assert memory.saves_uq is False


def test_the_mode_route_refuses_a_body_without_a_boolean():
    memory = DetectorMemory()

    answer = mode_client(memory).put(MODE_ROUTE, json={"on": "maybe"})

    assert answer.status_code == 422
    assert memory.saves_uq is False


def test_the_detector_classifies_in_the_mode_the_route_set(middleware, ogm):
    memory = DetectorMemory()
    mode_client(memory).put(MODE_ROUTE, json={"on": True})
    values = effective(SUCCESS_PASSING_M4_VALUES)

    reads, _ = classify_once(middleware, "success", values, memory)

    assert reads.uq_reads[0] is True
    screw = change_of(the_one_write(ogm), SCREW).data
    assert str(JMS.hasScrewHeadPositionVariable) in screw


@pytest.mark.parametrize("saves_uq", [False, True])
def test_the_detector_serves_its_mode_route_from_its_start_value(saves_uq):
    middleware = build(
        str(DETECTOR),
        SimpleNamespace(db=None),  # type: ignore[arg-type]
        "127.0.0.1",
        8000,
        reader=SimpleNamespace(read=lambda reference: None),  # type: ignore[arg-type]
        saves_uq=saves_uq,
    )

    answer = TestClient(middleware.app).get(MODE_ROUTE)

    assert answer.json() == {"on": saves_uq}
