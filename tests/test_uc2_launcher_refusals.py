"""The launch option `collect_refusals` at the Launcher: the field of a launch request, the field
of the Launcher's state, and the mode each launch hands to the monitor's command line.

Without it the monitor starts in "graph only", which reads the knowledge graph and nothing else;
with it, in "graph + streams", which collects the refusals from the participants' handover event
streams. The monitor's process here is a script that writes its arguments to a file and sleeps;
nothing reaches a store.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

import pytest
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

NOTES_ITS_ARGUMENTS = (
    "import sys, time; "
    "open(sys.argv[1], 'w').write('|'.join(sys.argv[2:])); "
    "time.sleep(60)"
)


class FakeGraph:
    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        pass

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []

    def clear(self) -> int:
        return 0


@pytest.fixture
def argv_file(tmp_path: Path) -> Path:
    return tmp_path / "monitor-arguments"


@pytest.fixture
def launcher(argv_file) -> Iterator[Launcher]:
    launcher = Launcher(
        graph=FakeGraph(),
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=[sys.executable, "-c", NOTES_ITS_ARGUMENTS, str(argv_file)],
        echo=lambda line: None,
        stop_timeout=5.0,
    )
    yield launcher
    launcher.stop_all()


@pytest.fixture
def client(launcher) -> Iterator[TestClient]:
    with TestClient(create_app(launcher)) as client:
        yield client


@pytest.fixture
def monitor_mode(argv_file) -> Callable[[], str]:
    """The mode the monitor's process was started with, once it has written its arguments."""

    def read() -> str:
        eventually(
            lambda: argv_file.exists() and argv_file.read_text() != "", timeout=10
        )
        arguments = argv_file.read_text().split("|")
        assert arguments[0] == "--mode"
        assert len(arguments) == 2
        return arguments[1]

    return read


def test_no_launch_is_in_force_before_the_first(client):
    assert client.get("/api/state").json()["collect_refusals"] is None


def test_a_launch_starts_the_monitor_in_graph_only_unless_it_asks(client, monitor_mode):
    state = client.post("/api/launch", json={"name": "pair"}).json()

    assert state["collect_refusals"] is False
    assert monitor_mode() == "graph only"


def test_a_launch_that_asks_starts_the_monitor_in_graph_and_streams_and_the_state_says_so(
    client, monitor_mode
):
    state = client.post(
        "/api/launch", json={"name": "pair", "collect_refusals": True}
    ).json()

    assert state["collect_refusals"] is True
    assert monitor_mode() == "graph + streams"
    assert client.get("/api/state").json()["collect_refusals"] is True


def test_each_launch_takes_its_own_choice(client, argv_file, monitor_mode):
    client.post("/api/launch", json={"name": "pair", "collect_refusals": True})
    assert monitor_mode() == "graph + streams"
    argv_file.unlink()

    state = client.post("/api/launch", json={"name": "pair"}).json()

    assert state["collect_refusals"] is False
    assert monitor_mode() == "graph only"


def test_an_edited_launch_takes_the_choice_as_a_query_parameter(client, monitor_mode):
    module_list = load_topology("pair").module_list()

    state = client.post(
        "/api/topology/launch?base=pair&collect_refusals=true", json=module_list
    ).json()

    assert state["collect_refusals"] is True
    assert monitor_mode() == "graph + streams"


def test_an_edited_launch_that_leaves_it_out_starts_the_monitor_in_graph_only(
    client, monitor_mode
):
    module_list = load_topology("pair").module_list()

    state = client.post("/api/topology/launch?base=pair", json=module_list).json()

    assert state["collect_refusals"] is False
    assert monitor_mode() == "graph only"


def test_a_clear_forgets_the_choice_with_the_rest_of_the_launch(client):
    client.post("/api/launch", json={"name": "pair", "collect_refusals": True})

    client.post("/api/clear")

    assert client.get("/api/state").json()["collect_refusals"] is None
