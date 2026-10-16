"""The Launcher's two strategies, chosen at each launch: `GET /api/strategies`, the launch fields
`module_strategy` and `wms_strategy`, the state's strategies in force, and the options on the
command lines of the processes each launch starts, which the run core builds.

The children are real processes that only sleep, or, where a test says so, the real module
process; the graph is a fake that seeds nothing and calls no process live, as in
`test_uc2_launcher_api.py`. The test strategies are registered through a fake set of entry points
(`_strategies`). (Added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07.)
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator

import pytest
from _strategies import REAL_ENTRIES, TEST_ENTRIES, entry_points
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.process import module_command
from jms_kapps.uc2.strategies import ModuleStrategy, WmsStrategy, found
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import MONITOR, WMS

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
REGISTERED = entry_points(REAL_ENTRIES, TEST_ENTRIES)
REAL_ONLY = {"name": "real", "label": "Real behaviour"}


class FakeGraph:
    """Records each topology it is given to seed, and calls no service live."""

    def __init__(self) -> None:
        self.seeded: list[Topology] = []

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        self.seeded.append(topology)

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def launcher_with(graph) -> Iterator[Callable[..., Launcher]]:
    """Builds a Launcher on the fake graph, and stops each one it built. `registered` gives the
    strategies it finds; the installed ones where it is None."""
    built: list[Launcher] = []

    def build(
        *,
        registered=None,
        module_command: Callable[[IRI], list[str]] = lambda resource: RUNS,
        **defaults: str,
    ) -> Launcher:
        strategies = (
            {}
            if registered is None
            else {
                "module_strategies": found(ModuleStrategy, registered),
                "wms_strategies": found(WmsStrategy, registered),
            }
        )
        strategies.update(defaults)
        launcher = Launcher(
            graph=graph,
            dataset="test-dataset",
            module_command=module_command,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
            **strategies,
        )
        built.append(launcher)
        return launcher

    yield build
    for launcher in built:
        launcher.stop_all()


@pytest.fixture
def client(launcher_with) -> Iterator[TestClient]:
    """The Launcher's app, over a Launcher that finds the test strategies beside `real`."""
    with TestClient(create_app(launcher_with(registered=REGISTERED))) as client:
        yield client


def commands(client: TestClient) -> dict[str, str]:
    """The command line of each process of the current launch, by resource."""
    return {
        p["resource"]: p["command"]
        for p in client.get("/api/state").json()["processes"]
    }


# -- What the page offers ----------------------------------------------------------------------- #


def test_the_installed_strategies_offer_real_and_southward_in_both_kinds(launcher_with):
    with TestClient(create_app(launcher_with())) as client:
        answer = client.get("/api/strategies")

    assert answer.status_code == 200
    southward = {"name": "southward", "label": "Southward (synthetic)"}
    assert answer.json() == {
        "module": [REAL_ONLY, southward],
        "wms": [REAL_ONLY, southward],
    }


def test_the_strategies_are_listed_by_kind_with_their_names_and_labels(client):
    assert client.get("/api/strategies").json() == {
        "module": [REAL_ONLY, {"name": "south", "label": "South only (synthetic)"}],
        "wms": [
            REAL_ONLY,
            {"name": "exit-below", "label": "Exit straight below (synthetic)"},
        ],
    }


def test_with_an_entry_whose_module_is_missing_the_launcher_still_offers_real(
    launcher_with,
):
    """As with the `evaluation` folder removed."""
    missing = entry_points(
        REAL_ENTRIES,
        {
            "jms_kapps.uc2.module_strategies": [
                ("gone", "jms_kapps.evaluation.no_such_module:STRATEGY")
            ]
        },
    )
    with TestClient(create_app(launcher_with(registered=missing))) as client:
        assert client.get("/api/strategies").json() == {
            "module": [REAL_ONLY],
            "wms": [REAL_ONLY],
        }
        launched = client.post("/api/launch", json={"name": "pair"})

    assert launched.status_code == 200, launched.text


# -- A launch ---------------------------------------------------------------------------------- #


def test_a_launch_runs_real_for_both_unless_it_names_others(client):
    state = client.post("/api/launch", json={"name": "pair"}).json()

    assert (state["module_strategy"], state["wms_strategy"]) == ("real", "real")
    lines = commands(client)
    pair = load_topology("pair")
    # Each module's line ends with the deadlock alarm time after its strategy (changed on
    # 2026-10-11, jms_kapps 77, decided by Etienne; it ended with `--module-strategy real`).
    for module in pair.modules:
        assert lines[str(module.resource)].endswith(
            "--module-strategy real --deadlock-alarm-seconds 300.0"
        )
    assert lines[str(WMS)].endswith("--wms-strategy real")


def test_a_launch_passes_the_chosen_names_to_every_module_and_to_the_wms(client):
    state = client.post(
        "/api/launch",
        json={"name": "grid", "module_strategy": "south", "wms_strategy": "exit-below"},
    ).json()

    assert (state["module_strategy"], state["wms_strategy"]) == ("south", "exit-below")
    lines = commands(client)
    grid = load_topology("grid")
    assert len(lines) == len(grid.modules) + 2
    # Each module's line ends with the deadlock alarm time after its strategy (changed on
    # 2026-10-11, jms_kapps 77, decided by Etienne; it ended with `--module-strategy south`).
    for module in grid.modules:
        line = lines[str(module.resource)]
        assert line.endswith("--module-strategy south --deadlock-alarm-seconds 300.0")
        assert "--wms-strategy" not in line
    assert lines[str(WMS)].endswith("--wms-strategy exit-below")
    assert "--module-strategy" not in lines[str(WMS)]
    assert "strategy" not in lines[str(MONITOR)]


def test_an_edited_launch_takes_the_two_names_as_the_transit_time_is_taken(client):
    module_list = load_topology("pair").module_list()

    state = client.post(
        "/api/topology/launch?base=pair&module_strategy=south&wms_strategy=exit-below",
        json=module_list,
    ).json()

    assert (state["module_strategy"], state["wms_strategy"]) == ("south", "exit-below")
    assert commands(client)[str(WMS)].endswith("--wms-strategy exit-below")


def test_a_launch_that_names_none_runs_with_the_launcher_s_own(launcher_with):
    """As `jms-uc2 --module-strategy south --wms-strategy exit-below` makes the Launcher, whose
    first launch names none: the defaults of a launch, as `--transit-time` is the default of the
    transit time."""
    launcher = launcher_with(
        registered=REGISTERED, module_strategy="south", wms_strategy="exit-below"
    )

    launcher.launch(load_topology("pair"), name="pair")

    state = launcher.state()
    assert (state.module_strategy, state.wms_strategy) == ("south", "exit-below")
    [wms] = [p for p in state.processes if p.child.resource == str(WMS)]
    assert wms.child.command.endswith("--wms-strategy exit-below")


def test_a_launch_from_the_page_that_names_none_runs_with_real(launcher_with):
    """The launch fields are `real` if left out, whatever the Launcher's own defaults."""
    launcher = launcher_with(
        registered=REGISTERED, module_strategy="south", wms_strategy="exit-below"
    )
    with TestClient(create_app(launcher)) as client:
        state = client.post("/api/launch", json={"name": "pair"}).json()

    assert (state["module_strategy"], state["wms_strategy"]) == ("real", "real")


def test_nothing_is_in_force_before_the_first_launch(client):
    state = client.get("/api/state").json()

    assert (state["module_strategy"], state["wms_strategy"]) == (None, None)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        (
            "module_strategy",
            "no module strategy 'nosuch'; the module strategies are real, south",
        ),
        (
            "wms_strategy",
            "no WMS strategy 'nosuch'; the WMS strategies are real, exit-below",
        ),
    ],
)
def test_a_launch_with_an_unknown_name_is_refused_and_clears_nothing(
    client, graph, field, reason
):
    """The run that is there stays: nothing is stopped, seeded or started."""
    client.post("/api/launch", json={"name": "pair"})
    before = client.get("/api/state").json()

    refused = client.post("/api/launch", json={"name": "grid", field: "nosuch"})

    assert refused.status_code == 422
    assert refused.json()["detail"] == reason
    assert len(graph.seeded) == 1
    after = client.get("/api/state").json()
    assert after["topology_name"] == "pair"
    assert [p["pid"] for p in after["processes"]] == [
        p["pid"] for p in before["processes"]
    ]
    assert all(p["state"] == "starting" for p in after["processes"])


def test_an_edited_launch_with_an_unknown_name_is_refused_and_clears_nothing(
    client, graph
):
    refused = client.post(
        "/api/topology/launch?base=pair&wms_strategy=nosuch",
        json=load_topology("pair").module_list(),
    )

    assert refused.status_code == 422
    assert "no WMS strategy 'nosuch'" in refused.json()["detail"]
    assert graph.seeded == []


# -- A process that does not know its strategy -------------------------------------------------- #


def test_a_module_process_that_does_not_know_its_strategy_is_shown_failed_with_the_reason(
    launcher_with, graph, monkeypatch
):
    """The Launcher finds `south` among the test strategies; the module process, a real one,
    finds the installed strategies only, so it ends at start with the reason, before it reaches a
    store, and the page shows it `failed` with that reason."""
    for variable in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"TRIPLESTORE_{variable}", raising=False)
        monkeypatch.delenv(f"GRAPHDB_{variable}", raising=False)
    launcher = launcher_with(
        registered=REGISTERED,
        module_command=lambda resource: module_command(resource, "no-dataset"),
    )
    pair = load_topology("pair")

    launcher.launch(pair, name="pair", module_strategy="south")

    def modules():
        return [p for p in launcher.state().processes if p.kind == "module"]

    eventually(lambda: all(p.child.state == "failed" for p in modules()), timeout=30)
    for process in modules():
        output = "\n".join(process.child.output)
        assert "no module strategy 'south'; the module strategies are real" in output, (
            output
        )
        assert "Traceback" not in output
