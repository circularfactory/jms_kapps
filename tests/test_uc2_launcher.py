"""The done conditions of the Launcher, the monitor's and the WMS's, each checked on `jms-uc2` as a
user runs it.

Every test starts `jms-uc2` against the dataset `--dataset` names (there is no default),
which it clears, and drives it through the Launcher page's API. The first runs the whole story:
five modules, the WMS and the monitor turn live, a box whose transport runs and one whose
transport is done reach the monitor, one module is stopped, a second `jms-uc2` is refused, and
Ctrl+C stops everything. The second makes the same two stops while every activity page is open.
The third kills a `jms-uc2`, and starts the next one over what the killed one left behind. The
fourth sends SIGTERM, or SIGHUP, to `jms-uc2` alone, and finds no process and no service left. The
next closes the terminal `jms-uc2` runs in, as a person closes theirs, and finds the same (added
on 2026-10-10, jms_kapps 32). The next launches from the page, once over its own run and once
beside a module run by hand. The last spawns a box at the WMS on two modules, and follows it over
one hop and back.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import pytest
from _bulk import bulk_validation_report, conforms
from _participants import TWO_MODULES
from _sse import hold
from _terminal import POSIX_ONLY, Terminal
from _wait import eventually
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore
from rdflib import Literal
from rdflib.namespace import RDF

from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.discovery import (
    boxes_held_by,
    service_addresses,
    workflow_endpoint,
)
from jms_kapps.uc2.handshake import acceptance_record, conveyed_record
from jms_kapps.uc2.launcher.backend import Launcher, TripleStoreGraph
from jms_kapps.uc2.launcher.runner import LAUNCHER_PORT
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.process import module_command
from jms_kapps.uc2.seed import place_box, possession_triples
from jms_kapps.uc2.topology import (
    TRANSIT_TIME_KEY,
    Connection,
    Module,
    Topology,
    load_topology,
)
from jms_kapps.uc2.transport import operation_status, status_record
from jms_kapps.uc2.vertical_slice import lines_of
from jms_kapps.uc2.vocabulary import (
    FC,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
)
from jms_kapps.uc2.wms import logger as wms_logger
from jms_kapps.uc2.wms import spawned_record, taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

PAGE = f"http://127.0.0.1:{LAUNCHER_PORT}"

# A test that starts `jms-uc2` through `start`: in a session of its own, whose process group the
# test signals, as a terminal does. Windows has neither.
IN_A_SESSION = pytest.mark.posix(
    reason="starts jms-uc2 in a session of its own and signals its process group"
)


@dataclass
class Run:
    """One `jms-uc2` the test started, and the file its output goes to, or the terminal it runs
    in."""

    process: subprocess.Popen
    log: Path
    terminal: Terminal | None = None

    def output(self) -> str:
        """What it printed so far: into its file, or to its terminal while that is open."""
        if self.terminal is not None:
            return "\n".join(self.terminal.lines)
        return self.log.read_text()


# How long a run's processes may take to turn live. Each check reads the page or the store, so
# a live test asks only every half second.
LIVE_WITHIN_SECONDS = 90.0
POLL_SECONDS = 0.5


def page_state() -> dict:
    return httpx.get(f"{PAGE}/api/state", timeout=5).json()


def processes() -> dict[str, str]:
    return {p["name"]: p["state"] for p in page_state()["processes"]}


def monitor() -> dict:
    return httpx.get(f"{PAGE}/{page_state()['monitor']}api/state", timeout=5).json()


def await_all_live(run: Run, count: int = 7) -> None:
    """Wait until the run's `count` processes are all live: by default the five
    FlexConveyorModules from the cross, the WMS and the monitor.

    A `jms-uc2` that has exited never gets there, so that fails at once, with what it printed.
    """

    def all_live() -> bool:
        if run.process.poll() is not None:
            pytest.fail(
                f"jms-uc2 exited with {run.process.returncode} before its processes were "
                f"live:\n{run.output()}"
            )
        states = processes()
        return set(states.values()) == {"live"} and len(states) == count

    eventually(all_live, timeout=LIVE_WITHIN_SECONDS, interval=POLL_SECONDS)


def stop_on_the_page(db: TripleStore, module: int) -> None:
    """Stopping one module on the page deregisters its service, and the page shows it stopped."""
    stopped = httpx.post(
        f"{PAGE}/api/processes/stop",
        json={"resource": str(module_iri(module))},
        timeout=30,
    )
    assert {p["name"]: p["state"] for p in stopped.json()["processes"]}[
        f"Module {module}"
    ] == "stopped"
    assert module_iri(module) not in service_addresses(db)


def refused(jms_uc2: str, dataset: str) -> None:
    """A `jms-uc2` started now refuses to run, because another launcher is live."""
    second = subprocess.run(
        [jms_uc2, "--dataset", dataset],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert second.returncode == 1, second.stdout + second.stderr
    assert "a fresh heartbeat in" in second.stderr, second.stderr
    assert "Or pass --force" in second.stderr, second.stderr


def press_ctrl_c(run: Run, db: TripleStore) -> None:
    """Ctrl+C stops everything, and every service deregisters."""
    os.killpg(run.process.pid, signal.SIGINT)
    assert run.process.wait(timeout=60) == 0
    assert service_addresses(db) == {}


@pytest.fixture
def jms_uc2() -> str:
    command = shutil.which("jms-uc2")
    assert command is not None, "the jms-uc2 console script is not installed"
    return command


@pytest.fixture
def start(jms_uc2, dataset, tmp_path) -> Iterator[Callable[..., Run]]:
    """Start `jms-uc2` against `dataset` with the options given, as a user starts it, with a
    transit time of 0.

    Every run it started is stopped again at the end, whatever happens.
    """
    runs: list[Run] = []

    def start(*options: str, terminal: Terminal | None = None) -> Run:
        log = tmp_path / f"launcher-{len(runs) + 1}.log"
        command = [jms_uc2, "--dataset", dataset, "--transit-time", "0", *options]
        # A session of its own, so that the test can send Ctrl+C to it and its children
        # alone, the way a terminal does.
        if terminal is not None:
            process = subprocess.Popen(
                command,
                stdout=terminal.end,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            terminal.started()
        else:
            with log.open("w") as output:
                process = subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        runs.append(Run(process, log, terminal))
        return runs[-1]

    try:
        yield start
    finally:
        for run in runs:
            if run.process.poll() is None:
                os.killpg(run.process.pid, signal.SIGTERM)
                run.process.wait(timeout=30)
            if run.terminal is not None:
                run.terminal.close()


@IN_A_SESSION
def test_the_launcher_runs_the_cross_and_stops_cleanly(start, jms_uc2, dataset):
    db = connect(dataset)
    run = start()
    await_all_live(run)

    # The monitor shows a box on its module, its transport running, and a box whose transport
    # is done, back in the WarehouseManagementSystem's possession: every box has a possessor.
    m1, m4 = module_iri(1), module_iri(4)
    place_box(OGM(db=db), box_iri(1), on=m1, origin=m1, destination=m4)
    done = transport_iri(box_iri(2))
    assert db.triples_add(
        [
            *possession_triples(db, box_iri(2), WMS),
            (box_iri(2), RDF.type, FC.Box),
            (done, RDF.type, FC.BoxTransportOperation),
            (done, FC.hasBox, box_iri(2)),
            (done, FC.hasOrigin, m1),
            (done, FC.hasDestination, m4),
            (done, SVC.operationStatus, Literal(OperationStatus.DONE)),
        ]
    )
    # The monitor reads the graph in the background, every second, so it shows them by its
    # next read.
    expected = [
        ("Box1", OperationStatus.RUNNING, "Module1"),
        ("Box2", OperationStatus.DONE, "WarehouseManagementSystem1"),
    ]
    eventually(
        lambda: (
            [(b["id"], b["status"], b["holder"]) for b in monitor()["boxes"]]
            == expected
        ),
        timeout=10,
        interval=POLL_SECONDS,
    )
    shown = monitor()
    assert {m["id"]: m["boxes"] for m in shown["modules"]}[str(m1)] == ["Box1"]

    # Stopping one module on the page deregisters its service, and it turns offline.
    stop_on_the_page(db, 2)
    eventually(
        lambda: (
            not {m["id"]: m["live"] for m in monitor()["modules"]}[str(module_iri(2))]
        ),
        timeout=10,
        interval=POLL_SECONDS,
    )

    refused(jms_uc2, dataset)
    press_ctrl_c(run, db)


@IN_A_SESSION
def test_a_stop_deregisters_while_every_activity_page_is_open(start, dataset):
    """Stopping one module on the page, then Ctrl+C, while a person watches every activity page.

    An activity page holds its instance's activity stream open. A stopping instance waits for its
    open connections before it deregisters, so a stream that never ended held the stop open until
    the Launcher killed the instance, and a kill skips the deregistration.
    """
    db = connect(dataset)
    run = start()
    await_all_live(run)
    closing = threading.Event()
    pages: list[threading.Thread] = []
    try:
        # Each activity page the Launcher page links to, opened as a person opens it: through
        # the Launcher's port.
        for process in page_state()["processes"]:
            links = {link["label"]: link["href"] for link in process["links"]}
            opened = threading.Event()
            pages.append(
                hold(
                    f"{PAGE}/{links['activity']}/stream",
                    until=closing.is_set,
                    opened=opened,
                )
            )
            assert opened.wait(timeout=10), f"{process['name']}'s stream never opened"

        stop_on_the_page(db, 2)
        press_ctrl_c(run, db)
    finally:
        closing.set()
        for page in pages:
            page.join(timeout=15)


# What a page or its script asks the browser for: a script, a style sheet, a dashboard box, the
# API description of FastAPI's docs, a fetch, a stream.
_ASKED_FOR = re.compile(
    r"""(?:\bsrc=|<link[^>]*\bhref=|\bhx-get=|\burl: |\bfetch\(|\bEventSource\()\s*"""
    r"""["']([^"']+)["']"""
)


@IN_A_SESSION
def test_every_page_the_launcher_page_links_works_through_its_port_alone(
    start, dataset
):
    """Over SSH, WSL or VS Code, a person reaches the Launcher's port and no other. Every page the
    Launcher page links opens through it, and so does what each page asks for: its scripts and
    style sheets, its dashboard boxes, its API description, its state and its stream. Swagger UI
    and its icon, which FastAPI's docs load from the web, are all that is asked of another host."""
    db = connect(dataset)
    run = start()
    await_all_live(run)
    state = page_state()
    linked = [state["monitor"], state["wms"]] + [
        link["href"] for process in state["processes"] for link in process["links"]
    ]
    # The header's two buttons; the docs, the activity, the dashboard and the logs of each of the
    # five modules and of the WMS; the monitor's page and its activity.
    assert len(linked) == 2 + 5 * 4 + 4 + 2
    with httpx.Client(timeout=10) as client:
        for href in linked:
            url = urljoin(f"{PAGE}/", href)
            assert url.startswith(f"{PAGE}/p/"), url
            page = client.get(url)
            assert page.status_code == 200, (url, page.text[:300])
            texts = [page.text]
            for asked in _ASKED_FOR.findall(page.text):
                target = urljoin(url, asked)
                if urlsplit(target).hostname not in ("127.0.0.1", "localhost"):
                    continue
                assert target.startswith(f"{PAGE}/p/"), (url, asked)
                with client.stream("GET", target) as answer:
                    assert answer.status_code == 200, (url, asked)
                    if target.endswith(".js"):
                        texts.append(answer.read().decode())
            # A script's fetch resolves against the page that loaded it, as the page's own do.
            for asked in _ASKED_FOR.findall("".join(texts[1:])):
                target = urljoin(url, asked)
                assert target.startswith(f"{PAGE}/p/"), (url, asked)
                with client.stream("GET", target) as answer:
                    assert answer.status_code == 200, (url, asked)
            if href.endswith("/docs"):
                (description,) = re.findall(r"url: '([^']*)'", page.text)
                servers = client.get(urljoin(url, description)).json()["servers"]
                base = urljoin(urljoin(url, description), servers[0]["url"])
                assert base == url.removesuffix("docs"), (url, base)
    press_ctrl_c(run, db)


@IN_A_SESSION
def test_force_starts_over_what_a_killed_launcher_left(start, jms_uc2, dataset):
    """A killed `jms-uc2` leaves its processes live to the graph. The next refuses, unless `--force`.

    A kill skips every deregistration, so the killed processes keep their addresses and fresh
    heartbeats until the heartbeats go stale.
    """
    db = connect(dataset)
    killed = start()
    await_all_live(killed)
    os.killpg(killed.process.pid, signal.SIGKILL)
    killed.process.wait(timeout=30)
    assert service_addresses(db), (
        "the killed processes left nothing behind to refuse over"
    )

    refused(jms_uc2, dataset)

    forced = start("--force")
    await_all_live(forced)
    press_ctrl_c(forced, db)


def registered(db: TripleStore) -> set[IRI]:
    """The resources whose service carries an address, however old its last heartbeat: each
    service that did not deregister."""
    rows = bindings(
        db,
        f"""
        SELECT DISTINCT ?resource WHERE {{
            ?service <{SVC.isServiceOf}> ?resource ;
                     <{SVC.address}> ?address .
        }}
        """,
    )
    return {IRI(str(row["resource"])) for row in rows}


@IN_A_SESSION
@pytest.mark.parametrize(
    "stop",
    [
        pytest.param(signal.SIGTERM, id="SIGTERM"),
        pytest.param(getattr(signal, "SIGHUP", None), id="SIGHUP", marks=POSIX_ONLY),
    ],
)
def test_sigterm_or_sighup_to_the_launcher_alone_leaves_no_process_and_no_service(
    start, dataset, stop
):
    """SIGTERM to `jms-uc2`, as `kill` sends it, stops everything, and every service deregisters.
    SIGHUP does the same (added on 2026-10-10, jms_kapps 32).

    Only the Launcher gets the signal, not its process group as with Ctrl+C in a terminal. So a
    child stops only because the Launcher stops it.
    """
    db = connect(dataset)
    run = start()
    await_all_live(run)
    try:
        run.process.send_signal(stop)

        assert run.process.wait(timeout=60) == 0, run.log.read_text()
        assert service_addresses(db) == {}
        assert registered(db) == set()
        with pytest.raises(ProcessLookupError):
            os.killpg(run.process.pid, 0)  # no process of the run is left
    finally:
        # Whatever the Launcher left behind is stopped, so that the next test is not refused.
        try:
            os.killpg(run.process.pid, signal.SIGTERM)
            eventually(
                lambda: not service_addresses(db), timeout=30, interval=POLL_SECONDS
            )
        except ProcessLookupError:
            pass


@POSIX_ONLY
def test_closing_the_terminal_leaves_no_process_and_no_service(start, dataset):
    """The terminal `jms-uc2` runs in closes, as a person closes theirs (jms_kapps 32): every
    write to it fails from then on, and SIGHUP reaches the whole process group, as the shell of a
    closed terminal sends it. So the Launcher and every process it started get SIGHUP at once.
    Each stops as SIGTERM stops it: no process of the run is left, and no service in the graph
    keeps its address. Until then SIGHUP ended every process on the spot, and none
    deregistered."""
    db = connect(dataset)
    terminal = Terminal()
    run = start(terminal=terminal)
    await_all_live(run)
    started = registered(db)
    assert len(started) == 7, started
    try:
        asked = time.monotonic()
        terminal.close()
        os.killpg(run.process.pid, signal.SIGHUP)

        code = run.process.wait(timeout=60)

        def run_is_gone() -> bool:
            try:
                os.killpg(run.process.pid, 0)
            except ProcessLookupError:
                return True
            return False

        eventually(run_is_gone, timeout=30, interval=POLL_SECONDS)
        assert registered(db) == set()
        print(
            f"the terminal closed: jms-uc2 exited with {code} after "
            f"{time.monotonic() - asked:.1f} s; no process and no registered service left of "
            f"{len(started)}"
        )
    finally:
        # Whatever the run left behind is stopped, so that the next test is not refused.
        try:
            os.killpg(run.process.pid, signal.SIGTERM)
            eventually(
                lambda: not service_addresses(db), timeout=30, interval=POLL_SECONDS
            )
        except ProcessLookupError:
            pass


@IN_A_SESSION
def test_a_launch_from_the_page_starts_over_its_own_run_but_not_over_a_module_run_by_hand(
    start, dataset, tmp_path
):
    """The page's launch asks the graph what is live only after it has stopped its own processes.

    They deregister as they stop, so a relaunch goes on. A module run by hand from its printed
    command line is live and is not the Launcher's, although the Launcher ran that module before.
    So a launch refuses, names it, and clears nothing.
    """
    db = connect(dataset)
    run = start()
    await_all_live(run)

    relaunched = httpx.post(f"{PAGE}/api/launch", json={"name": "cross"}, timeout=90)
    assert relaunched.status_code == 200, relaunched.text
    await_all_live(run)

    stop_on_the_page(db, 2)
    with (tmp_path / "module-2-by-hand.log").open("w") as output:
        by_hand = subprocess.Popen(
            module_command(module_iri(2), dataset),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        eventually(
            lambda: module_iri(2) in service_addresses(db),
            timeout=LIVE_WITHIN_SECONDS,
            interval=POLL_SECONDS,
        )

        refused_launch = httpx.post(
            f"{PAGE}/api/launch", json={"name": "cross"}, timeout=90
        )

        assert refused_launch.status_code == 409, refused_launch.text
        assert "Module2 carries a fresh heartbeat" in refused_launch.json()["detail"]
        assert module_iri(2) in service_addresses(db)
    finally:
        by_hand.terminate()
        by_hand.wait(timeout=30)
    assert module_iri(2) not in service_addresses(db)
    press_ctrl_c(run, db)


def accepted(lines: list[str], logger_name: str, record: str) -> bool:
    """Whether `lines`, one process's output, hold `record` right after its logger's name."""
    return any(line.endswith(f"{logger_name}:{record}") for line in lines)


@IN_A_SESSION
def test_a_box_goes_from_the_wms_over_one_hop_and_back_and_its_transport_is_done(
    start, dataset, tmp_path
):
    """The WMS spawns a box onto module 1, the free entry module, for module 2, the exit module.
    Module 1 hands it to module 2, which gives it back. Its transport passes queued, running and
    done, each party logs its part, and the graph is valid."""
    db = connect(dataset)
    topology = tmp_path / "two-modules.json"
    topology.write_text(json.dumps(TWO_MODULES.module_list()))
    run = start("--topology", str(topology))
    await_all_live(run, count=4)
    m1, m2 = module_iri(1), module_iri(2)

    # A process is live from its service's first heartbeat on, and registers its workflows one
    # after another after that.
    eventually(
        lambda: workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow) is not None,
        timeout=LIVE_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    spawn = workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow)
    assert spawn is not None
    spawned = httpx.post(spawn, json={}, timeout=60).json()

    assert spawned["accepted"], spawned
    box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
    eventually(
        lambda: operation_status(db, transport) == OperationStatus.DONE,
        timeout=30,
        interval=POLL_SECONDS,
    )
    assert boxes_held_by(db, WMS) == [box]

    # Each party's own record of its part, in the output the Launcher collected.
    output = run.log.read_text().splitlines()
    parts = [
        (
            "Warehouse Management System",
            wms_logger,
            status_record(WMS, transport, OperationStatus.QUEUED),
        ),
        # Evidence that the WMS notified module 1 through the middleware's event trigger: this
        # is module 1's own record that it committed the transport `running`, together with the
        # spawn's answer `accepted` (which rules out NotificationUndelivered). The middleware's
        # `_take_in` starts module 1's BoxTransportWorkflow and has two callers:
        # `_handle_event_trigger` (the event trigger) and `_reconstruct_queue` (at startup, for
        # Operations already queued). The second cannot fire here: the transport Operation is
        # created by the spawn, after `await_all_live`, so nothing is queued for module 1 when
        # it starts. Only the WMS's notification can have started it. No library log line.
        (
            "Module 1",
            module_logger,
            status_record(m1, transport, OperationStatus.RUNNING),
        ),
        (
            "Module 2",
            module_logger,
            acceptance_record(m2, FC.ReserveWorkflow, box, m1),
        ),
        (
            "Module 2",
            module_logger,
            acceptance_record(m2, FC.ReceiveWorkflow, box, m1),
        ),
        ("Module 2", module_logger, status_record(m2, transport, OperationStatus.DONE)),
    ]
    for child, logger, record in parts:
        assert accepted(lines_of(output, child), logger.name, record), record
    eventually(
        lambda: accepted(
            lines_of(run.log.read_text().splitlines(), "Warehouse Management System"),
            wms_logger.name,
            taken_back_record(WMS, box, transport),
        ),
        timeout=10,
        interval=POLL_SECONDS,
    )

    report = bulk_validation_report(db)
    assert conforms(report), report.serialize()
    press_ctrl_c(run, db)


# -- The transit time of each module, in the graph (jms_kapps 8) -------------------------------- #


def transit_times_in_the_graph(db: TripleStore) -> dict[str, list[Decimal]]:
    """Every `fc:hasTransitTime` the store holds, by module's local name: one entry for each
    value, so that a module with two shows both."""
    rows = bindings(
        db, f"SELECT ?module ?time WHERE {{ ?module <{FC.hasTransitTime}> ?time }}"
    )
    times: dict[str, list[Decimal]] = {}
    for row in rows:
        times.setdefault(IRI(str(row["module"])).fragment, []).append(
            Decimal(str(row["time"]))
        )
    return times


@IN_A_SESSION
def test_a_launch_gives_each_module_one_transit_time_its_own_or_the_fields_and_its_dashboard_shows_it(
    start, dataset
):
    """The cross at the field's 0.5 s, where the topology gives Module 3 its own 2 s: the seed
    writes 2 on Module 3 and 0.5 on every other module, each once, and each module's drawing box
    shows its own (jms_kapps 8)."""
    db = connect(dataset)
    run = start()
    await_all_live(run)
    cross = load_topology("cross").module_list()
    for entry in cross:
        if entry["id"].endswith("Module3"):
            entry[TRANSIT_TIME_KEY] = [2]

    launched = httpx.post(
        f"{PAGE}/api/topology/launch?transit_time=0.5", json=cross, timeout=90
    )

    assert launched.status_code == 200, launched.text
    await_all_live(run)
    assert transit_times_in_the_graph(db) == {
        "Module1": [Decimal("0.5")],
        "Module2": [Decimal("0.5")],
        "Module3": [Decimal(2)],
        "Module4": [Decimal("0.5")],
        "Module5": [Decimal("0.5")],
    }
    shown = {}
    for process in page_state()["processes"]:
        links = {link["label"]: link["href"] for link in process["links"]}
        if process["kind"] == "module":
            fragment = httpx.get(f"{PAGE}/{links['dashboard']}/box/module", timeout=10)
            shown[process["name"]] = re.search(
                r"transit time: ([\d.]+) s", fragment.text
            )
    assert {name: found and found.group(1) for name, found in shown.items()} == {
        "Module 1": "0.5",
        "Module 2": "0.5",
        "Module 3": "2",
        "Module 4": "0.5",
        "Module 5": "0.5",
    }
    press_ctrl_c(run, db)


class WithoutModule2sTransitTime(TripleStoreGraph):
    """The Launcher's graph, which seeds as it does and then takes one module's transit time
    away, so that the module's process meets a graph that has none for it."""

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        super().seed(topology, transit_seconds, collect_history=collect_history)
        # The term as the store holds it, so that the delete matches it exactly.
        [row] = bindings(
            self.db,
            f"SELECT ?time WHERE {{ <{module_iri(2)}> <{FC.hasTransitTime}> ?time }}",
            convert=False,
        )
        stored = Literal(row["time"]["value"], datatype=row["time"]["datatype"])
        self.db.triple_delete((module_iri(2), FC.hasTransitTime, stored))


def test_a_module_whose_graph_holds_no_transit_time_does_not_start_and_the_launcher_shows_why(
    dataset,
):
    db = connect(dataset)
    sleeping = [sys.executable, "-c", "import time; time.sleep(120)"]
    launcher = Launcher(
        graph=WithoutModule2sTransitTime(db),
        dataset=dataset,
        module_command=lambda resource: module_command(resource, dataset),
        wms_command=sleeping,
        monitor_command=sleeping,
        echo=lambda line: None,
    )
    try:
        launcher.launch(TWO_MODULES, name="two modules", transit_seconds=0)

        def module_2() -> ChildSnapshot:
            return next(
                p.child
                for p in launcher.state().processes
                if p.child.resource == module_iri(2)
            )

        eventually(
            lambda: module_2().state == "failed",
            timeout=LIVE_WITHIN_SECONDS,
            interval=POLL_SECONDS,
        )
        output = "\n".join(module_2().output)
        assert "Module2 has no transit time in the graph" in output, output
        assert "Traceback" not in output
    finally:
        launcher.stop_all()


@IN_A_SESSION
def test_a_module_conveys_after_its_own_transit_time_not_the_neighbours(
    start, dataset, tmp_path
):
    """Four modules in a line, Module1 -> Module2 -> Module3 -> Module4, one way, every spawn from
    Module1 to Module4, Module2 at 0 s and Module3 at 2 s. On each of the two, the interval from
    its record that its ReceiveWorkflow accepted the box to its record that it conveyed it is read
    from its activity page. Module3's exceeds Module2's by 2 s, give or take half a second: the
    difference cancels the round trips that both contain."""
    db = connect(dataset)
    line = Topology(
        modules=[
            Module(
                id=f"Module{n}",
                types=["EntryModule"]
                if n == 1
                else ["ExitModule"]
                if n == 4
                else ["FlexConveyorModule"],
                connections=[Connection(direction="East", to=f"Module{n + 1}")]
                if n < 4
                else [],
                transit_seconds={2: 0.0, 3: 2.0}.get(n),
            )
            for n in range(1, 5)
        ]
    )
    path = tmp_path / "line.json"
    path.write_text(json.dumps(line.module_list()))
    run = start("--topology", str(path))
    await_all_live(run, count=6)
    eventually(
        lambda: workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow) is not None,
        timeout=LIVE_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    activity: dict[int, list[dict]] = {2: [], 3: []}
    closing = threading.Event()
    for process in page_state()["processes"]:
        for n, records in activity.items():
            if process["resource"] == str(module_iri(n)):
                links = {link["label"]: link["href"] for link in process["links"]}
                hold(
                    f"{PAGE}/{links['activity']}/stream",
                    until=closing.is_set,
                    opened=threading.Event(),
                    shown=records,
                )
    try:
        spawn = workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow)
        assert spawn is not None
        spawned = httpx.post(spawn, json={}, timeout=60).json()
        assert spawned["accepted"], spawned
        box = IRI(spawned["box"])
        transport = IRI(spawned["operation"])
        eventually(
            lambda: operation_status(db, transport) == OperationStatus.DONE,
            timeout=60,
            interval=POLL_SECONDS,
        )

        def interval(n: int) -> float:
            module, receiver = module_iri(n), module_iri(n + 1)
            accepted_at = conveyed_at = None
            for record in activity[n]:
                if record["message"] == acceptance_record(
                    module, FC.ReceiveWorkflow, box, module_iri(n - 1)
                ):
                    accepted_at = record["timestamp"]
                if record["message"] == conveyed_record(module, box, receiver):
                    conveyed_at = record["timestamp"]
            assert accepted_at is not None and conveyed_at is not None, activity[n]
            return conveyed_at - accepted_at

        eventually(
            lambda: all(
                any(" conveyed " in r["message"] for r in activity[n]) for n in (2, 3)
            ),
            timeout=30,
            interval=POLL_SECONDS,
        )
        assert interval(3) - interval(2) == pytest.approx(2.0, abs=0.5), (
            interval(2),
            interval(3),
        )
    finally:
        closing.set()
    press_ctrl_c(run, db)


# The Logs tab's radio, checked: how a page says that the tab is the selected one.
LOGS_TAB_CHECKED = r'<input type="radio" name="tab" id="tab-logs"[^>]*checked'


def logs_link(resource: IRI) -> str:
    """The `logs` link the page gives the process of `resource`, as a URL on the Launcher's port."""
    for process in page_state()["processes"]:
        if process["resource"] == str(resource):
            (href,) = [x["href"] for x in process["links"] if x["label"] == "logs"]
            return urljoin(f"{PAGE}/", href)
    raise AssertionError(f"no process {resource} on the page")


def logs_box_html(page_url: str) -> str:
    """The Logs box of the dashboard page at `page_url`, fetched as the page fetches it: by the
    relative path its box names, through the Launcher's port."""
    box = httpx.get(urljoin(page_url, "dashboard/box/logs"), timeout=10)
    assert box.status_code == 200, box.text[:300]
    return box.text


@IN_A_SESSION
def test_the_logs_link_of_each_party_opens_its_logs_tab_and_its_form_works_through_the_port(
    start, dataset, tmp_path
):
    """After a box has travelled from module 1 to module 2, the logs link of module 2 opens the
    dashboard on its Logs tab, whose box lists the INFO record of its ReceiveWorkflow. The WMS's
    lists the record that it spawned the box. The tab's form sets a level through the same port."""
    db = connect(dataset)
    topology = tmp_path / "two-modules.json"
    topology.write_text(json.dumps(TWO_MODULES.module_list()))
    run = start("--topology", str(topology))
    await_all_live(run, count=4)
    m1, m2 = module_iri(1), module_iri(2)
    eventually(
        lambda: workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow) is not None,
        timeout=LIVE_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    spawn = workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow)
    assert spawn is not None
    spawned = httpx.post(spawn, json={}, timeout=60).json()
    assert spawned["accepted"], spawned
    box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
    eventually(
        lambda: operation_status(db, transport) == OperationStatus.DONE,
        timeout=30,
        interval=POLL_SECONDS,
    )

    destination = logs_link(m2)
    page = httpx.get(destination, timeout=10)
    assert page.status_code == 200, page.text[:300]
    # The Logs tab is the selected one: its radio is checked. (Its label is not highlighted
    # until KSM 458's first item, the tab label's highlight, is fixed, so the radio is what a test can read.)
    assert re.search(LOGS_TAB_CHECKED, page.text)
    received = acceptance_record(m2, FC.ReceiveWorkflow, box, m1)
    eventually(
        lambda: received in html.unescape(logs_box_html(destination)),
        timeout=15,
        interval=POLL_SECONDS,
    )

    wms = logs_link(WMS)
    assert re.search(
        LOGS_TAB_CHECKED,
        httpx.get(wms, timeout=10).text,
    )
    assert spawned_record(WMS, box, m1, m2) in html.unescape(logs_box_html(wms))

    # The form posts to `dashboard/logs/level`, relative to the page that the link opened.
    level = httpx.post(
        urljoin(destination, "dashboard/logs/level"),
        json={"logger": module_logger.name, "level": "DEBUG"},
        timeout=10,
    )
    assert level.status_code == 200, level.text
    assert level.json()["level"] == "DEBUG"
    press_ctrl_c(run, db)
