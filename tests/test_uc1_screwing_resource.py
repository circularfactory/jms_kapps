"""The screwing resource on a live GraphDB and a live InfluxDB: one attempt for each operation.

The planner's stand-in dispatches an unscrewing operation with `mw.issue_operation()`, which notifies the
screwing resource. Its intake runs its unscrewing workflow with the operation. The attempt replays
a recording as a frame into InfluxDB, and one commit leaves the operation `running` with its
time-series reference. Then the screwing resource calls the anomaly detector's classify workflow,
which it finds in the graph by the detector's Classify capability. The detector is a fake here
(see `_uc1_parties.py`), so that a test controls it: it keeps each call, and ends no operation
unless a test asks it to. When the screwing resource cannot reach the detector, it ends the
operation `failed` itself. `test_uc1_anomaly_detector_live.py` serves the real detector.

The tests need both stores. They skip without the store's connection variables, and when no
InfluxDB answers at `INFLUXDB_URL` (without it, at the local service that
`docker compose up -d --wait` starts, the fallback). Each test clears the dataset that
`--dataset` names, and the screwing resource writes its frames into the InfluxDB database
`Tests`, whatever the dataset. InfluxDB keeps those frames.

Changed on 2026-10-05, after Etienne Hoffmann decided that the local stack is only the fallback
(the ticket "jms_kapps' demos run on the configured live GraphDB (422)" in
kapps_semantic_middleware); the parenthesis read "(default: the local service that
`docker compose up -d --wait` starts)".

Changed on 2026-10-15 (jms_kapps 103), after Sören Weindel decided on 2026-10-12 that all UC1 live
tests share one InfluxDB database, `Tests` (`_uc1_influx.py`): each test wrote its frames into a
database of the dataset's name, which filled the five that InfluxDB 3 Core allows.
"""

from __future__ import annotations

import threading

import pytest
from _uc1_influx import INFLUX_TEST_DATABASE
from _uc1_parties import (
    HOST,
    SCREWING_RESOURCE,
    FakeDetector,
    dispatch,
    planner,
    seed_parties,
    served,
)
from _wait import eventually
from kapps_ogm import OGM, ClassScope, Update
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.common.triplestore import read_asset, reset
from jms_kapps.uc1 import classify_call
from jms_kapps.uc1.replay import mock_frame
from jms_kapps.uc1.screwing_resource import (
    NOT_REACHED,
    attempt_failed_record,
    build,
    ended_meanwhile_record,
    left_alone_record,
)
from jms_kapps.uc1.timeseries import FrameReader, FrameWriter
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

SCREW = IRI("TestScrew", base=JMS_NS)

# What a test reads back of an operation after the attempt.
READ_BACK_SCOPE = ClassScope.from_property_chains(
    [[SVC.operationStatus], [SVC.executionResult], [JMS.hasTimeSeriesReference]]
)

# How long an attempt may take, from the dispatch to the call of the detector.
ATTEMPT_SECONDS = 30.0


@pytest.fixture(scope="module")
def influxdb():
    client = connect_influxdb()
    if not client.available():
        client.close()
        pytest.skip(
            "no InfluxDB answers; start the local one with `docker compose up -d --wait`"
        )
    yield client
    client.close()


@pytest.fixture
def dataset_with_screw(dataset: str) -> str:
    """The dataset, cleared, with use case 1's ontology, the parties and one Screw."""
    db = connect_store(dataset)
    reset(
        db,
        ontology=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        shapes="",
    )
    db.graph_add(seed_parties([SCREW]), content_type="application/x-turtle")
    return dataset


def screwing_resource(dataset: str, influxdb, **options):
    """Builds the screwing resource for a port, on `dataset` and the shared InfluxDB database."""

    def build_for_port(port: int):
        return build(
            str(SCREWING_RESOURCE),
            OGM(db=connect_store(dataset)),
            HOST,
            port,
            writer=FrameWriter(influxdb, database=INFLUX_TEST_DATABASE),
            **options,
        )

    return build_for_port


def read_operation(dataset: str, operation: IRI) -> dict[IRI, list]:
    node = OGM(db=connect_store(dataset)).fetch(
        instance_iri=operation, class_scope=READ_BACK_SCOPE
    )
    return dict(node.data or {})


def test_a_notification_from_the_planner_makes_one_attempt_and_calls_the_detector(
    dataset_with_screw, influxdb
):
    dataset = dataset_with_screw
    detector = FakeDetector(dataset)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(lambda: detector.calls == [str(operation)], timeout=ATTEMPT_SECONDS)

    data = read_operation(dataset, operation)
    assert data[SVC.operationStatus] == [OperationStatus.RUNNING]
    (reference,) = data[JMS.hasTimeSeriesReference]
    frame = FrameReader(influxdb).read(str(reference))
    recording = mock_frame("success")
    assert frame.signals == recording.signals
    assert frame.phases == recording.phases
    assert frame.source == recording.source


def test_an_attempt_that_fails_in_its_body_ends_the_operation_failed(
    dataset_with_screw, influxdb, caplog
):
    dataset = dataset_with_screw
    detector = FakeDetector(dataset)

    def slipping(screw: IRI):
        raise RuntimeError("the screwdriver slipped off the head")

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb, attempt=slipping)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: logged(caplog, attempt_failed_record(SCREWING_RESOURCE, operation)),
            timeout=ATTEMPT_SECONDS,
        )

    data = read_operation(dataset, operation)
    assert data[SVC.operationStatus] == [OperationStatus.FAILED]
    (result,) = data[SVC.executionResult]
    assert "the screwdriver slipped off the head" in str(result)
    assert JMS.hasTimeSeriesReference not in data
    assert detector.calls == []


def test_a_second_notification_of_an_operation_makes_no_second_attempt(
    dataset_with_screw, influxdb, caplog
):
    dataset = dataset_with_screw
    detector = FakeDetector(dataset)
    dispatcher = planner(dataset)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        operation = dispatch(dispatcher, SCREW)
        eventually(lambda: detector.calls == [str(operation)], timeout=ATTEMPT_SECONDS)
        (first,) = read_operation(dataset, operation)[JMS.hasTimeSeriesReference]

        dispatcher.notify(operation=operation, resource=SCREWING_RESOURCE)
        eventually(
            lambda: logged(
                caplog,
                left_alone_record(
                    SCREWING_RESOURCE, operation, OperationStatus.RUNNING
                ),
            ),
            timeout=ATTEMPT_SECONDS,
        )

    data = read_operation(dataset, operation)
    assert data[SVC.operationStatus] == [OperationStatus.RUNNING]
    assert data[JMS.hasTimeSeriesReference] == [first]
    assert detector.calls == [str(operation)]


def assert_failed_unreached_with_its_curves(
    dataset: str, operation: IRI, influxdb
) -> str:
    """The operation is `failed` because the detector was not reached, and still holds the
    reference to the attempt's curves, which read back as the recording. Gives the reason."""
    data = read_operation(dataset, operation)
    assert data[SVC.operationStatus] == [OperationStatus.FAILED]
    (result,) = data[SVC.executionResult]
    assert str(result).startswith(NOT_REACHED)
    (reference,) = data[JMS.hasTimeSeriesReference]
    assert (
        FrameReader(influxdb).read(str(reference)).signals
        == mock_frame("success").signals
    )
    return str(result)


def is_failed(dataset: str, operation: IRI) -> bool:
    status = read_operation(dataset, operation).get(SVC.operationStatus)
    return status == [OperationStatus.FAILED]


def test_an_attempt_without_a_live_detector_ends_the_operation_failed(
    dataset_with_screw, influxdb
):
    dataset = dataset_with_screw

    with served(screwing_resource(dataset, influxdb)):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: is_failed(dataset, operation),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )

    reason = assert_failed_unreached_with_its_curves(dataset, operation, influxdb)
    assert "ClassifyCapability" in reason


def test_an_attempt_whose_detector_refuses_the_call_ends_the_operation_failed(
    dataset_with_screw, influxdb
):
    dataset = dataset_with_screw

    def refuse(middleware: SemanticMiddleware, operation: IRI) -> None:
        # The framework answers a RuntimeError of a workflow with 409 Conflict.
        raise RuntimeError("the detector refuses the call")

    detector = FakeDetector(dataset, on_call=refuse)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: is_failed(dataset, operation),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )

    reason = assert_failed_unreached_with_its_curves(dataset, operation, influxdb)
    assert "409" in reason
    assert detector.calls == [str(operation)]


def test_an_attempt_whose_detector_cannot_be_looked_up_ends_the_operation_failed(
    dataset_with_screw, influxdb, monkeypatch
):
    dataset = dataset_with_screw
    detector = FakeDetector(dataset)

    def unreadable(db) -> str | None:
        raise RuntimeError("the graph did not answer the lookup")

    monkeypatch.setattr(classify_call, "classify_endpoint", unreadable)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: is_failed(dataset, operation),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )

    reason = assert_failed_unreached_with_its_curves(dataset, operation, influxdb)
    assert "the graph did not answer the lookup" in reason
    assert detector.calls == []


def test_an_attempt_whose_detector_answers_too_late_ends_the_operation_failed(
    dataset_with_screw, influxdb, monkeypatch
):
    dataset = dataset_with_screw
    monkeypatch.setattr(classify_call, "CALL_TIMEOUT_SECONDS", 1.0)
    answer = threading.Event()

    def answer_late(middleware: SemanticMiddleware, operation: IRI) -> None:
        answer.wait(timeout=ATTEMPT_SECONDS)

    detector = FakeDetector(dataset, on_call=answer_late)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        try:
            operation = dispatch(planner(dataset), SCREW)
            eventually(
                lambda: is_failed(dataset, operation),
                timeout=ATTEMPT_SECONDS,
                interval=0.5,
            )
        finally:
            answer.set()

    reason = assert_failed_unreached_with_its_curves(dataset, operation, influxdb)
    assert "within 1 s" in reason
    assert detector.calls == [str(operation)]


def test_a_detector_that_ended_the_operation_keeps_its_end_when_the_call_fails(
    dataset_with_screw, influxdb, caplog
):
    dataset = dataset_with_screw

    def end_then_refuse(middleware: SemanticMiddleware, operation: IRI) -> None:
        done = Update(
            instance_iri=operation,
            data={str(SVC.operationStatus): [OperationStatus.DONE]},
        )
        with middleware.commit(entry=[done]):
            pass
        raise RuntimeError("the detector ended the operation, then failed to answer")

    detector = FakeDetector(dataset, on_call=end_then_refuse)

    with (
        served(detector.build),
        served(screwing_resource(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: logged(
                caplog,
                ended_meanwhile_record(
                    SCREWING_RESOURCE, operation, OperationStatus.DONE
                ),
            ),
            timeout=ATTEMPT_SECONDS,
        )

    data = read_operation(dataset, operation)
    assert data[SVC.operationStatus] == [OperationStatus.DONE]
    assert SVC.executionResult not in data
    assert len(data[JMS.hasTimeSeriesReference]) == 1


def logged(caplog, record: str) -> bool:
    """Whether the screwing resource has logged `record`, whole."""
    return any(
        entry.name == "jms_kapps.uc1.screwing_resource" and entry.getMessage() == record
        for entry in caplog.records
    )
