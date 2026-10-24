"""Use case 1's paper claims of §5.1 on one run of `jms-uc1`, on a live GraphDB and a live
InfluxDB.

One run serves every test of the module. `jms-uc1` seeds the dataset and starts the planner, the
screwing resource, the anomaly detector and the learner, as a person starts them. The run presses
Unscrew on the planner for Screw1, waits until its operation is `done`, waits until the learner has
committed a new `:M4` value, then presses Unscrew for Screw2 and waits until that operation has
ended: `failed`, because its outcome, MissingScrew, is a failure outcome. The run records
what it saw on the way; each test reads the record, the graph, the
time-series store, or the run's terminal output, and asserts its claim. Claim 13 is proved by a
scan of the source (`test_uc1_writes.py`).

**Only on a dataset you can lose.** The run clears the dataset `--dataset` names, as every graphdb
test does. On a shared store never `Tests`, which every library's live suite shares, nor
`jms-kapps`. On the local stack `jms-kapps` is its only dataset: the run clears it, and the next
demo seeds it again.
The frames go into InfluxDB's database `jms-kapps`, the runtime's own, so the run adds no
database. It needs the local stack (`docker compose up -d --wait`). It runs on POSIX: it starts
`jms-uc1` in a session of its own and stops it with Ctrl+C to that session, as a terminal does.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from _ps import children_of, shown_processes
from _uc1_run import (
    POLL_SECONDS,
    await_all_live,
    await_ended,
    m4_torque,
    operations,
    planner_address,
    press,
    started_uc1,
)
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.services import Service, services
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.anomaly_detector import outcome_iri
from jms_kapps.uc1.classify_call import ClassifyRequest, classify_endpoint
from jms_kapps.uc1.effective_values import PROPERTY_OF_FIELD
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.learner import placeholder_max_dynamic_torque
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.screwing_resource import RESOURCE as SCREWING_RESOURCE
from jms_kapps.uc1.seed import M4_VALUES, PLACEMENT_VALUES, RECORDING_OF, SCREW, SCREW_2
from jms_kapps.uc1.timeseries import FrameReader
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, PROV

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.posix(
        reason="starts jms-uc1 in a session of its own and signals its process group"
    ),
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# How long the learner may take, from the first operation's `done` to its new `:M4` value. It
# runs every second.
LEARNED_WITHIN_SECONDS = 30.0

# The ten values of a decision, by property.
TEN = tuple(PROPERTY_OF_FIELD.values())

# The resource of each participant, and the class of the workflow it registers, if any.
WORKFLOW_OF = {
    DETECTOR: JMS.ClassifyWorkflow,
    SCREWING_RESOURCE: JMS.UnscrewingWorkflow,
    JMS.Planner1: JMS.StartUnscrewingWorkflow,
}
PARTICIPANTS = {*WORKFLOW_OF, JMS.Learner1}


@dataclass(frozen=True)
class Loop:
    """What the run saw: the five processes' command lines, the live services, `:M4`'s max
    dynamic torque as seeded and as the learner refined it, and how many operations the graph held
    when the learner's value appeared. `log` is the run's terminal output."""

    db: TripleStore
    log: Path
    children: list[str]
    services: list[Service]
    seeded: float
    learned: float
    operations_when_learned: int


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


@pytest.fixture(scope="module")
def loop(dataset, influxdb, tmp_path_factory) -> Iterator[Loop]:
    """One run of `jms-uc1` through the loop: Screw1, the learner, Screw2. It stays up until the
    module's tests are done, then Ctrl+C stops it."""
    db = connect(dataset)
    with started_uc1(dataset, tmp_path_factory.mktemp("uc1")) as (run, log, page_url):
        await_all_live(run, log, page_url)
        children = children_of(run.pid, shown_processes())
        live = services(db, workflows=list(WORKFLOW_OF.values()))
        planner = planner_address(page_url)
        seeded = m4_torque(db)

        press(planner, SCREW)
        await_ended(db, 1)
        eventually(
            lambda: m4_torque(db) != seeded,
            timeout=LEARNED_WITHIN_SECONDS,
            interval=POLL_SECONDS,
        )
        learned = m4_torque(db)
        operations_when_learned = len(operations(db))

        press(planner, SCREW_2)
        await_ended(db, 2)

        yield Loop(
            db=db,
            log=log,
            children=children,
            services=live,
            seeded=seeded,
            learned=learned,
            operations_when_learned=operations_when_learned,
        )
        stop(run)


def stop(run: subprocess.Popen) -> None:
    """Ctrl+C to the run's session, as a terminal sends it."""
    os.killpg(run.pid, signal.SIGINT)
    run.wait(timeout=60)


def operation_of(db: TripleStore, screw: IRI) -> dict:
    """The one operation of `screw`, as the graph holds it: its IRI, its issuer, the resource of
    the service whose workflow realizes the Capability it implements, its status, result, outcome
    and time-series reference, and the agent of the classification that generated its snapshot."""
    rows = bindings(
        db,
        f"""
        SELECT ?op ?issuer ?taker ?status ?result ?outcome ?reference ?agent WHERE {{
            ?op a <{JMS.UnscrewingOperation}> ;
                <{JMS.concernsScrew}> <{screw}> .
            OPTIONAL {{ ?op <{SVC.issuedBy}> ?issuer }}
            OPTIONAL {{
                ?op <{CFC.implementsCapability}> ?capability .
                ?capability a <{JMS.UnscrewingCapability}> ;
                            <{SVC.realizedByWorkflow}> ?workflow .
                ?workflow <{SVC.isWorkflowOf}> ?service .
                ?service <{SVC.isServiceOf}> ?taker .
            }}
            OPTIONAL {{ ?op <{SVC.hasOperationStatus}> ?status }}
            OPTIONAL {{ ?op <{SVC.hasExecutionResult}> ?result }}
            OPTIONAL {{ ?op <{JMS.hasDetectionResult}> ?outcome }}
            OPTIONAL {{ ?op <{JMS.hasTimeSeriesReference}> ?reference }}
            OPTIONAL {{
                ?op <{JMS.classifiedWith}> ?snapshot .
                ?snapshot <{PROV.wasGeneratedBy}> ?classification .
                ?classification <{PROV.wasAssociatedWith}> ?agent .
            }}
        }}
        """,
    )
    assert rows, f"the graph holds no operation of {screw}"
    assert len({str(row["op"]) for row in rows}) == 1, rows
    return {
        name: sorted({str(row[name]) for row in rows if row.get(name) is not None})
        for name in (
            "op",
            "issuer",
            "taker",
            "status",
            "result",
            "outcome",
            "reference",
            "agent",
        )
    }


def ten_values(db: TripleStore, pattern: str) -> list[tuple[IRI, float]]:
    """Each value of the ten that `?holder` holds, by property; `pattern` binds `?holder`."""
    properties = " ".join(f"<{prop}>" for prop in TEN)
    rows = bindings(
        db,
        f"""
        SELECT ?property ?value WHERE {{
            {pattern}
            VALUES ?property {{ {properties} }}
            ?holder ?property ?value .
        }}
        """,
    )
    return [(IRI(str(row["property"])), float(row["value"])) for row in rows]


def snapshot_of(db: TripleStore, screw: IRI) -> dict[IRI, list[float]]:
    """The ten values of the parameter copy of `screw`'s operation, each as a list of the values
    it holds."""
    found: dict[IRI, list[float]] = {}
    for prop, value in ten_values(
        db,
        f"""
            ?op a <{JMS.UnscrewingOperation}> ;
                <{JMS.concernsScrew}> <{screw}> ;
                <{JMS.classifiedWith}> ?holder .
        """,
    ):
        found.setdefault(prop, []).append(value)
    return found


def type_values(db: TripleStore) -> dict[IRI, float]:
    """The values that `:M4` holds of the ten, each as the one value it holds."""
    return dict(ten_values(db, f"VALUES ?holder {{ <{JMS.M4}> }}"))


def frame_of(db: TripleStore, influxdb, screw: IRI) -> Frame:
    """The frame of `screw`'s attempt, read through its operation's reference."""
    (reference,) = operation_of(db, screw)["reference"]
    return FrameReader(influxdb).read(reference)


def effective_value_reads(log: Path, screw: IRI) -> list[str]:
    """The lines of the run's output in which the detector reads `screw`'s effective values
    through the object-graph mapper."""
    marker = f"Anomaly detector reads {screw.fragment} with EFFECTIVE_VALUES_SCOPE"
    return [line for line in log.read_text().splitlines() if marker in line]


@pytest.mark.paper_claim
def test_the_participants_coordinate_through_the_graph(loop):
    """Paper claim 10 (§5.1), read as: coordination is state and data.
    Every state change, handover and result of the loop is in the graph, and the participants find
    each other there: the five processes are started with no address of another, each live service
    and its workflow is in the graph, the planner hands each operation to the service that
    realizes the Capability it implements, and the screwing resource finds the detector by its
    Classify capability. The screwing resource's call of the detector names the operation and
    nothing else; the other two calls, the middleware's notifications of the screwing resource and
    of the planner, are the middleware's own. The fifth process, the robot controller stand-in, is
    a device: it has no service, and its command line names its broker, not a service's address."""
    # Started as `jms-uc1` starts them: five processes, none told where another one is.
    assert len(loop.children) == 5, loop.children
    assert all("-m jms_kapps.uc1." in child for child in loop.children), loop.children
    # No command line names a service's address. The stand-in's names the Screws it replays, by
    # IRI, which is not an address of anything: it is left out of this check and holds no `http`
    # address of a service below.
    middleware_children = [
        child for child in loop.children if "jms_kapps.uc1.robot_controller" not in child
    ]
    assert len(middleware_children) == 4, loop.children
    assert not [child for child in middleware_children if "://" in child], loop.children

    # They find each other in the graph: each is live there, with its workflow's endpoint.
    by_resource = {service.resource: service for service in loop.services}
    assert set(by_resource) == PARTICIPANTS, by_resource
    for resource, workflow in WORKFLOW_OF.items():
        assert workflow in by_resource[resource].workflows, by_resource[resource]
    assert (
        classify_endpoint(loop.db)
        == by_resource[DETECTOR].workflows[JMS.ClassifyWorkflow]
    )
    addresses = [str(service.address) for service in loop.services]
    assert not [
        child
        for child in loop.children
        for address in addresses
        if address.split("://")[-1].rstrip("/") in child
    ], (loop.children, addresses)

    # Each operation's handovers and results are in the graph. Screw1's intact screw ends its
    # operation done; Screw2's MissingScrew is a failure outcome, which ends it failed and names it.
    for screw, status, outcome, result in (
        (SCREW, OperationStatus.DONE, "ScrewIntact", []),
        (SCREW_2, OperationStatus.FAILED, "MissingScrew", ["ANOMALY: MissingScrew"]),
    ):
        operation = operation_of(loop.db, screw)
        assert operation["issuer"] == [str(JMS.Planner1)], operation
        assert operation["taker"] == [str(SCREWING_RESOURCE)], operation
        assert operation["status"] == [status], operation
        assert operation["result"] == result, operation
        assert len(operation["reference"]) == 1, operation
        assert operation["outcome"] == [str(outcome_iri(outcome))], operation
        assert operation["agent"] == [str(DETECTOR)], operation

    # The detector's call names the operation and nothing else.
    assert set(ClassifyRequest.model_fields) == {"operation"}


@pytest.mark.paper_claim
def test_the_detector_reads_the_screw_types_thresholds_through_the_mapper_at_every_attempt(
    loop,
):
    """Paper claim 11 (§5.1): at each attempt the detector reads the Screw's effective values
    afresh, through the object-graph mapper, from the Screw and through its Screw type, `:M4`. Its
    terminal line says so once for each attempt, and each parameter copy holds `:M4`'s
    thresholds as they were at that attempt: the seeded value for Screw1, and for Screw2 the value
    the learner committed in between."""
    for screw in (SCREW, SCREW_2):
        eventually(
            lambda screw=screw: len(effective_value_reads(loop.log, screw)) == 1,
            timeout=10,
            interval=POLL_SECONDS,
        )
        (line,) = effective_value_reads(loop.log, screw)
        assert "hasScrewType" in line, line

    first, second = (snapshot_of(loop.db, screw) for screw in (SCREW, SCREW_2))
    assert first == {
        prop: [value] for prop, value in {**M4_VALUES, **PLACEMENT_VALUES}.items()
    }, first
    assert first[JMS.hasMaxDynamicTorque] == [loop.seeded], first
    assert second[JMS.hasMaxDynamicTorque] == [loop.learned], second
    held = type_values(loop.db)
    assert held[JMS.hasMaxDynamicTorque] == loop.learned
    assert second == {
        prop: [value] for prop, value in {**held, **PLACEMENT_VALUES}.items()
    }, second


@pytest.mark.paper_claim
def test_the_learner_commits_a_value_from_completed_operations_and_the_detector_uses_it_next(
    loop, influxdb
):
    """Paper claim 12 (§5.1): the learner, which runs every second on its own, finds the completed operation in the graph, reads its curves, and commits a new maximum
    dynamic torque to `:M4`; the next attempt's parameter copy holds it. The value is the
    placeholder rule's over the first attempt's frame: the rule is a placeholder, not the
    paper's regression."""
    assert loop.operations_when_learned == 1
    assert loop.learned != loop.seeded
    assert loop.learned == pytest.approx(
        placeholder_max_dynamic_torque([frame_of(loop.db, influxdb, SCREW)])
    )
    assert snapshot_of(loop.db, SCREW_2)[JMS.hasMaxDynamicTorque] == [loop.learned]


@pytest.mark.paper_claim
def test_the_sensor_channels_are_a_replay_of_recorded_series(loop, influxdb):
    """Paper claim 14 (§5.1): the curves of each attempt are a replay of a recorded series, the
    example recording the demo names for the Screw. The frame that the operation's reference
    names in the time-series store holds the recording's samples, at the recording's spacing,
    with its phases, and says which recording it replays."""
    for screw in (SCREW, SCREW_2):
        (reference,) = operation_of(loop.db, screw)["reference"]
        assert reference.startswith("influxdb:///jms-kapps/"), reference
        frame = frame_of(loop.db, influxdb, screw)
        recording = example_frame(RECORDING_OF[screw])
        assert frame.source == recording.source
        assert frame.signals == recording.signals
        assert frame.phases == recording.phases
        assert [t - frame.times[0] for t in frame.times] == [
            t - recording.times[0] for t in recording.times
        ]
