"""The deadlock alarm time, a launch option in seconds, 300 by default: `jms-uc2 --deadlock-alarm-seconds`, the Launcher's launch field
`deadlock_alarm_seconds`, and the run core, which puts it on every module's command line, as it
puts the strategies there. A value that is not a positive number is refused at launch, with a
reason a person reads.

The children are real processes that only sleep, or, where a test says so, the real module
process; the graph is a fake that seeds nothing and calls no process live, as in
`test_uc2_launcher_strategies.py`. None of these tests reaches a store: the store's connection
variables are taken away from every process they start. The Launcher page's field is tested in
`test_uc2_launcher_deadlock_alarm_page.py`.
"""

from __future__ import annotations

import os
import shlex
import subprocess
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
from jms_kapps.uc2.module import DEFAULT_DEADLOCK_ALARM_SECONDS
from jms_kapps.uc2.process import module_command, module_parser
from jms_kapps.uc2.run_core import participants_of
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import MONITOR, WMS, module_iri
from jms_kapps.uc2.wms_process import wms_command

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# The store's connection variables, and their deprecated predecessors, which the store still
# reads, which no process of these tests may see.
STORE_VARIABLES = (
    "TRIPLESTORE_URL",
    "TRIPLESTORE_USERNAME",
    "TRIPLESTORE_PASSWORD",
    "TRIPLESTORE_ALLOW_DESTRUCTIVE",
    "TRIPLESTORE_DATASET",
    "GRAPHDB_URL",
    "GRAPHDB_USERNAME",
    "GRAPHDB_PASSWORD",
    "GRAPHDB_ALLOW_DESTRUCTIVE",
    "GRAPHDB_REPOSITORY",
    "GRAPHDB_TEST_REPOSITORY",
    "INFLUXDB_URL",
    "INFLUXDB_TOKEN",
)
WITHOUT_A_STORE = {k: v for k, v in os.environ.items() if k not in STORE_VARIABLES}

NOT_POSITIVE = ["0", "-5", "nan", "inf"]


def alarm_time_of(command: list[str]) -> float:
    """The deadlock alarm time that the module process started by `command` reads."""
    assert command[1:3] == ["-m", "jms_kapps.uc2.process"]
    return module_parser().parse_args(command[3:]).deadlock_alarm_seconds


# -- The module process ------------------------------------------------------------------------ #


def test_a_module_process_raises_the_alarm_after_300_s_unless_it_is_given_another_time():
    assert DEFAULT_DEADLOCK_ALARM_SECONDS == 300.0
    assert (
        module_parser().parse_args(["--resource-iri", "x"]).deadlock_alarm_seconds
        == 300.0
    )
    parsed = module_parser().parse_args(
        ["--resource-iri", "x", "--deadlock-alarm-seconds", "12.5"]
    )
    assert parsed.deadlock_alarm_seconds == 12.5


def test_a_module_process_given_an_alarm_time_that_is_not_positive_ends_at_start_with_why():
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "jms_kapps.uc2.process",
            "--resource-iri",
            str(module_iri(1)),
            "--dataset",
            "no-dataset",
            "--deadlock-alarm-seconds",
            "0",
        ],
        capture_output=True,
        text=True,
        env=WITHOUT_A_STORE,
        timeout=60,
        check=False,
    )

    assert done.returncode == 2
    assert (
        "--deadlock-alarm-seconds must be a positive number of seconds, not 0"
        in done.stderr
    ), done.stderr
    assert "Traceback" not in done.stderr


# -- The run core ------------------------------------------------------------------------------ #


def test_the_run_core_puts_the_alarm_time_on_every_module_s_command_line_and_no_other():
    pair = load_topology("pair")

    def participants(**options: float):
        return participants_of(
            pair,
            module_command=lambda resource: module_command(resource, "test-dataset"),
            wms_command=wms_command("test-dataset"),
            **options,
        )

    *modules, wms = participants()
    assert [alarm_time_of(m.command) for m in modules] == [300.0, 300.0]
    assert "--deadlock-alarm-seconds" not in wms.command

    *modules, _ = participants(deadlock_alarm_seconds=12.5)
    assert [alarm_time_of(m.command) for m in modules] == [12.5, 12.5]


# -- jms-uc2 ----------------------------------------------------------------------------------- #


@pytest.fixture
def runs(monkeypatch):
    """Each run of the Launcher that `jms-uc2` starts, with its keyword arguments."""
    started: list[dict] = []

    def run(dataset, **options):
        started.append(options)
        return 0

    monkeypatch.setattr(runner, "run", run)
    return started


def test_jms_uc2_passes_the_alarm_time_to_the_launcher(runs):
    assert (
        jms_uc2.main(["--dataset", "repo", "--deadlock-alarm-seconds", "12.5"]) == 0
    )

    assert runs[0]["deadlock_alarm_seconds"] == 12.5


def test_jms_uc2_leaves_the_alarm_time_to_the_launcher_s_300_s_unless_it_is_given(runs):
    assert jms_uc2.main(["--dataset", "repo"]) == 0

    assert "deadlock_alarm_seconds" not in runs[0]
    assert (
        Launcher(
            graph=FakeGraph(),
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
        )
        .state()
        .default_deadlock_alarm_seconds
        == 300.0
    )


@pytest.mark.parametrize("seconds", NOT_POSITIVE)
def test_jms_uc2_refuses_an_alarm_time_that_is_not_a_positive_number(
    runs, capsys, seconds
):
    with pytest.raises(SystemExit) as exited:
        jms_uc2.main(["--dataset", "repo", "--deadlock-alarm-seconds", seconds])

    assert exited.value.code != 0
    assert "--deadlock-alarm-seconds must be a positive number of seconds" in (
        capsys.readouterr().err
    )
    assert runs == []


# -- The Launcher ------------------------------------------------------------------------------ #


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

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def launcher_with(graph) -> Iterator[Callable[..., Launcher]]:
    """Builds a Launcher on the fake graph with the defaults it is given, and stops each one it
    built."""
    built: list[Launcher] = []

    def build(**defaults: float) -> Launcher:
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


def alarm_times(state) -> dict[str, float | None]:
    """The alarm time on the command line of each process of the current launch, by resource;
    None where its command line has none."""
    times: dict[str, float | None] = {}
    for process in state["processes"]:
        line = shlex.split(process["command"])
        times[process["resource"]] = (
            float(line[line.index("--deadlock-alarm-seconds") + 1])
            if "--deadlock-alarm-seconds" in line
            else None
        )
    return times


def test_the_page_s_field_starts_at_the_launcher_s_own_alarm_time(launcher_with):
    with TestClient(create_app(launcher_with(deadlock_alarm_seconds=12.5))) as client:
        state = client.get("/api/state").json()

    assert state["default_deadlock_alarm_seconds"] == 12.5


def test_a_launch_that_names_no_alarm_time_runs_every_module_with_the_launcher_s_own(
    launcher_with,
):
    """As `jms-uc2 --deadlock-alarm-seconds 12.5` makes the Launcher, whose first launch names
    none."""
    launcher = launcher_with(deadlock_alarm_seconds=12.5)
    launcher.launch(load_topology("pair"), name="pair")

    with TestClient(create_app(launcher)) as client:
        times = alarm_times(client.get("/api/state").json())

    assert times == {
        str(module_iri(1)): 12.5,
        str(module_iri(2)): 12.5,
        str(WMS): None,
        str(MONITOR): None,
    }


def test_a_launch_from_the_page_takes_the_alarm_time_of_its_field(launcher_with):
    with TestClient(create_app(launcher_with())) as client:
        response = client.post(
            "/api/launch", json={"name": "pair", "deadlock_alarm_seconds": 7}
        )

        assert response.status_code == 200, response.text
        times = alarm_times(response.json())
    assert [times[str(module_iri(n))] for n in (1, 2)] == [7.0, 7.0]


def test_an_edited_launch_takes_the_alarm_time_as_a_query_parameter(launcher_with):
    module_list = load_topology("pair").module_list()
    with TestClient(create_app(launcher_with())) as client:
        response = client.post(
            "/api/topology/launch?base=pair&deadlock_alarm_seconds=0.5",
            json=module_list,
        )

        assert response.status_code == 200, response.text
        times = alarm_times(response.json())
    assert [times[str(module_iri(n))] for n in (1, 2)] == [0.5, 0.5]


@pytest.mark.parametrize(
    ("value", "shown"), [(0, "0"), (-1, "-1"), ("soon", '"soon"'), (True, "true")]
)
def test_a_launch_with_an_alarm_time_that_is_not_a_positive_number_is_refused_and_launches_nothing(
    launcher_with, graph, value, shown
):
    with TestClient(create_app(launcher_with())) as client:
        response = client.post(
            "/api/launch", json={"name": "pair", "deadlock_alarm_seconds": value}
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The deadlock alarm time must be a number of seconds above 0, "
        f"not {shown}. Nothing was launched."
    )
    assert graph.seeded == []


def test_an_edited_launch_with_an_alarm_time_that_is_not_positive_is_refused(
    launcher_with, graph
):
    module_list = load_topology("pair").module_list()
    with TestClient(create_app(launcher_with())) as client:
        response = client.post(
            "/api/topology/launch?base=pair&deadlock_alarm_seconds=0", json=module_list
        )

    assert response.status_code == 422
    assert (
        "The deadlock alarm time must be a number of seconds above 0"
        in (response.json()["detail"])
    )
    assert graph.seeded == []
