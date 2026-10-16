"""Scripted runs of the running system: the real `jms-uc2`, driven over HTTP.

The first starts `jms-uc2 --topology <name or file>` on the shipped merge of two streams (`merge-fault-demo`) against the
dataset `--dataset` names, which it clears, in a session of its own, so that a Ctrl+C reaches
the Launcher and every process it started, as a terminal's does. Then it does what a person does
from the Launcher page:

- It waits until the Launcher's `/api/state` shows every process live, and learns there the
  paths under which the Launcher passes requests through to the WarehouseManagementSystem and the
  monitor: it reaches both through the Launcher's port alone, as a person over SSH does.
- It spawns each box through the WarehouseManagementSystem's spawn workflow, whose API docs the
  Launcher page links, and arms each fault through the Launcher's `POST /api/modules/faults`.
- **Fault scenario 1.** `skip-reserve` on Module 5. Boxes go from Module 2 over Module 3 to
  Module 4, the exit, and boxes from Module 1 over Module 5 and Module 6 to Module 4 as well, one
  after another on both streams, each spawned as soon as its origin accepts. Module 5 takes a box
  without looking ahead and hands it to Module 6 without asking, while Module 6 often still holds
  the box before it, which waits there for Module 4: the store refuses that handover. Only Module
  5 hands boxes to Module 6, so no other module's reservation is taken by the fault. That is a
  race of the live run, not a held move as in the tests of the faults, so the boxes keep coming
  until the monitor shows Module 5's refusal, for two minutes at most.
- **Fault scenario 2.** `complete-without-releasing` on Module 4, and box C toward Module 4.
- It reads the monitor's `/api/state` until both refusals are there, with their reports, their
  results, and the refusing shape's message among the rows a person reads, and every box is
  back in the WarehouseManagementSystem with its transport done.
- It keeps the activity page of every module and of the WMS open from the start, through the
  Launcher's port, as a person who watches the run does. At the end it finds there every record
  that the process logged from INFO up, with its time, and none below: for box A, the first box
  over Module 3, the steps of each module A passed and of the WMS, and Module 5's refusal of box
  B, with the store's reason; and Module 4's refusal of box C.
- It stops the run with Ctrl+C, and finds that `jms-uc2` exited cleanly, that no process of the
  run is left, and that no service in the graph is live.

A second scripted run, on the cross, sets a high spawn rate and presses Ctrl+C while a module
moves a box onto the next one. It finds that every process stopped before the Launcher's kill, and
that no transport is left running or queued: each one the stop cut ended failed, "the system is
stopping", written once, by the module it is assigned to or by the WarehouseManagementSystem
(added on 2026-10-06, jms_kapps 42).

A third, the load run, runs the grid with every transit time 0 and sets the spawn rate to 600
boxes a minute for 60 s, then to 0. It finds one record of the WarehouseManagementSystem for each
spawn due, a box spawned or a spawn refused with why; every transport ended, done or failed with a
reason, within 5 minutes after the rate is 0; and a clean end of the run. It prints the counts by
reason (added on 2026-10-07, jms_kapps 51).

A fourth, the run of the strategies, starts on the pair and launches the grid from the Launcher,
as the page does, with `real` for both strategies and every transit time 0, then sets the spawn
rate to 30 boxes a minute for 2 minutes, then to 0. It finds every process on the dataset the
run names, with its strategy on its command line; every spawned box arrived, or its transport
ended failed with a reason, within 5 minutes after the rate is 0; and the state naming `real` for
both (added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07).

The two take a few minutes, with the default transit time of a few seconds per move, which a person
watching the pages follows. The Launcher binds its fixed port, so no other `jms-uc2` may run on
this machine meanwhile.

(Changed on 2026-10-06, jms_kapps 43, decided by Etienne: the origin of a spawn looks ahead, and
`skip-reserve` skips the look-ahead of the module it is armed on. On the cross, every box then
passes Module 3 alone, and Module 3 is reserved for the box on an entry module, so no handover
without asking meets an occupied module there. Until then the first run used the cross, with
`skip-reserve` on Module 1, and pairs of boxes A, from Module 2 toward Module 4, and B, from Module
1 toward Module 5, 0.7 transit times after it, so that Module 1 handed B to Module 3 while Module 3
held A, three pairs at most.)

This test clears the dataset it runs against. It runs only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from _ps import children_of, shown_processes
from _sse import hold
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import live_resources
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.handshake import acceptance_record, conveyed_record, reserved_record
from jms_kapps.uc2.launcher.passthrough import process_path
from jms_kapps.uc2.launcher.runner import LAUNCHER_HOST, LAUNCHER_PORT
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.transport import status_record
from jms_kapps.uc2.vertical_slice import POSSESSION_PATH, lines_of
from jms_kapps.uc2.vocabulary import (
    FC,
    FCI_NS,
    WMS,
    local_name,
    module_iri,
)
from jms_kapps.uc2.wms import taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
    pytest.mark.posix(
        reason="starts jms-uc2 in a session of its own and signals its process group"
    ),
]

M1, M2, M3, M4, M5, M6 = (module_iri(n) for n in range(1, 7))

# The merge of two streams is the shipped topology `merge-fault-demo` (jms_kapps 46): the live
# suite no longer keeps a copy of its own. Two streams into Module 4, the exit: Module 2 over
# Module 3, and Module 1 over Module 5 and Module 6. Module 3 and Module 6 hand boxes to Module 4,
# and only Module 5 to Module 6.
#
#     Module1   Module2
#        ↓         ↓
#     Module5   Module3
#        ↓         ↓
#     Module6 → Module4
MERGE_NAME = "merge-fault-demo"

# One process for each module, one for the WMS, and one for the monitor.
MERGE_PROCESSES = len(shipped_topology(MERGE_NAME).modules) + 2
CROSS_PROCESSES = len(shipped_topology("cross").modules) + 2
LAUNCHER = f"http://{LAUNCHER_HOST}:{LAUNCHER_PORT}"

# Every process of the cross registered and live.
LIVE_WITHIN_SECONDS = 90.0

# A box's journey: four moves of a few seconds each, and a wait for Module 3 at most.
BACK_WITHIN_SECONDS = 120.0

# Every process stops within the stop timeout, in parallel, and the Launcher after them.
STOPPED_WITHIN_SECONDS = 60.0

# How long the boxes of fault scenario 1 keep coming, at most, until Module 5's refusal shows.
COLLIDES_WITHIN_SECONDS = 120.0


class Run:
    """The `jms-uc2` process of this run, and every line it printed: its own and its children's,
    which the Launcher echoes with each child's name in front."""

    def __init__(
        self, dataset: str, topology: str, processes: int, *options: str
    ) -> None:
        self.started_at = time.time()
        self.processes = processes
        command = shutil.which("jms-uc2")
        assert command is not None, "the jms-uc2 console script is not installed"
        self.process = subprocess.Popen(
            [command, "--dataset", dataset, "--topology", topology, *options],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # A session of its own: the process group a terminal's Ctrl+C would reach.
            start_new_session=True,
        )
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    def tail(self, n: int = 80) -> str:
        return "\n".join(self.lines[-n:])

    def ctrl_c(self) -> None:
        """SIGINT to the whole process group, as a terminal sends it on Ctrl+C."""
        os.killpg(self.process.pid, signal.SIGINT)

    def group_is_gone(self) -> bool:
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return True
        return False

    def end(self) -> None:
        """Stop whatever of the run is left, if a check failed before the stop: Ctrl+C first, so
        that every service deregisters and the next run may clear the dataset, then a kill of
        what is still there."""
        if self.process.poll() is None:
            self.ctrl_c()
            try:
                self.process.wait(timeout=STOPPED_WITHIN_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if not self.group_is_gone():
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()

    def wait_until_live(self) -> dict:
        """The Launcher's state once it shows all its processes live. Fails the test with each
        process's state and the run's output if they are not live in time, or if `jms-uc2` has
        exited."""
        deadline = time.monotonic() + LIVE_WITHIN_SECONDS
        state: dict = {}
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(
                    f"jms-uc2 exited with {self.process.returncode}:\n{self.tail()}"
                )
            try:
                state = launcher_state()
            except httpx.HTTPError:
                state = {}
            if state and processes_live(state, self.processes):
                return state
            time.sleep(0.5)
        states = {p["name"]: p["state"] for p in state.get("processes", [])}
        pytest.fail(f"not all live in time: {states}\n{self.tail()}")


@contextmanager
def running(
    dataset: str, topology: str, processes: int, *options: str
) -> Iterator[Run]:
    """`jms-uc2` on `topology`, a shipped name or a file, with the command line's further
    `options`, until the block ends."""
    with httpx.Client() as client:
        try:
            client.get(LAUNCHER, timeout=1)
        except httpx.ConnectError:
            pass
        else:
            pytest.fail(f"{LAUNCHER} answers already: another jms-uc2 runs here")
    started = Run(dataset, topology, processes, *options)
    try:
        yield started
    finally:
        started.end()


def argument(command: str, option: str) -> str | None:
    """The token after `option` on the command line `command`, as a shell splits it, or None
    where the line does not carry the option."""
    tokens = shlex.split(command)
    return tokens[tokens.index(option) + 1] if option in tokens[:-1] else None


@pytest.fixture
def run(dataset) -> Iterator[Run]:
    """`jms-uc2` on the cross."""
    with running(dataset, "cross", CROSS_PROCESSES) as started:
        yield started


@pytest.fixture
def merge_run(dataset) -> Iterator[Run]:
    """`jms-uc2` on the shipped `merge-fault-demo`. (Added on 2026-10-06, jms_kapps 43; the
    topology was a copy of the suite's own until jms_kapps 46.)"""
    with running(dataset, MERGE_NAME, MERGE_PROCESSES) as started:
        yield started


class ActivityPages:
    """The activity page of every module and of the WMS, open through the Launcher's port as a
    person opens it from the Launcher page, and every record each has shown, by the process's
    resource."""

    def __init__(self, state: dict) -> None:
        self.closing = threading.Event()
        self.shown: dict[str, list[dict]] = {}
        self.names: dict[str, str] = {}
        self._pages: list[threading.Thread] = []
        for process in state["processes"]:
            if process["kind"] not in ("module", "wms"):
                continue
            links = {link["label"]: link["href"] for link in process["links"]}
            shown = self.shown[process["resource"]] = []
            self.names[process["resource"]] = process["name"]
            opened = threading.Event()
            self._pages.append(
                hold(
                    f"{LAUNCHER}/{links['activity']}/stream",
                    until=self.closing.is_set,
                    opened=opened,
                    shown=shown,
                )
            )
            assert opened.wait(timeout=10), f"{process['name']}'s stream never opened"

    def messages(self, resource: IRI) -> list[str]:
        """Each message the page of `resource` has shown, in order."""
        return [record["message"] for record in self.shown[str(resource)]]

    def close(self) -> None:
        self.closing.set()
        for page in self._pages:
            page.join(timeout=15)


# A record of this package as a process writes it to its output, in Python's default format.
_OWN_RECORD = re.compile(r"(INFO|WARNING|ERROR|CRITICAL):(jms_kapps[\w.]*):(.*)")


def own_records(lines: list[str], child: str) -> set[tuple[str, str, str]]:
    """Each record of this package that `child` wrote to its output, out of what the Launcher
    echoed: its level, its logger, and the first line of its message."""
    return {
        (m.group(1), m.group(2), m.group(3))
        for line in lines_of(lines, child)
        if (m := _OWN_RECORD.fullmatch(line))
    }


def shown_records(page: list[dict]) -> set[tuple[str, str, str]]:
    """The same of each record an activity page has shown."""
    return {(r["level"], r["logger"], r["message"].split("\n")[0]) for r in page}


def missing_from_the_pages(run: Run, pages: ActivityPages) -> dict[str, set]:
    """By process, each record of this package that it logged and its page has not shown."""
    missing = {
        pages.names[resource]: own_records(run.lines, pages.names[resource])
        - shown_records(page)
        for resource, page in pages.shown.items()
    }
    return {name: records for name, records in missing.items() if records}


def in_order(expected: list[str], shown: list[str]) -> bool:
    """Whether `shown` holds each of `expected`, in that order, with others between."""
    rest = iter(shown)
    return all(any(line == wanted for line in rest) for wanted in expected)


def first_lines(page: list[str]) -> list[str]:
    return [message.split("\n")[0] for message in page]


def launcher_state() -> dict:
    return httpx.get(f"{LAUNCHER}/api/state", timeout=5).json()


def processes_live(state: dict, count: int) -> bool:
    processes = state["processes"]
    return len(processes) == count and all(p["state"] == "live" for p in processes)


def through_the_launcher(state: dict, kind: str) -> str:
    """Where a person reaches the process of `kind`: through the Launcher's port, under the
    process's own path."""
    (process,) = [p for p in state["processes"] if p["kind"] == kind]
    return f"{LAUNCHER}/{process_path(IRI(process['resource']))}".rstrip("/")


def arm(module: IRI, name: str) -> None:
    """Arm `name` on `module` through the Launcher, which holds what it commanded."""
    response = httpx.post(
        f"{LAUNCHER}/api/modules/faults",
        json={"module": str(module), "name": name, "armed": True},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    (shown,) = [p for p in response.json()["processes"] if p["resource"] == str(module)]
    assert shown["faults"][name] is True, shown


def spawn_until_accepted(
    wms: str,
    origin: IRI,
    destination: IRI,
    opened: dict[str, IRI],
    until: threading.Event,
) -> str | None:
    """Spawn a box through the WMS's spawn workflow, and return its local name, asking again
    while the origin refuses with a refusal that passes, as it does while the module after it is
    reserved or occupied; None if `until` is set first. Its transport, as the spawn's answer names
    it, goes into `opened` under that name."""
    while not until.is_set():
        response = httpx.post(
            f"{wms}/workflows/spawn_box/execute",
            json={"origin": str(origin), "destination": str(destination)},
            timeout=60,
        )
        response.raise_for_status()
        spawned = response.json()
        if spawned["accepted"]:
            box = local_name(spawned["box"])
            opened[box] = IRI(spawned["operation"])
            return box
        assert "refused to reserve" in spawned["reason"], spawned
        time.sleep(0.5)
    return None


def monitor_state(monitor: str) -> dict:
    return httpx.get(f"{monitor}/api/state", timeout=5).json()


def back_in_the_wms(monitor: str, boxes: list[str]) -> bool:
    shown = {b["id"]: b for b in monitor_state(monitor)["boxes"]}
    return all(
        box in shown
        and shown[box]["holder"] == local_name(WMS)
        and shown[box]["status"] == "done"
        for box in boxes
    )


def refusal_of(monitor: str, giver: IRI, box: str) -> dict | None:
    """The refusal the monitor shows of `giver` about `box`, if it shows one."""
    return next(
        (
            r
            for r in monitor_state(monitor)["refusals"]
            if r["giver"] == local_name(giver) and r["box"] == box
        ),
        None,
    )


# The message of the rule that refuses a transport done while a module possesses its box.
RELEASED_MESSAGE = "The Box of a done BoxTransportOperation must not be possessed by a FlexConveyorModule."


def readable(refusal: dict) -> set[tuple[str | None, str]]:
    """Each result of `refusal` as its page shows it: its source shape whole, and its message."""
    return {
        (result["shape"] and result["shape"]["full"], result["message"])
        for result in refusal["readable_results"]
    }


def test_the_running_system_shows_both_faults_refused_and_every_box_arrives(
    merge_run, dataset, request
):
    run = merge_run
    state = run.wait_until_live()
    wms, monitor = (
        through_the_launcher(state, "wms"),
        through_the_launcher(state, "monitor"),
    )
    pids = [p["pid"] for p in state["processes"]]
    spawned: list[str] = []
    # Each box's transport, by the box's local name, as its spawn answered.
    opened: dict[str, IRI] = {}
    print(f"all {len(pids)} processes live; WMS at {wms}, monitor at {monitor}")
    pages = ActivityPages(state)
    request.addfinalizer(pages.close)

    # Fault scenario 1.
    arm(M5, "skip-reserve")
    collided = threading.Event()
    streams: dict[IRI, list[str]] = {M2: [], M1: []}

    def stream(origin: IRI) -> None:
        while not collided.is_set():
            box = spawn_until_accepted(wms, origin, M4, opened, collided)
            if box is not None:
                streams[origin].append(box)

    with ThreadPoolExecutor(2) as pool:
        running = [pool.submit(stream, origin) for origin in streams]
        deadline = time.monotonic() + COLLIDES_WITHIN_SECONDS
        first: dict | None = None
        while first is None and time.monotonic() < deadline:
            first = next(
                (
                    r
                    for r in monitor_state(monitor)["refusals"]
                    if r["giver"] == local_name(M5)
                ),
                None,
            )
            time.sleep(1)
        collided.set()
        for spawning in running:
            spawning.result()
    spawned.extend([*streams[M2], *streams[M1]])
    print(
        f"spawned over Module 3: {streams[M2]}; over Module 5: {streams[M1]}; "
        f"{'refused: ' + first['box'] if first else 'no handover refused'}"
    )
    assert first is not None, f"no handover of Module 5 refused\n{run.tail()}"
    box_a, box_b = streams[M2][0], first["box"]
    eventually(
        lambda boxes=tuple(spawned): back_in_the_wms(monitor, list(boxes)),
        timeout=BACK_WITHIN_SECONDS,
    )
    assert first["receiver"] == "Module6"
    assert first["report"]
    assert {
        "shape": str(FC.FlexConveyorModulePossessionShape),
        "focus_node": str(M6),
        "path": POSSESSION_PATH,
    } in first["results"]
    assert (
        str(FC.FlexConveyorModulePossessionShape),
        "A FlexConveyorModule may possess at most one Box at a time.",
    ) in readable(first)

    # Fault scenario 2.
    arm(M4, "complete-without-releasing")
    box_c = spawn_until_accepted(wms, M2, M4, opened, threading.Event())
    assert box_c is not None
    spawned.append(box_c)
    eventually(
        lambda: refusal_of(monitor, M4, box_c) is not None,
        timeout=BACK_WITHIN_SECONDS,
    )
    second = refusal_of(monitor, M4, box_c)
    assert second is not None
    assert second["receiver"] is None
    assert second["report"]
    assert str(FC.DoneTransportBoxReleasedShape) in {
        r["shape"] for r in second["results"]
    }
    assert (
        str(FC.DoneTransportBoxReleasedShape),
        RELEASED_MESSAGE,
    ) in readable(second)
    print(f"shown: {first['rules']} for {box_b}; {second['rules']} for {box_c}")

    # Every box back in the WMS, its transport done, both refusals still shown.
    eventually(lambda: back_in_the_wms(monitor, spawned), timeout=BACK_WITHIN_SECONDS)
    shown = {r["id"] for r in monitor_state(monitor)["refusals"]}
    assert {first["id"], second["id"]} <= shown

    # Every page shows what its process logged, from INFO up, with its time.
    deadline = time.monotonic() + 10
    while (
        missing := missing_from_the_pages(run, pages)
    ) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert missing == {}, missing
    for page in pages.shown.values():
        for record in page:
            assert record["level"] in ("INFO", "WARNING", "ERROR", "CRITICAL"), record
            assert run.started_at <= record["timestamp"] <= time.time(), record
    check_the_steps_of_box_a(pages, IRI(box_a, base=FCI_NS), opened[box_a])
    check_the_wms_heard_each_box_once(pages, opened)
    # Each refusal on the page of the giving module, with the store's reason.
    assert (
        f"Module5: the triple store refused handing {box_b} to Module6, by "
        "FlexConveyorModulePossessionShape at Module6; asking before trying again:"
    ) in first_lines(pages.messages(M5))
    assert any(
        line.startswith(
            f"Module4: the triple store refused completing {box_c}_transport without "
            f"releasing {box_c}, by "
        )
        and "DoneTransportBoxReleasedShape" in line
        for line in first_lines(pages.messages(M4))
    ), pages.messages(M4)
    print(
        "every activity page shows its process's records; "
        + ", ".join(f"{pages.names[r]}: {len(p)}" for r, p in pages.shown.items())
    )
    pages.close()

    # Ctrl+C: jms-uc2 stops every process, and nothing of the run is left.
    run.ctrl_c()
    assert run.process.wait(timeout=STOPPED_WITHIN_SECONDS) == 0, run.tail()
    print(f"every box back in the WMS: {', '.join(spawned)}; Ctrl+C, jms-uc2 exited 0")
    eventually(run.group_is_gone, timeout=10)
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not [line for line in run.lines if "and was killed" in line], run.tail()
    assert live_resources(connect(dataset)) == set()


def check_the_steps_of_box_a(pages: ActivityPages, box: IRI, transport: IRI) -> None:
    """Box A went from the WMS onto Module 2, over Module 3 to Module 4, and back. The page of each
    module it passed shows, in the order they happened, the Reserve it was asked and, after a hop,
    the Receive, the Reserve it asked of the next module and its convey, and each change of status
    it made; the WMS's page shows the spawn, the origin's answer to its Reserve, and the end of the
    transport. The WMS calls no Receive: its notification starts the origin."""
    passed = [(WMS, M2), (M2, M3), (M3, M4)]
    for giver, module in passed:
        expected = [acceptance_record(module, FC.ReserveWorkflow, box, giver)]
        if giver != WMS:
            expected.append(acceptance_record(module, FC.ReceiveWorkflow, box, giver))
        if module == M2:
            expected.append(status_record(M2, transport, OperationStatus.RUNNING))
        if module == M4:
            expected.append(status_record(M4, transport, OperationStatus.DONE))
        else:
            following = {M2: M3, M3: M4}[module]
            expected += [
                reserved_record(module, following, box),
                conveyed_record(module, box, following),
            ]
        shown = pages.messages(module)
        assert in_order(expected, shown), (local_name(module), expected, shown)
    shown_by_the_wms = set(pages.messages(WMS))
    assert {
        f"WarehouseManagementSystem1: spawned {box.fragment} onto Module2, for Module4",
        status_record(WMS, transport, OperationStatus.QUEUED),
        reserved_record(WMS, M2, box),
        taken_back_record(WMS, box, transport),
    } <= shown_by_the_wms, sorted(shown_by_the_wms)


def check_the_wms_heard_each_box_once(
    pages: ActivityPages, opened: dict[str, IRI]
) -> None:
    """The WMS hears of each box once, at its transport's end: a module notifies it only when it
    ends a transport, `done` or `failed`, and a change of the assigned module sends nothing
    (Etienne, 2026-10-06). Every box came back, so the one record of each is the WMS taking it
    back, and its page shows no other notification. The graph shows a box back as soon as the
    store admits the last handover, and the WMS hears of it only after the move, so its records
    are waited for."""
    taken_back = [
        taken_back_record(WMS, IRI(box, base=FCI_NS), transport)
        for box, transport in opened.items()
    ]
    eventually(
        lambda: set(taken_back) <= set(first_lines(pages.messages(WMS))),
        timeout=BACK_WITHIN_SECONDS,
    )
    shown = first_lines(pages.messages(WMS))
    for record in taken_back:
        assert shown.count(record) == 1, (record, shown)
    heard_otherwise = [
        line
        for line in shown
        if line.startswith(f"{WMS.fragment}: notified of ")
        or any(
            line.startswith(f"{WMS.fragment}: {t.fragment} failed: ")
            for t in opened.values()
        )
    ]
    assert heard_otherwise == [], heard_otherwise


# The reason of a transport that a stop ends.
STOPPING = "the system is stopping"

# The spawn rate of the stop test: a box every two seconds, so that boxes are between modules at
# the Ctrl+C.
HIGH_SPAWN_RATE = 30.0

# How long the stop test lets the WMS spawn before it waits for a hop to press Ctrl+C in.
SPAWNING_SECONDS = 15.0

# How long after a giver's log line that its next module accepted its Reserve the stop test
# presses Ctrl+C: once the commit has landed on the shared store, inside the move that follows.
INTO_THE_MOVE_SECONDS = 1.5

# The name of a box or a transport: a box the WMS spawns is named `Box-<UUID>` (jms_kapps 92), and
# no such name is a word.
_NAME = r"[A-Za-z0-9_.\-]+"

# The record a giving module logs when its next module accepted its Reserve, in the run's output.
_RESERVED = re.compile(
    r".*INFO:jms_kapps\.uc2\.module:(Module\d+): Module\d+'s ReserveWorkflow accepted "
    + _NAME
)

# The record a party logs once the store admitted its write of a transport `failed`.
_FAILED = re.compile(rf".*INFO:jms_kapps\.uc2\.[\w.]+:(\w+): ({_NAME}) is failed$")


def transports(dataset: str) -> dict[IRI, dict[str, str | None]]:
    """Every transport in the graph: its status, why it failed, and the module it is assigned
    to."""
    rows = bindings(
        connect(dataset),
        f"SELECT ?t ?status ?result ?assigned WHERE {{ ?t a <{FC.BoxTransportOperation}> ; "
        f"<{SVC.operationStatus}> ?status . OPTIONAL {{ ?t <{SVC.executionResult}> ?result }} "
        f"OPTIONAL {{ ?t <{CFC.executedOn}> ?assigned }} }}",
    )
    return {
        IRI(str(row["t"])): {
            "status": str(row["status"]),
            "result": str(row["result"]) if row.get("result") is not None else None,
            "assigned": (
                local_name(IRI(str(row["assigned"])))
                if row.get("assigned") is not None
                else None
            ),
        }
        for row in rows
    }


def test_ctrl_c_at_a_high_spawn_rate_ends_every_transport_by_its_own_party(
    run, dataset
):
    """The WMS spawns a box every two seconds on the cross, and Ctrl+C comes while a module
    moves a box onto the next one (jms_kapps 42). Every process stops before the Launcher's kill,
    and then no transport is left running or queued: each one the stop cut ended failed, "the
    system is stopping", written once, by the module it is assigned to, or by the WMS for a box
    between its create and its notification."""
    state = run.wait_until_live()
    wms = through_the_launcher(state, "wms")
    response = httpx.post(
        f"{wms}/workflows/set_spawn_rate/execute",
        json={"boxes_per_minute": HIGH_SPAWN_RATE},
        timeout=30,
    )
    response.raise_for_status()
    assert response.json()["boxes_per_minute"] == HIGH_SPAWN_RATE
    time.sleep(SPAWNING_SECONDS)
    seen = len(run.lines)
    deadline = time.monotonic() + BACK_WITHIN_SECONDS
    while not any(_RESERVED.fullmatch(line) for line in run.lines[seen:]):
        assert time.monotonic() < deadline, f"no module reserved a hop:\n{run.tail()}"
        time.sleep(0.05)
    time.sleep(INTO_THE_MOVE_SECONDS)

    asked = time.monotonic()
    run.ctrl_c()
    assert run.process.wait(timeout=STOPPED_WITHIN_SECONDS) == 0, run.tail()
    stopped = time.monotonic() - asked

    assert not [line for line in run.lines if "and was killed" in line], run.tail()
    assert stopped < STOP_TIMEOUT_SECONDS, stopped
    assert live_resources(connect(dataset)) == set()
    found = transports(dataset)
    assert found, "the WMS spawned nothing"
    left = {t: f for t, f in found.items() if f["status"] in ("running", "queued")}
    assert left == {}, left
    writers: dict[str, list[str]] = {}
    for line in run.lines:
        if m := _FAILED.fullmatch(line):
            writers.setdefault(m.group(2), []).append(m.group(1))
    failed = {t: f for t, f in found.items() if f["status"] == OperationStatus.FAILED}
    for transport, fields in failed.items():
        by = writers.get(transport.fragment, [])
        assert len(by) == 1, (transport.fragment, by)
        assert by[0] in (fields["assigned"], local_name(WMS)), (transport, fields, by)
        assert STOPPING in (fields["result"] or ""), (transport.fragment, fields)
    done = sum(1 for f in found.values() if f["status"] == OperationStatus.DONE)
    print(
        f"Ctrl+C after {len(found)} spawns: {done} done, {len(failed)} failed, the stop took "
        f"{stopped:.1f} s; "
        + "; ".join(
            f"{t.fragment} by {writers[t.fragment][0]}: {f['result']}"
            for t, f in sorted(failed.items())
        )
    )


# -- The load run (jms_kapps 51, decided by Etienne on 2026-10-07) ---------------------------- #

# 600 boxes a minute for 60 s: 600 spawns due.
LOAD_RATE = 600.0
LOAD_SECONDS = 60.0
LOAD_DUE = round(LOAD_RATE * LOAD_SECONDS / 60)

# Within this long after the rate is set to 0, every transport has ended.
SETTLED_WITHIN_SECONDS = 300.0

GRID_PROCESSES = len(shipped_topology("grid").modules) + 2

# The WMS's one record of a spawn, in the run's output: a box spawned, or a spawn refused with why,
# and the origin and the destination where they are known.
_SPAWN_RECORD = re.compile(
    r".*INFO:jms_kapps\.uc2\.wms:\w+: "
    rf"(?:spawned {_NAME} onto .*|spawn refused: (?P<reason>.*?)"
    r"(?: \((?:origin|destination) [^()]*\))?)$"
)


def spawn_records(lines: list[str]) -> list[re.Match[str]]:
    return [m for line in lines if (m := _SPAWN_RECORD.fullmatch(line))]


def set_spawn_rate(wms: str, boxes_per_minute: float) -> None:
    response = httpx.post(
        f"{wms}/workflows/set_spawn_rate/execute",
        json={"boxes_per_minute": boxes_per_minute},
        timeout=30,
    )
    response.raise_for_status()
    assert response.json()["boxes_per_minute"] == boxes_per_minute


def by_reason(records: list[re.Match[str]]) -> Counter[str]:
    """How many spawns ended for each reason, "spawned" for a box spawned, with module numbers
    and box names left out, so that the same reason counts once."""
    return Counter(
        "spawned"
        if m.group("reason") is None
        else re.sub(
            r"Box-[0-9a-f-]{36}|Box\d+",
            "BoxN",
            re.sub(r"Module\d+", "ModuleN", m.group("reason")),
        )
        for m in records
    )


def test_the_load_run_accounts_for_every_spawn_at_600_boxes_a_minute_on_the_grid(
    dataset,
):
    """The grid, every transit time 0, the spawn rate at 600 boxes a minute for 60 s, then 0: one
    record for each spawn due, 600 give or take 2, each a box spawned or a spawn refused with why;
    every transport ended, done or failed with a reason, within 5 minutes; a clean end."""
    with running(dataset, "grid", GRID_PROCESSES, "--transit-time", "0") as run:
        wms = through_the_launcher(run.wait_until_live(), "wms")
        set_spawn_rate(wms, LOAD_RATE)
        time.sleep(LOAD_SECONDS)
        set_spawn_rate(wms, 0)
        zero = time.monotonic()
        # The spawns under way when the rate went to 0 end within a few store round trips.
        counted = -1
        while counted != len(spawn_records(run.lines)):
            counted = len(spawn_records(run.lines))
            time.sleep(5)
        records = spawn_records(run.lines)

        while open_ := {
            t: f
            for t, f in transports(dataset).items()
            if f["status"] not in (OperationStatus.DONE, OperationStatus.FAILED)
        }:
            assert time.monotonic() - zero < SETTLED_WITHIN_SECONDS, open_
            time.sleep(5)
        settled = time.monotonic() - zero
        found = transports(dataset)

        run.ctrl_c()
        assert run.process.wait(timeout=STOPPED_WITHIN_SECONDS) == 0, run.tail()

    counts = by_reason(records)
    print(
        f"Load run: {len(records)} spawn records for {LOAD_DUE} due; every transport ended "
        f"{settled:.0f} s after the rate was 0; "
        + "; ".join(f"{reason}: {n}" for reason, n in counts.most_common())
    )
    assert abs(len(records) - LOAD_DUE) <= 2, counts
    assert counts["spawned"] > 0, counts
    assert not [line for line in run.lines if "and was killed" in line], run.tail()
    assert live_resources(connect(dataset)) == set()
    failed = {t: f for t, f in found.items() if f["status"] == OperationStatus.FAILED}
    assert all(f["result"] for f in failed.values()), failed
    done = len(found) - len(failed)
    print(f"Load run: {len(found)} transports, {done} done, {len(failed)} failed")
    # A spawn counts as created once the store admitted its create, which wrote its transport.
    assert len(found) == counts["spawned"], (len(found), counts)


# -- The strategies, chosen at start (jms_kapps 52, decided by Etienne on 2026-10-07) ----------- #

# 30 boxes a minute for 2 minutes: 60 spawns due.
STRATEGIES_RATE = 30.0
STRATEGIES_SECONDS = 120.0
STRATEGIES_DUE = round(STRATEGIES_RATE * STRATEGIES_SECONDS / 60)

PAIR_PROCESSES = len(shipped_topology("pair").modules) + 2

# A launch from the page stops every process, clears the dataset, seeds and starts the grid.
LAUNCHED_WITHIN_SECONDS = 180.0


def launch_from_the_page(**command: object) -> dict:
    """`POST /api/launch` with `command`, as the Launcher page sends it, and the state it leaves."""
    response = httpx.post(
        f"{LAUNCHER}/api/launch", json=command, timeout=LAUNCHED_WITHIN_SECONDS
    )
    assert response.status_code == 200, response.text
    return response.json()


def module_processes_shown(run: Run) -> tuple[list[str], str]:
    """The command line of each module process that `run`'s Launcher started, as `ps` shows it,
    and the evidence for a failed check: `jms-uc2`'s pid and every line of `ps` that names jms."""
    shown = shown_processes()
    modules = [
        line for line in children_of(run.process.pid, shown) if "--resource-iri" in line
    ]
    evidence = (
        f"jms-uc2 is pid {run.process.pid}; ps -ww -eo pid=,ppid=,args=:\n"
        + "\n".join(line for line in shown if "jms" in line)
    )
    return modules, evidence


def test_the_grid_launched_from_the_launcher_with_real_for_both_settles_every_spawned_box(
    dataset,
):
    """The ticket's live run: the grid launched from the Launcher with `real` for both strategies
    and every transit time 0, the spawn rate at 30 boxes a minute for 2 minutes, then 0. Every
    process runs against the dataset the run names, with its strategy on its command line.
    Within 5 minutes after the rate is 0, every spawned box has reached its destination or its
    transport has ended failed with a reason, and the state names `real` for both."""
    with running(dataset, "pair", PAIR_PROCESSES) as run:
        run.wait_until_live()
        launched = launch_from_the_page(
            name="grid", transit_time=0, module_strategy="real", wms_strategy="real"
        )
        assert (launched["module_strategy"], launched["wms_strategy"]) == (
            "real",
            "real",
        )
        run.processes = GRID_PROCESSES
        state = run.wait_until_live()

        for process in state["processes"]:
            assert f"--dataset {dataset}" in process["command"], process
        commands = {p["kind"]: [] for p in state["processes"]}
        for process in state["processes"]:
            commands[process["kind"]].append(process["command"])
        # By each option's own argument: what else a command line carries, and in which order,
        # is not this test's (since jms_kapps 77 a module's ends with its deadlock alarm time).
        assert commands["module"]
        assert all(argument(c, "--module-strategy") == "real" for c in commands["module"])
        assert [argument(c, "--wms-strategy") for c in commands["wms"]] == ["real"]
        children, evidence = module_processes_shown(run)
        assert len(children) == len(shipped_topology("grid").modules), evidence
        assert all(
            f"--dataset {dataset}" in line and "--module-strategy real" in line
            for line in children
        ), evidence

        wms = through_the_launcher(state, "wms")
        seen = len(run.lines)
        set_spawn_rate(wms, STRATEGIES_RATE)
        time.sleep(STRATEGIES_SECONDS)
        set_spawn_rate(wms, 0)
        zero = time.monotonic()
        counted = -1
        while counted != len(spawn_records(run.lines[seen:])):
            counted = len(spawn_records(run.lines[seen:]))
            time.sleep(5)
        records = spawn_records(run.lines[seen:])

        while open_ := {
            t: f
            for t, f in transports(dataset).items()
            if f["status"] not in (OperationStatus.DONE, OperationStatus.FAILED)
        }:
            assert time.monotonic() - zero < SETTLED_WITHIN_SECONDS, open_
            time.sleep(5)
        settled = time.monotonic() - zero
        found = transports(dataset)
        in_force = launcher_state()

        run.ctrl_c()
        assert run.process.wait(timeout=STOPPED_WITHIN_SECONDS) == 0, run.tail()

    counts = by_reason(records)
    failed = {t: f for t, f in found.items() if f["status"] == OperationStatus.FAILED}
    print(
        f"Strategies run: {len(records)} spawn records for {STRATEGIES_DUE} due; "
        f"{len(found)} transports, {len(found) - len(failed)} done, {len(failed)} failed; every "
        f"transport ended {settled:.0f} s after the rate was 0; "
        + "; ".join(f"{reason}: {n}" for reason, n in counts.most_common())
    )
    assert (in_force["module_strategy"], in_force["wms_strategy"]) == ("real", "real")
    assert in_force["topology_name"] == "grid"
    assert counts["spawned"] > 0, counts
    assert all(f["result"] for f in failed.values()), failed
    # A spawn counts as spawned once the store admitted its create, which wrote its transport.
    assert len(found) == counts["spawned"], (len(found), counts)
    assert not [line for line in run.lines if "and was killed" in line], run.tail()
    assert live_resources(connect(dataset)) == set()
