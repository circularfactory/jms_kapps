"""The run core, as a caller that is not the demo Launcher uses it: the bench launcher.

Such a caller asks the guard, starts the participants of a topology, waits at the readiness gate
until each is ready, and stops them, importing nothing from the Launcher's API or its page, and
starting no monitor.

The tests without a store run real child processes, each a few lines of Python, against a stand-in
of the graph: a process registers its service by creating a file named after its resource, then,
a moment later, its workflows, one line each, as a middleware writes its service and its first
heartbeat before its workflows; and it deregisters on SIGTERM by removing the file. The live test
runs the real modules and WarehouseManagementSystem on the dataset `--dataset` names, which
it clears, and sends the WarehouseManagementSystem an order the moment the gate opens.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Collection, Iterator
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
import pytest
from _services import live_services
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common import supervision
from jms_kapps.common.platforms import PLATFORM
from jms_kapps.common.services import Service, live_resources, reader
from jms_kapps.common.supervision import Supervisor, refuse_if_live
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.discovery import workflow_endpoint
from jms_kapps.uc2.process import MODULE_WORKFLOWS, module_command, module_parser
from jms_kapps.uc2.run_core import TripleStoreGraph, participants_of, start_run
from jms_kapps.uc2.topology import Topology, shipped_topology
from jms_kapps.uc2.transport import operation_status
from jms_kapps.uc2.vocabulary import MONITOR, WMS
from jms_kapps.uc2.wms_process import WMS_WORKFLOWS, wms_command, wms_parser

# How long after its service a stand-in process registers its workflows, in seconds.
WORKFLOWS_LATER = 2.0


class RegisteringGraph:
    """A graph in which a process's service is live from its start until it is asked to stop, and
    has its workflows from `WORKFLOWS_LATER` after its start. It records each topology it seeds."""

    def __init__(self, registry: Path) -> None:
        self.registry = registry
        self.seeded: list[Topology] = []

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        self.seeded.append(topology)

    def command(self, resource: IRI) -> list[str]:
        """The process of `resource`, which registers the workflows its participant registers."""
        marker = self.registry / quote(str(resource), safe="")
        workflows = WMS_WORKFLOWS if resource == WMS else MODULE_WORKFLOWS
        return [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys, time\n"
                f"marker = pathlib.Path({str(marker)!r})\n"
                "def deregister(*_):\n"
                "    marker.unlink(missing_ok=True)\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, deregister)\n"
                "marker.touch()\n"
                f"time.sleep({WORKFLOWS_LATER})\n"
                f"marker.write_text({chr(10).join(str(w) for w in workflows)!r})\n"
                "time.sleep(60)\n"
            ),
        ]

    def registered(self) -> dict[IRI, set[IRI]]:
        """Each resource whose process has registered its service, with its workflows so far."""
        return {
            IRI(unquote(marker.name)): {
                IRI(line) for line in marker.read_text().split()
            }
            for marker in self.registry.iterdir()
        }

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        found = []
        for resource, registered in self.registered().items():
            [service] = live_services([resource], set(workflows) & registered)
            found.append(service)
        return found


@pytest.fixture
def pair() -> Topology:
    return shipped_topology("pair")


class AskedToStop:
    """This platform, recording the process of each stop it sends, in order."""

    def __init__(self) -> None:
        self.pids: list[int] = []

    def __getattr__(self, name: str) -> object:
        return getattr(PLATFORM, name)

    def request_stop(self, process: subprocess.Popen) -> None:
        self.pids.append(process.pid)
        PLATFORM.request_stop(process)


@pytest.mark.posix(
    reason="each process deregisters on SIGTERM, which a stop sends on POSIX"
)
def test_a_caller_outside_the_launcher_starts_a_topology_waits_until_ready_and_stops_it(
    monkeypatch, tmp_path, pair
):
    """The bench launcher's way through the run core: the guard, the start, the readiness gate
    and the ordered teardown. The gate opens only once every participant's workflows are in the
    graph, not at its first heartbeat, so an order sent at once finds the WMS's spawn workflow.
    The stop asks each participant in the order they started. No monitor is started."""
    asked = AskedToStop()
    monkeypatch.setattr(supervision, "PLATFORM", asked)
    graph = RegisteringGraph(tmp_path)
    supervisor = Supervisor(graph.services, echo=lambda _: None, stop_timeout=5.0)
    started = participants_of(
        pair, module_command=graph.command, wms_command=graph.command(WMS)
    )

    refuse_if_live(graph.services, dataset="test-dataset")
    start_run(graph, supervisor, pair, started, transit_seconds=0)
    try:
        eventually(lambda: len(graph.registered()) == len(started))
        supervisor.refresh()
        assert {c.state for c in supervisor.snapshot()} == {"starting"}, (
            "a service without its workflows is not ready"
        )

        supervisor.wait_until_ready(timeout=30, interval=0.05)

        registered = graph.registered()
        assert registered[WMS] == set(WMS_WORKFLOWS)
        for module in pair.modules:
            assert registered[module.resource] == set(MODULE_WORKFLOWS)
        assert {c.state for c in supervisor.snapshot()} == {"live"}
    finally:
        supervisor.stop_all()

    assert graph.seeded == [pair]
    assert [c.resource for c in supervisor.snapshot()] == [
        *(m.resource for m in pair.modules),
        WMS,
    ]
    assert MONITOR not in {c.resource for c in supervisor.snapshot()}
    assert asked.pids == [c.pid for c in supervisor.snapshot()]
    assert {c.state for c in supervisor.snapshot()} == {"stopped"}
    assert supervisor.may_not_have_deregistered() == set()
    assert graph.registered() == {}


def test_the_participants_of_a_run_are_the_modules_and_the_wms_and_never_the_monitor(
    pair,
):
    """Each with its strategy on its command line, `real` where the caller names none, and each
    module's with the deadlock alarm time after it, 300 s where the caller names none (changed on
    2026-10-11, jms_kapps 77, decided by Etienne; a module's line ended with its strategy)."""
    started = participants_of(
        pair, module_command=lambda resource: [str(resource)], wms_command=["wms"]
    )

    assert [(p.resource, p.name, p.command, p.workflows) for p in started] == [
        (
            m.resource,
            m.name,
            [
                str(m.resource),
                "--module-strategy",
                "real",
                "--deadlock-alarm-seconds",
                "300.0",
            ],
            MODULE_WORKFLOWS,
        )
        for m in pair.modules
    ] + [
        (
            WMS,
            "Warehouse Management System",
            ["wms", "--wms-strategy", "real"],
            WMS_WORKFLOWS,
        )
    ]


def test_a_caller_outside_the_launcher_chooses_the_two_strategies_through_the_run_core(
    pair,
):
    """The bench launcher names the strategies of a run as the Launcher's launch fields do,
    `module_strategy` and `wms_strategy`: every module's command line carries the one, the
    WMS's the other, and each process reads its own with its own parser. (Added on 2026-10-10,
    jms_kapps 52 merged into jms_kapps 31: the two options go on the command lines the run core
    builds.)"""
    started = participants_of(
        pair,
        module_command=lambda resource: module_command(resource, "test-dataset"),
        wms_command=wms_command("test-dataset"),
        module_strategy="south",
        wms_strategy="exit-below",
    )

    *modules, wms = started
    assert [m.resource for m in modules] == [m.resource for m in pair.modules]
    for module in modules:
        assert module.command[:3] == [sys.executable, "-m", "jms_kapps.uc2.process"]
        parsed = module_parser().parse_args(module.command[3:])
        assert (parsed.resource_iri, parsed.module_strategy) == (
            str(module.resource),
            "south",
        )
    assert wms.resource == WMS
    assert wms.command[:3] == [sys.executable, "-m", "jms_kapps.uc2.wms_process"]
    assert wms_parser().parse_args(wms.command[3:]).wms_strategy == "exit-below"


def test_the_run_core_imports_nothing_of_the_launcher():
    """Neither its page nor its API, nor the rest of the Launcher: the bench launcher, in the
    `evaluation` folder, starts a run with this module and the supervision alone."""
    probe = (
        "import sys\n"
        "import jms_kapps.uc2.run_core, jms_kapps.common.supervision\n"
        "print(sorted(m for m in sys.modules if m.startswith('jms_kapps.uc2.launcher')))\n"
    )

    found = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert found.stdout.strip() == "[]", found.stdout + found.stderr


@pytest.fixture
def db(dataset):
    if not triplestore_env_present():
        pytest.skip("no triple store connection variables")
    return connect(dataset)


@pytest.fixture
def run_on_the_store(db, dataset, pair) -> Iterator[Supervisor]:
    """The pair, through the run core, on the dataset the test names: real modules and a real
    WarehouseManagementSystem, and no monitor."""
    graph = TripleStoreGraph(db)
    supervisor = Supervisor(graph.services, echo=lambda _: None)
    refuse_if_live(reader(db), dataset=dataset)
    start_run(
        graph,
        supervisor,
        pair,
        participants_of(
            pair,
            module_command=lambda resource: module_command(resource, dataset),
            wms_command=wms_command(dataset),
        ),
        transit_seconds=0,
    )
    try:
        yield supervisor
    finally:
        supervisor.stop_all()


@pytest.mark.graphdb
def test_a_run_through_the_run_core_takes_an_order_the_moment_it_is_ready_and_stops_clean(
    db, run_on_the_store, pair
):
    """The bench launcher sends orders at once: the gate opens only once the WMS's spawn workflow
    is in the graph, so the first order finds it. The run seeds no monitor and starts none, and its
    stop leaves no service live."""
    supervisor = run_on_the_store

    supervisor.wait_until_ready()

    endpoint = workflow_endpoint(db, WMS, WMS_WORKFLOWS[0])
    assert endpoint is not None, (
        "the gate opened before the WMS's spawn workflow was there"
    )
    answer = httpx.post(endpoint, json={}, timeout=60).json()
    assert answer["accepted"], answer
    transport = IRI(answer["operation"])
    eventually(
        lambda: operation_status(db, transport) == OperationStatus.DONE,
        timeout=60,
        interval=0.5,
    )
    assert not bindings(db, f"SELECT ?p WHERE {{ <{MONITOR}> ?p ?o }} LIMIT 1")
    assert MONITOR not in live_resources(db)

    supervisor.stop_all()

    assert {c.state for c in supervisor.snapshot()} == {"stopped"}
    assert supervisor.may_not_have_deregistered() == set()
    assert live_resources(db) == set()
