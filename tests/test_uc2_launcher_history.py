"""The launch option `collect_history` at the Launcher: the field of a launch request, the field of
the Launcher's state, the Launcher's own default, which `jms-uc2 --collect-history` sets, and the
option each launch hands to its seed.

A launch with the option has its reset turn on the store's history collection; one without it
leaves the collection as it was (`test_uc2_history.py`). The children are processes that only
sleep, and the graph is a fake that records the option of each seed and calls no process live, as
in `test_uc2_launcher_strategies.py`. Nothing reaches a store. (Added on 2026-10-10,
jms_kapps 22, with the names Etienne decided on 2026-10-07: `jms-uc2 --collect-history`, the
checkbox "collect history", and the field `collect_history`.)
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator

import pytest
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.uc2 import __main__ as jms_uc2
from jms_kapps.uc2.launcher import runner
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]


class FakeGraph:
    """Records the option of each seed, and calls no service live."""

    def __init__(self) -> None:
        self.collected: list[bool] = []

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        self.collected.append(collect_history)

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def launcher_with(graph) -> Iterator[Callable[..., Launcher]]:
    """Builds a Launcher on the fake graph, with the defaults it is given, and stops each one it
    built."""
    built: list[Launcher] = []

    def build(**defaults: bool) -> Launcher:
        launcher = Launcher(
            graph=graph,
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
            **defaults,
        )
        built.append(launcher)
        return launcher

    yield build
    for launcher in built:
        launcher.stop_all()


@pytest.fixture
def client(launcher_with) -> Iterator[TestClient]:
    with TestClient(create_app(launcher_with())) as client:
        yield client


# -- A launch from the page ------------------------------------------------------------------- #


def test_no_launch_is_in_force_before_the_first(client):
    assert client.get("/api/state").json()["collect_history"] is None


def test_a_launch_collects_no_history_unless_it_asks(client, graph):
    state = client.post("/api/launch", json={"name": "pair"}).json()

    assert state["collect_history"] is False
    assert graph.collected == [False]


def test_a_launch_that_asks_has_its_seed_collect_history_and_the_state_says_so(
    client, graph
):
    state = client.post(
        "/api/launch", json={"name": "pair", "collect_history": True}
    ).json()

    assert state["collect_history"] is True
    assert graph.collected == [True]
    assert client.get("/api/state").json()["collect_history"] is True


def test_each_launch_takes_its_own_option(client, graph):
    client.post("/api/launch", json={"name": "pair", "collect_history": True})

    state = client.post(
        "/api/launch", json={"name": "pair", "collect_history": False}
    ).json()

    assert state["collect_history"] is False
    assert graph.collected == [True, False]


def test_an_edited_launch_takes_the_option_as_the_transit_time_is_taken(client, graph):
    module_list = load_topology("pair").module_list()

    state = client.post(
        "/api/topology/launch?base=pair&collect_history=true", json=module_list
    ).json()

    assert state["collect_history"] is True
    assert graph.collected == [True]


def test_an_edited_launch_that_leaves_it_out_collects_none(client, graph):
    module_list = load_topology("pair").module_list()

    state = client.post("/api/topology/launch?base=pair", json=module_list).json()

    assert state["collect_history"] is False
    assert graph.collected == [False]


# -- The Launcher's own default, `jms-uc2 --collect-history` ----------------------------------- #


def test_a_launch_that_names_none_takes_the_launcher_s_own(launcher_with, graph):
    """As `jms-uc2 --collect-history` makes the Launcher, whose first launch names none."""
    launcher = launcher_with(collect_history=True)

    launcher.launch(load_topology("pair"), name="pair")

    assert launcher.state().collect_history is True
    assert graph.collected == [True]


def test_the_launcher_collects_none_by_default(launcher_with, graph):
    launcher = launcher_with()

    launcher.launch(load_topology("pair"), name="pair")

    assert launcher.state().collect_history is False
    assert graph.collected == [False]


def test_a_launch_from_the_page_that_leaves_it_out_collects_none(launcher_with, graph):
    """The launch field is off if left out, whatever the Launcher's own default: the page always
    sends its checkbox."""
    with TestClient(create_app(launcher_with(collect_history=True))) as client:
        state = client.post("/api/launch", json={"name": "pair"}).json()

    assert state["collect_history"] is False
    assert graph.collected == [False]


@pytest.fixture
def runs(monkeypatch):
    """Each run of the Launcher that `jms-uc2` starts, with its keyword arguments."""
    started: list[dict] = []

    def run(dataset, **options):
        started.append(options)
        return 0

    monkeypatch.setattr(runner, "run", run)
    return started


def test_jms_uc2_collects_no_history_unless_told(runs):
    assert jms_uc2.main(["--dataset", "repo"]) == 0

    assert runs[0]["collect_history"] is False


def test_jms_uc2_with_collect_history_passes_it_to_the_run(runs):
    assert jms_uc2.main(["--collect-history", "--dataset", "repo"]) == 0

    assert runs[0]["collect_history"] is True


class Started(Exception):
    """Raised by the stand-in Launcher's launch, which ends the run there."""


@pytest.mark.parametrize("collect_history", [False, True])
def test_the_run_makes_its_launcher_with_the_option_and_launches_first_naming_none(
    monkeypatch, collect_history
):
    """So the first launch takes the Launcher's own default, which the page's checkbox then starts
    at (`test_uc2_launcher_history_page.py`)."""
    made: list[dict] = []
    launched: list[dict] = []

    class StandInLauncher:
        boxes = None

        def __init__(self, **kwargs) -> None:
            made.append(kwargs)

        def launch(self, topology, **kwargs) -> None:
            launched.append(kwargs)
            raise Started

        def stop_all(self) -> None:
            pass

    monkeypatch.setattr(runner, "connect", lambda dataset: None)
    monkeypatch.setattr(runner, "refuse_if_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "bind_socket", lambda host, port: None)
    monkeypatch.setattr(runner, "TripleStoreGraph", lambda db: None)
    monkeypatch.setattr(runner, "Launcher", StandInLauncher)

    with pytest.raises(Started):
        runner.run(
            "ogm-disposable-jms52",
            topology="pair",
            force=False,
            collect_history=collect_history,
        )

    assert made[0]["collect_history"] is collect_history
    assert "collect_history" not in launched[0]
