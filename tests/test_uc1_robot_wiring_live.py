"""The screwing resource's middleware on a live GraphDB builds its connectors to the robot from the
knowledge graph: after the seed, a build with the robot's wiring holds twenty connections, twelve
read and eight write, one for each parameter the graph describes, and the transport hook is asked
for the broker the graph names.

Construction alone: the middleware wires its connectors in its constructor, from what it reads of
the resource under the robot's class scope, and lists them before anything serves. No process, no
broker: the hook is a recorder. The test clears the dataset that `--dataset` names and skips
without the store's connection variables.
"""

from __future__ import annotations

import socket

import pytest
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import TripleStore

from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.uc1.robot_binding import robot_wiring
from jms_kapps.uc1.run_core import resources_turtle
from jms_kapps.uc1.screwing_resource import RESOURCE, build
from jms_kapps.uc1.seed import (
    MQTT_BROKER_IP,
    MQTT_BROKER_PORT,
    ROBOT,
    reset_uc1,
    seed_robot,
)
from jms_kapps.uc1.vocabulary import READWRITE, ROBOT_PARAMETERS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]


class _NoWriter:
    """A frame writer nothing writes to: the build needs one, this test makes no attempt."""

    def write(self, frame):  # pragma: no cover - never called
        raise AssertionError("no frame is written here")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def seeded(dataset: str) -> TripleStore:
    """The dataset as `jms-uc1` seeds it for the robot: the ontology, the seeded resources, and the
    robot with its twelve parameter nodes."""
    db = connect_store(dataset)
    reset_uc1(db, individuals=resources_turtle())
    seed_robot(OGM(db=db))
    return db


def test_the_middleware_lists_twenty_connections_built_from_the_graph_description(
    seeded,
):
    hooked: list[tuple[str, int]] = []
    wiring = robot_wiring(lambda host, port: hooked.append((host, port)))

    middleware = build(
        str(RESOURCE),
        OGM(db=seeded),
        "127.0.0.1",
        _free_port(),
        writer=_NoWriter(),  # type: ignore[arg-type]
        robot=wiring,
    )

    connections = middleware.connections
    assert len(connections) == 20
    ids = [record.connector_id for record in connections]
    assert len(set(ids)) == 20
    assert sum(1 for id_ in ids if id_.endswith("#read")) == 12
    assert sum(1 for id_ in ids if id_.endswith("#write")) == 8
    assert all(f"{ROBOT}#" in id_ for id_ in ids)
    # One binding per parameter, the write legs for the readwrite ones.
    assert set(wiring.bound) == set(ROBOT_PARAMETERS)
    written = {
        parameter
        for parameter, bound in wiring.bound.items()
        if f"{bound.binding.resource_iri}#{bound.binding.field_id}#write" in ids
    }
    assert written == set(READWRITE)
    # The broker comes from the graph, once per address.
    assert hooked == [(MQTT_BROKER_IP, MQTT_BROKER_PORT)]


def test_a_graph_without_the_robot_refuses_the_build(dataset: str):
    """Without the seeded robot, the middleware builds no connector, and the build raises instead
    of coming up with attempts that would wait for their timeout in silence."""
    db = connect_store(dataset)
    reset_uc1(db, individuals=resources_turtle())

    with pytest.raises(RuntimeError) as error:
        build(
            str(RESOURCE),
            OGM(db=db),
            "127.0.0.1",
            _free_port(),
            writer=_NoWriter(),  # type: ignore[arg-type]
            robot=robot_wiring(lambda host, port: None),
        )

    assert "built no connector for" in str(error.value)
    assert "hasRobotPosition" in str(error.value)
