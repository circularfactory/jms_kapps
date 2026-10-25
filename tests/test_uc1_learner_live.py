"""The learner on a live GraphDB and a live InfluxDB: a run of attempts in, a changed `:M4` out.

Each test seeds use case 1's ontology, the example values of `:M4` (see `_uc1_example.py`: none of them is
measured) and one Screw of type `:M4` with its placement values. Then it records attempts as the
screwing resource and the detector will record them: each frame is written to InfluxDB, and each
operation is opened under the middleware's operation scope and ended in one `mw.commit()` with its
Screw, its reference, its outcome and `done`. A middleware stands in for those two participants,
built and never served, because a commit needs only its object-graph mapper. The learner's
middleware is built the same way.

What the detector decides with at its next attempt, and what its next parameter copy
records, is `read_effective_values` of the Screw, so the tests read that.

The tests need both stores. They skip without the store's connection variables, and when no
InfluxDB answers at `INFLUXDB_URL` (without it, at the local service, the fallback). They clear the
dataset that `--dataset` names, and write their frames into the InfluxDB database `Tests`,
whatever the dataset. The local stack is only the fallback.

All UC1 live tests share one InfluxDB database, `Tests` (`_uc1_influx.py`), since InfluxDB 3 Core
allows five databases only.
"""

from __future__ import annotations

import pytest
from _uc1_example import ARBITRARY_PLACEMENT_VALUES, EXAMPLE_M4_VALUES
from _uc1_influx import INFLUX_TEST_DATABASE
from kapps_ogm import OGM, ClassScope
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.operation import operation as operation_scope
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import bindings, connect, reset
from jms_kapps.uc1.effective_values import read_effective_values
from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.learner import Learner
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.seed import uc1_ontology
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

SCREW = IRI("TestScrewOfTypeM4", base=JMS_NS)
THREAD_FAULT = IRI("ThreadFault", base=JMS_NS)

# The screwing resource and the detector, as one stand-in, built and never served. Neither IRI is
# declared, and no Capability is registered.
CELL = IRI("TestUnscrewingCell", base=JMS_NS)
CELL_SERVICE = IRI("TestUnscrewingCellService", base=JMS_NS)
UNSCREWING_CAPABILITY = IRI("TestUnscrewingCapability", base=JMS_NS)

START = 1_758_800_000_000_000_000


def disassembly(*torques: float) -> Frame:
    """An attempt's frame whose Disassembly samples carry `torques`, one a second. The torques that
    the tests give are arbitrary test values, not measured."""
    count = len(torques)
    return Frame(
        times=[START + i * 1_000_000_000 for i in range(count)],
        signals={
            ROBOT_POSITION: [0.0] * count,
            AXIAL_FORCE: [0.0] * count,
            UNSCREWING_TORQUE: list(torques),
        },
        phases=["Disassembly"] * count,
    )


def turtle_values(subject: IRI, values: dict[IRI, float]) -> str:
    return "".join(
        f'<{subject}> <{prop}> "{value}"^^<http://www.w3.org/2001/XMLSchema#double> .\n'
        for prop, value in values.items()
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


@pytest.fixture
def db(dataset):
    """The ontology, `:M4` with its example values, and one Screw of type `:M4` with its placement
    values and no type value. The seed writes Turtle, as a demonstration seed may."""
    db = connect(dataset)
    reset(
        db,
        ontology=uc1_ontology(),
        shapes="",
    )
    db.graph_add(
        f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        + turtle_values(JMS.M4, EXAMPLE_M4_VALUES)
        + turtle_values(SCREW, ARBITRARY_PLACEMENT_VALUES),
        content_type="application/x-turtle",
    )
    return db


@pytest.fixture
def record(db, influxdb):
    """Record one finished attempt on `SCREW`, as the screwing resource and the detector will.

    The attempt's curves are a frame, which is written to InfluxDB first, or a reference to store
    as it is, without a frame behind it."""
    cell = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(CELL),
        service_class=str(CELL_SERVICE),
        ogm=OGM(db=db),
    )
    writer = FrameWriter(influxdb, database=INFLUX_TEST_DATABASE)
    count = 0

    def attempt(curves: Frame | str, outcome: IRI) -> IRI:
        nonlocal count
        count += 1
        operation = IRI(f"TestUnscrewingOperation{count}", base=JMS_NS)
        reference = writer.write(curves) if isinstance(curves, Frame) else curves
        # One write: the store's operation-status shape refuses an Operation that is created
        # without its status.
        with cell.create(
            str(JMS.UnscrewingOperation),
            operation_scope(ClassScope(), capability=str(UNSCREWING_CAPABILITY)),
            instance_iri=str(operation),
        ) as created:
            created.update(
                {
                    str(JMS.concernsScrew): [{"id": str(SCREW)}],
                    str(JMS.hasTimeSeriesReference): [str(reference)],
                    str(JMS.hasDetectionResult): [{"id": str(outcome)}],
                    str(SVC.hasOperationStatus): [OperationStatus.DONE],
                }
            )
        return operation

    return attempt


@pytest.fixture
def learner(db, influxdb) -> Learner:
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(JMS.Learner1),
        service_class=str(JMS.LearnerService),
        ogm=OGM(db=db),
    )
    return Learner(middleware, FrameReader(influxdb))


def max_dynamic_torque_of_the_screw(db) -> float:
    """The maximum dynamic torque the detector decides the Screw's next attempt with."""
    return read_effective_values(OGM(db=db), SCREW).max_dynamic_torque


def test_after_a_run_of_attempts_the_learner_commits_a_changed_threshold_to_m4(
    db, record, learner
) -> None:
    """Two ScrewIntact attempts of the example success recording, whose highest dynamic torque
    is 0.7, and one ThreadFault with 1.9: the placeholder rule learns from the two, and gives 0.84.
    The detector's next decision holds it, and the Screw itself holds no value."""
    assert (
        max_dynamic_torque_of_the_screw(db)
        == EXAMPLE_M4_VALUES[JMS.hasMaxDynamicTorque]
    )
    record(example_frame("success", start=START), JMS.ScrewIntact)
    record(example_frame("success", start=START), JMS.ScrewIntact)
    record(disassembly(0.4, 1.9), THREAD_FAULT)

    learned = learner.learn()

    assert learned == pytest.approx({JMS.M4: 0.84})
    assert max_dynamic_torque_of_the_screw(db) == pytest.approx(0.84)
    assert (
        bindings(
            db,
            f"SELECT ?v WHERE {{ <{SCREW}> <{JMS.hasMaxDynamicTorque}> ?v }}",
        )
        == []
    )


def test_each_run_learns_from_the_attempts_completed_since_the_last(
    db, record, learner
) -> None:
    """A run with no new attempt commits nothing. A new attempt whose dynamic torque rises to 1.0
    raises the maximum to 1.2, and a later one with a lower torque leaves it there."""
    record(example_frame("success", start=START), JMS.ScrewIntact)
    assert learner.learn() == pytest.approx({JMS.M4: 0.84})

    assert learner.learn() == {}

    record(disassembly(0.5, 1.0), JMS.ScrewIntact)
    assert learner.learn() == pytest.approx({JMS.M4: 1.2})
    assert max_dynamic_torque_of_the_screw(db) == pytest.approx(1.2)

    record(disassembly(0.2), JMS.ScrewIntact)
    assert learner.learn() == {}
    assert max_dynamic_torque_of_the_screw(db) == pytest.approx(1.2)


def test_an_attempt_whose_frame_cannot_be_read_is_passed_over_for_good(
    db, record, learner
) -> None:
    """One attempt names a frame that the store does not hold, and one a reference that names no
    frame at all. The learner passes over both, learns from the third, and its next run does not
    trip over them again."""
    record(example_frame("success", start=START), JMS.ScrewIntact)
    record(
        f"influxdb:///{INFLUX_TEST_DATABASE}/unscrewing?frame=never-written"
        "&start=2025-09-25T11%3A33%3A20.000000000Z&stop=2025-09-25T11%3A33%3A26.500000000Z",
        JMS.ScrewIntact,
    )
    record(
        f"influxdb:///{INFLUX_TEST_DATABASE}/unscrewing?frame=no-window",
        JMS.ScrewIntact,
    )

    assert learner.learn() == pytest.approx({JMS.M4: 0.84})
    assert learner.learn() == {}


def test_the_learner_remembers_the_value_each_commit_replaced(
    db, record, learner
) -> None:
    """The run's one query also reads what `:M4` held, which the learner's dashboard box shows:
    first the example 1.5, replaced by 0.84, then the 0.84, raised to 1.2."""
    record(example_frame("success", start=START), JMS.ScrewIntact)
    learner.learn()
    record(disassembly(0.5, 1.0), JMS.ScrewIntact)
    learner.learn()

    changes = learner.memory.state().changes

    assert [change.screw_type for change in changes] == [JMS.M4, JMS.M4]
    assert [change.old for change in changes] == pytest.approx([0.84, 1.5])
    assert [change.new for change in changes] == pytest.approx([1.2, 0.84])
