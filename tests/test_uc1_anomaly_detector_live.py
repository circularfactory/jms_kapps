"""The anomaly detector on a live GraphDB and a live InfluxDB, behind its own middleware instance.

The planner's stand-in dispatches an unscrewing operation, the screwing resource replays a
recording as the attempt's frame and calls the detector's classify workflow, which it finds by the
detector's Classify capability. The detector classifies the attempt and ends the operation in one
commit. Each party is served in the test's own process (see `_uc1_parties.py`), so every call goes
over REST and every write goes through a middleware into the store.

The values are example values (see `_uc1_example.py`). `:M4` holds `SUCCESS_PASSING_M4_VALUES`, and each placed
Screw holds `RECORDING_PLACEMENT_VALUES`, so the frame replayed from `success` reaches
ScrewIntact.

The tests need both stores. They skip without the store's connection variables, and when no
InfluxDB answers at `INFLUXDB_URL` (default: the local service that `docker compose up -d --wait`
starts). Each test clears the dataset that `--dataset` names, and the screwing resource
writes its frames into the InfluxDB database `Tests`, whatever the dataset. InfluxDB keeps those
frames.

All UC1 live tests share one InfluxDB database, `Tests` (`_uc1_influx.py`), since InfluxDB 3 Core
allows five databases only.
"""

from __future__ import annotations

import logging
import time
import uuid

import pytest
from _uc1_example import RECORDING_PLACEMENT_VALUES, SUCCESS_PASSING_M4_VALUES
from _uc1_influx import INFLUX_TEST_DATABASE
from _uc1_parties import (
    DETECTOR,
    HOST,
    SCREWING_RESOURCE,
    dispatch,
    planner,
    seed_parties,
    served,
)
from _wait import eventually
from kapps_ogm import OGM, ClassScope, Update
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import bindings, read_asset, reset
from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.uc1 import anomaly_detector, classify_call, screwing_resource
from jms_kapps.uc1.anomaly_detector import NOT_CLASSIFIED, left_as_it_is_record
from jms_kapps.uc1.classify_call import call_detector
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.screwing_resource import NOT_REACHED
from jms_kapps.uc1.timeseries import (
    TABLE,
    FrameReader,
    FrameReference,
    FrameWriter,
)
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PLACEMENT, TYPE_LEVEL

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

SCREW = IRI("TestScrew", base=JMS_NS)
UNPLACED_SCREW = IRI("TestScrewWithoutPlacement", base=JMS_NS)
TEN = (*TYPE_LEVEL, *PLACEMENT)

# What a test reads back of an operation once the detector has ended it.
READ_BACK_SCOPE = ClassScope.from_property_chains(
    [
        [SVC.hasOperationStatus],
        [SVC.hasExecutionResult],
        [JMS.hasDetectionResult],
        [JMS.hasMeasuredApproachEndPosition],
        [JMS.hasTimeSeriesReference],
        *([JMS.classifiedWith, prop] for prop in TEN),
    ]
)

# How long an attempt may take, from the dispatch to the end of its operation.
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


def turtle_values(subject: IRI, values: dict[IRI, float]) -> str:
    return "".join(
        f'<{subject}> <{prop}> "{value}"^^<http://www.w3.org/2001/XMLSchema#double> .\n'
        for prop, value in values.items()
    )


@pytest.fixture
def dataset_seeded(dataset: str) -> str:
    """The dataset, cleared, with use case 1's ontology, the three parties, `:M4`'s values, a
    placed Screw and a Screw without placement values. A demonstration seed writes it directly,
    before any party starts."""
    db = connect_store(dataset)
    reset(
        db,
        ontology=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        shapes="",
    )
    db.graph_add(
        seed_parties([SCREW, UNPLACED_SCREW])
        + turtle_values(JMS.M4, SUCCESS_PASSING_M4_VALUES)
        + turtle_values(SCREW, RECORDING_PLACEMENT_VALUES),
        content_type="application/x-turtle",
    )
    return dataset


def detector(dataset: str, influxdb, reader: FrameReader | None = None):
    """Builds the anomaly detector for a port, on `dataset`."""

    def build_for_port(port: int):
        return anomaly_detector.build(
            str(DETECTOR),
            OGM(db=connect_store(dataset)),
            HOST,
            port,
            reader=reader if reader is not None else FrameReader(influxdb),
        )

    return build_for_port


def screwing(dataset: str, influxdb, writer: FrameWriter | None = None):
    """Builds the screwing resource for a port, on `dataset` and the shared InfluxDB database."""

    def build_for_port(port: int):
        return screwing_resource.build(
            str(SCREWING_RESOURCE),
            OGM(db=connect_store(dataset)),
            HOST,
            port,
            writer=writer
            if writer is not None
            else FrameWriter(influxdb, database=INFLUX_TEST_DATABASE),
        )

    return build_for_port


def read_operation(dataset: str, operation: IRI) -> dict[IRI, list]:
    node = OGM(db=connect_store(dataset)).fetch(
        instance_iri=operation, class_scope=READ_BACK_SCOPE
    )
    return dict(node.data or {})


def has_ended(dataset: str, operation: IRI) -> bool:
    status = read_operation(dataset, operation).get(SVC.hasOperationStatus)
    return status in ([OperationStatus.DONE], [OperationStatus.FAILED])


def snapshot_values(data: dict[IRI, list]) -> dict[IRI, list]:
    """The ten values of the one parameter copy that the operation links to."""
    (snapshot,) = data[JMS.classifiedWith]
    return {prop: (snapshot.data or {}).get(prop) for prop in TEN}


def snapshots_in(dataset: str) -> int:
    (row,) = bindings(
        connect_store(dataset),
        f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s a <{JMS.ParameterCopy}> }}",
    )
    return int(row["n"])


def classify_dispatched(dataset: str, influxdb, screw: IRI) -> IRI:
    """Serve the detector and the screwing resource, dispatch an unscrewing operation of `screw`,
    and wait until it has ended."""
    with (
        served(detector(dataset, influxdb)),
        served(screwing(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), screw)
        eventually(
            lambda: has_ended(dataset, operation),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )
    return operation


def test_a_dispatched_attempt_ends_done_with_its_outcome_and_its_decision_snapshot(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded

    operation = classify_dispatched(dataset, influxdb, SCREW)

    data = read_operation(dataset, operation)
    assert data[SVC.hasOperationStatus] == [OperationStatus.DONE]
    assert [str(o.id) for o in data[JMS.hasDetectionResult]] == [
        str(IRI("ScrewIntact", base=JMS_NS))
    ]
    assert snapshot_values(data) == {
        prop: [value]
        for prop, value in {
            **SUCCESS_PASSING_M4_VALUES,
            **RECORDING_PLACEMENT_VALUES,
        }.items()
    }
    assert JMS.hasMeasuredApproachEndPosition not in data
    assert SVC.hasExecutionResult not in data
    assert len(data[JMS.hasTimeSeriesReference]) == 1


def test_a_threshold_changed_between_two_attempts_is_used_on_the_next(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded
    first = classify_dispatched(dataset, influxdb, SCREW)

    # As the learner commits a Screw type's value: through a middleware, in one commit. The
    # approach of the frame of `success` reaches 0.5, not 0.6. So it touches nothing, and it
    # passes the depth limit at its last Approach sample, 0.0.
    with planner(dataset).commit(
        entry=[
            Update(
                instance_iri=JMS.M4,
                data={str(JMS.hasMinApproachAxialForce): [0.6]},
            )
        ]
    ):
        pass
    second = classify_dispatched(dataset, influxdb, SCREW)

    before, after = (read_operation(dataset, op) for op in (first, second))
    assert snapshot_values(before)[JMS.hasMinApproachAxialForce] == [0.5]
    assert snapshot_values(after)[JMS.hasMinApproachAxialForce] == [0.6]
    # The changed threshold gives the second attempt a failure outcome, which ends it failed.
    assert after[SVC.hasOperationStatus] == [OperationStatus.FAILED]
    assert [str(r) for r in after[SVC.hasExecutionResult]] == ["ANOMALY: MissingScrew"]
    assert [str(o.id) for o in after[JMS.hasDetectionResult]] == [
        str(IRI("MissingScrew", base=JMS_NS))
    ]
    assert after[JMS.hasMeasuredApproachEndPosition] == [0.0]


def assert_failed_and_nothing_else(dataset: str, operation: IRI) -> str:
    """The operation is `failed` with why, and holds no outcome, no snapshot and no measured
    position; the dataset holds no parameter copy. Gives the why."""
    data = read_operation(dataset, operation)
    assert data[SVC.hasOperationStatus] == [OperationStatus.FAILED]
    assert JMS.hasDetectionResult not in data
    assert JMS.classifiedWith not in data
    assert JMS.hasMeasuredApproachEndPosition not in data
    assert snapshots_in(dataset) == 0
    (why,) = data[SVC.hasExecutionResult]
    return str(why)


def test_a_screw_without_placement_values_ends_its_operation_failed_and_nothing_else(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded

    operation = classify_dispatched(dataset, influxdb, UNPLACED_SCREW)

    why = assert_failed_and_nothing_else(dataset, operation)
    assert why.startswith(NOT_CLASSIFIED)
    assert "hasMinApproachPosition: the Screw holds no value" in why


class LosingWriter(FrameWriter):
    """Writes the frame, and gives back a reference to a frame of another id, which the store
    lacks. Its write lays the store out, so the read finds the table and no row in it."""

    def write(self, frame: Frame) -> FrameReference:
        written = super().write(frame)
        return FrameReference(
            database=written.database,
            table=TABLE,
            frame=uuid.uuid4().hex,
            start=written.start,
            stop=written.stop,
        )


def test_a_frame_that_the_store_lacks_ends_the_operation_failed_with_why(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded
    lost = LosingWriter(influxdb, database=INFLUX_TEST_DATABASE)

    with (
        served(detector(dataset, influxdb)),
        served(screwing(dataset, influxdb, writer=lost)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: has_ended(dataset, operation),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )

    why = assert_failed_and_nothing_else(dataset, operation)
    assert why.startswith(NOT_CLASSIFIED)
    assert "the store holds no row of" in why


def test_an_operation_that_is_not_running_is_left_as_it_is(
    dataset_seeded, influxdb, caplog
):
    dataset = dataset_seeded
    operation = IRI("TestEndedOperation", base=JMS_NS)
    ended = f"{NOT_REACHED}its classify workflow did not answer"
    connect_store(dataset).graph_add(
        f"<{operation}> a <{JMS.UnscrewingOperation}> ; <{JMS.concernsScrew}> <{SCREW}> ; "
        f'<{SVC.hasOperationStatus}> "{OperationStatus.FAILED}" ; '
        f'<{SVC.hasExecutionResult}> "{ended}" .\n',
        content_type="application/x-turtle",
    )

    with caplog.at_level(logging.INFO), served(detector(dataset, influxdb)):
        call_detector(connect_store(dataset), operation)

    data = read_operation(dataset, operation)
    assert data[SVC.hasOperationStatus] == [OperationStatus.FAILED]
    assert [str(r) for r in data[SVC.hasExecutionResult]] == [ended]
    assert JMS.hasDetectionResult not in data
    assert snapshots_in(dataset) == 0
    assert left_as_it_is_record(
        DETECTOR, operation, OperationStatus.FAILED
    ) in detector_log(caplog)


class SlowReader(FrameReader):
    """Reads a frame only after `seconds`, as a detector that takes too long."""

    def __init__(self, influxdb, seconds: float) -> None:
        super().__init__(influxdb)
        self.seconds = seconds

    def read(self, reference):
        time.sleep(self.seconds)
        return super().read(reference)


def test_an_operation_that_the_screwing_resource_ended_meanwhile_is_left_as_it_is(
    dataset_seeded, influxdb, caplog, monkeypatch
):
    """The screwing resource stops waiting for the detector's answer, and ends the operation
    `failed` itself. The detector, still classifying, finds it ended, and leaves it."""
    dataset = dataset_seeded
    monkeypatch.setattr(classify_call, "CALL_TIMEOUT_SECONDS", 1.0)
    slow = SlowReader(influxdb, seconds=5.0)

    with (
        caplog.at_level(logging.INFO),
        served(detector(dataset, influxdb, reader=slow)),
        served(screwing(dataset, influxdb)),
    ):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: (
                left_as_it_is_record(DETECTOR, operation, OperationStatus.FAILED)
                in detector_log(caplog)
            ),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )

    why = assert_failed_and_nothing_else(dataset, operation)
    assert why.startswith(NOT_REACHED)
    assert "within 1 s" in why


def detector_log(caplog) -> list[str]:
    return [
        entry.getMessage()
        for entry in caplog.records
        if entry.name == "jms_kapps.uc1.anomaly_detector"
    ]
