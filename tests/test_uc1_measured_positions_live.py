"""Measured positions saved as uncertainty quantifications, on a live GraphDB and a live InfluxDB:
the anomaly detector with the mode "Save measurements as uncertainty quantifications" on, behind
its own middleware instance, with use case 1's shapes loaded.

As in `test_uc1_anomaly_detector_live.py`, the planner's stand-in dispatches an unscrewing
operation, the screwing resource replays a recording and calls the detector, and the detector ends
the operation in one commit. Here the screwing resource is a `:ScrewingResource` with a robot that
holds its position standard deviation, so the detector finds it through the capability that the
operation implements. Each expected position is computed from the recordings and the seed's
values; each literal is read back with its datatype.

**Only on a dataset you can lose.** Each test clears the dataset `--dataset` names.
"""

from __future__ import annotations

import pytest
from _uc1_example import RECORDING_PLACEMENT_VALUES, SUCCESS_PASSING_M4_VALUES
from _uc1_influx import INFLUX_TEST_DATABASE
from _uc1_parties import (
    DETECTOR,
    HOST,
    PLANNER,
    SCREWING_RESOURCE,
    dispatch,
    planner,
    served,
)
from _uc1_run import random_variables
from _wait import eventually
from kapps_ogm import OGM, ClassScope
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from rdflib import Literal

from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.uc1 import anomaly_detector, screwing_resource
from jms_kapps.uc1.anomaly_detector import approach_end_position, attempt_signals
from jms_kapps.uc1.decision_tree import contact_position
from jms_kapps.uc1.effective_values import resolve
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.seed import POSITION_STANDARD_DEVIATION, reset_uc1
from jms_kapps.uc1.timeseries import FrameReader, FrameWriter
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.uncertainty import GMM_LITERAL, read_measurement
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, UQ

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

SCREW = IRI("TestScrew", base=JMS_NS)
ROBOT = IRI("TestRobot", base=JMS_NS)
ATTEMPT_SECONDS = 30.0
_DOUBLE = "http://www.w3.org/2001/XMLSchema#double"

OPERATION_SCOPE = ClassScope.from_property_chains(
    [
        [SVC.hasOperationStatus],
        [SVC.hasExecutionResult],
        [JMS.hasMeasuredApproachEndPosition],
        [JMS.hasApproachEndPositionVariable, UQ.hasDistribution],
    ]
)
SCREW_SCOPE = ClassScope.from_property_chains(
    [
        [JMS.hasMeasuredScrewHeadPosition],
        [JMS.hasScrewHeadPositionVariable, UQ.hasDistribution],
    ]
)


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


def _doubles(subject: IRI, values: dict[IRI, float]) -> str:
    return "".join(
        f'<{subject}> <{prop}> "{value}"^^<{_DOUBLE}> .\n'
        for prop, value in values.items()
    )


@pytest.fixture
def dataset_seeded(dataset: str) -> str:
    """The dataset, cleared, with use case 1's ontology, its uncertainty module and its shapes;
    the screwing resource with its robot and the robot's standard deviation; the detector and the
    planner; `:M4`'s values and one placed Screw."""
    individuals = (
        f"<{SCREWING_RESOURCE}> a <{JMS.ScrewingResource}> ; <{JMS.hasRobot}> <{ROBOT}> .\n"
        f"<{ROBOT}> a <{JMS.Robot}> ; <{JMS.isRobotOf}> <{SCREWING_RESOURCE}> ; "
        f'<{JMS.hasPositionStandardDeviation}> "{POSITION_STANDARD_DEVIATION}"^^<{_DOUBLE}> .\n'
        f"<{DETECTOR}> a <{CFC.Resource}> .\n"
        f"<{PLANNER}> a <{CFC.Resource}> .\n"
        f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        + _doubles(JMS.M4, SUCCESS_PASSING_M4_VALUES)
        + _doubles(SCREW, RECORDING_PLACEMENT_VALUES)
    )
    reset_uc1(connect_store(dataset), individuals=individuals)
    return dataset


def classify_dispatched(dataset: str, influxdb, recording: str) -> IRI:
    """Serve the detector, on, and the screwing resource, which replays `recording`; dispatch an
    unscrewing operation of `SCREW`, and wait until it has ended."""

    def detector(port: int):
        return anomaly_detector.build(
            str(DETECTOR),
            OGM(db=connect_store(dataset)),
            HOST,
            port,
            reader=FrameReader(influxdb),
            saves_uq=True,
        )

    def screwing(port: int):
        return screwing_resource.build(
            str(SCREWING_RESOURCE),
            OGM(db=connect_store(dataset)),
            HOST,
            port,
            writer=FrameWriter(influxdb, database=INFLUX_TEST_DATABASE),
            attempt=screwing_resource.replay_attempt(recording),
        )

    with served(detector), served(screwing):
        operation = dispatch(planner(dataset), SCREW)
        eventually(
            lambda: (
                read(dataset, operation, OPERATION_SCOPE).get(SVC.hasOperationStatus)
                in ([OperationStatus.DONE], [OperationStatus.FAILED])
            ),
            timeout=ATTEMPT_SECONDS,
            interval=0.5,
        )
    return operation


def read(dataset: str, instance: IRI, scope: ClassScope) -> dict:
    node = OGM(db=connect_store(dataset)).fetch(
        instance_iri=instance, class_scope=scope
    )
    return dict(node.data or {})


def the_literal(data: dict, link: IRI) -> tuple[str, tuple[float, float]]:
    """The one variable that `link` names, and the measurement of its one literal."""
    (variable,) = data[link]
    (literal,) = (variable.data or {})[UQ.hasDistribution]
    assert isinstance(literal, Literal)
    assert literal.datatype == GMM_LITERAL
    return str(variable.id), read_measurement(literal)


def seeded_values():
    return resolve(
        SCREW,
        {prop: [value] for prop, value in RECORDING_PLACEMENT_VALUES.items()},
        JMS.M4,
        {prop: [value] for prop, value in SUCCESS_PASSING_M4_VALUES.items()},
    )


VARIANCE = POSITION_STANDARD_DEVIATION**2


def test_a_missing_screw_leaves_its_operation_with_its_end_position_and_its_variable(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded
    end = approach_end_position(attempt_signals(example_frame("missing_screw")))

    operation = classify_dispatched(dataset, influxdb, "missing_screw")

    data = read(dataset, operation, OPERATION_SCOPE)
    assert data[SVC.hasOperationStatus] == [OperationStatus.FAILED]
    assert [str(r) for r in data[SVC.hasExecutionResult]] == ["ANOMALY: MissingScrew"]
    assert data[JMS.hasMeasuredApproachEndPosition] == [end]
    _, measurement = the_literal(data, JMS.hasApproachEndPositionVariable)
    assert measurement == pytest.approx((end, VARIANCE))
    assert (
        read(dataset, SCREW, SCREW_SCOPE).get(JMS.hasMeasuredScrewHeadPosition) is None
    )


def test_an_intact_screw_holds_its_head_position_and_a_second_attempt_replaces_its_literal(
    dataset_seeded, influxdb
):
    dataset = dataset_seeded
    at_contact = contact_position(
        attempt_signals(example_frame("success")), seeded_values()
    )

    first = classify_dispatched(dataset, influxdb, "success")
    after_first = read(dataset, SCREW, SCREW_SCOPE)
    second = classify_dispatched(dataset, influxdb, "success")
    after_second = read(dataset, SCREW, SCREW_SCOPE)

    for operation in (first, second):
        ended = read(dataset, operation, OPERATION_SCOPE)
        assert ended[SVC.hasOperationStatus] == [OperationStatus.DONE]
        assert JMS.hasApproachEndPositionVariable not in ended
    variable, measurement = the_literal(after_first, JMS.hasScrewHeadPositionVariable)
    assert after_first[JMS.hasMeasuredScrewHeadPosition] == [at_contact]
    assert measurement == pytest.approx((at_contact, VARIANCE))
    # The same variable, its literal replaced by the second measurement; nothing is fused, so
    # with the same recording the mean and the variance stay.
    again, measurement = the_literal(after_second, JMS.hasScrewHeadPositionVariable)
    assert again == variable
    assert after_second[JMS.hasMeasuredScrewHeadPosition] == [at_contact]
    assert measurement == pytest.approx((at_contact, VARIANCE))
    assert random_variables(connect_store(dataset)) == 1
