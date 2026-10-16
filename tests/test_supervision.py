"""The process supervision a launcher starts its processes with: use case 2's now, use case 1's next.

Real child processes, each a few lines of Python. The services the supervisor reads are a set of
live resources the test controls, each with every workflow it is asked for unless a test says
otherwise, so no triple store is needed. The live-instance guard's read runs against an rdflib
graph in memory. A test that needs a platform's choice other than this platform's takes it from
the platform switch, injected into the supervisor. The tests marked `posix` need a stop that a
child handles itself, which is SIGTERM there, or a signal only POSIX has.
"""

from __future__ import annotations

import errno
import io
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from _services import live_service, live_services
from _wait import eventually
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI
from rdflib import XSD, Graph, Literal, URIRef

from jms_kapps.common import supervision
from jms_kapps.common.platforms import PLATFORM, for_platform
from jms_kapps.common.services import (
    MAX_HEARTBEAT_AGE_SECONDS,
    Service,
    ServiceReader,
    live_resources,
    reader,
)
from jms_kapps.common.supervision import (
    WATCH_INTERVAL_SECONDS,
    ChildSnapshot,
    LaunchFailed,
    LiveInstanceError,
    NotReady,
    Supervisor,
    display_names,
    refuse_if_live,
)

A = IRI("http://example.org/A")
B = IRI("http://example.org/B")
C = IRI("http://example.org/C")


def python(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def state_of(supervisor: Supervisor, resource: IRI) -> ChildSnapshot:
    return next(c for c in supervisor.snapshot() if c.resource == resource)


def reading(live: set[IRI]) -> ServiceReader:
    """The services of the resources in `live`, as it is at each read, each with every workflow
    it is asked for."""
    return lambda workflows: live_services(live, workflows)


def platform_with(**choices: object) -> SimpleNamespace:
    """This platform's choices, with `choices` in place of its own: what a test injects into the
    supervisor as its `PLATFORM`."""
    own = {
        name: getattr(PLATFORM, name)
        for name in (
            "NEW_PROCESS_GROUP",
            "STOP_SIGNALS",
            "HANGUP",
            "REUSE_FIXED_PORT",
            "request_stop",
            "command_line",
        )
    }
    return SimpleNamespace(**(own | choices))


@pytest.fixture
def live() -> set[IRI]:
    """The resources whose heartbeat the supervisor finds fresh."""
    return set()


@pytest.fixture
def echoed() -> list[str]:
    return []


@pytest.fixture
def supervisor(live, echoed):
    supervisor = Supervisor(reading(live), echo=echoed.append, stop_timeout=5.0)
    yield supervisor
    supervisor.stop_all()


def test_a_child_command_line_is_printed_and_its_output_echoed_with_its_name(
    supervisor, echoed
):
    command = python("print('hello'); print('world')")

    supervisor.spawn(A, "Module 1", command)

    assert echoed[0] == PLATFORM.command_line(command)
    assert state_of(supervisor, A).command == echoed[0]
    eventually(lambda: len(echoed) == 3)
    assert echoed[1:] == ["Module 1  hello", "Module 1  world"]
    assert state_of(supervisor, A).output == ["hello", "world"]


RUNS = python("import time; time.sleep(60)")


def test_a_command_line_is_printed_and_shown_in_the_form_of_the_platforms_shell(
    monkeypatch, supervisor, echoed
):
    """On Windows in the form cmd.exe and PowerShell take, so that the line runs when pasted
    there: not in POSIX quoting, which puts the IRI's `#` in single quotes (jms_kapps 15)."""
    monkeypatch.setattr(
        supervision,
        "PLATFORM",
        platform_with(command_line=for_platform("win32").command_line),
    )
    command = [*RUNS, "http://w3id.org/circularfactory/FlexConveyorInstances#Module1"]

    supervisor.spawn(A, "Module 1", command)

    assert echoed[0] == subprocess.list2cmdline(command)
    assert "'" not in echoed[0]
    assert state_of(supervisor, A).command == echoed[0]


@pytest.mark.posix(reason="probes a process with signal 0")
def test_a_second_child_for_one_resource_is_refused_and_the_first_still_stops(
    supervisor, echoed
):
    supervisor.spawn(A, "A", RUNS)
    first = state_of(supervisor, A).pid

    with pytest.raises(ValueError, match="spawned already"):
        supervisor.spawn(A, "A again", RUNS)

    # Refused before it printed a command line, so before it started anything.
    assert len(echoed) == 1
    assert [(c.name, c.pid) for c in supervisor.snapshot()] == [("A", first)]
    supervisor.stop_all()
    assert state_of(supervisor, A).state == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(first, 0)


def test_a_child_is_starting_until_its_heartbeat_is_fresh_then_live(supervisor, live):
    supervisor.spawn(A, "A", RUNS)
    supervisor.refresh()
    assert state_of(supervisor, A).state == "starting"

    live.add(A)
    supervisor.refresh()
    assert state_of(supervisor, A).state == "live"


def test_a_child_that_exits_on_its_own_has_failed(supervisor, live):
    live.add(A)
    supervisor.spawn(A, "A", python("raise SystemExit(3)"))

    def failed() -> bool:
        supervisor.refresh()
        return state_of(supervisor, A).state == "failed"

    eventually(failed)


def test_a_child_that_exited_on_its_own_still_reads_failed_after_a_stop(
    supervisor, live
):
    """`stopped` means that the child exited when asked, so it deregistered first. A crash skips
    the deregistration, and a stop that reaches the child later does not make it clean."""
    live.add(A)
    supervisor.spawn(A, "A", python("raise SystemExit(3)"))

    def failed() -> bool:
        supervisor.refresh()
        return state_of(supervisor, A).state == "failed"

    eventually(failed)

    supervisor.stop_all()
    assert state_of(supervisor, A).state == "failed"
    supervisor.stop(A)
    assert state_of(supervisor, A).state == "failed"


def test_a_live_child_whose_heartbeat_goes_stale_has_failed(supervisor, live):
    supervisor.spawn(A, "A", RUNS)
    live.add(A)
    supervisor.refresh()

    live.discard(A)
    supervisor.refresh()

    assert state_of(supervisor, A).state == "failed"


def test_a_running_child_that_failed_reads_stopped_once_it_exits_when_asked(
    supervisor, live
):
    """Its heartbeat went stale, but it runs. Asked to stop, it can deregister as it goes."""
    supervisor.spawn(A, "A", RUNS)
    live.add(A)
    supervisor.refresh()
    live.discard(A)
    supervisor.refresh()
    assert state_of(supervisor, A).state == "failed"

    supervisor.stop_all()

    assert state_of(supervisor, A).state == "stopped"


# Says goodbye when asked to stop, which is what a middleware does when it deregisters.
GRACEFUL = python(
    "import signal, sys, time\n"
    "def bye(*_):\n"
    "    print('bye')\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, bye)\n"
    "print('ready')\n"
    "time.sleep(60)\n"
)


def ready(supervisor: Supervisor, resource: IRI) -> Callable[[], bool]:
    return lambda: "ready" in state_of(supervisor, resource).output


@pytest.mark.posix(reason="the child says goodbye on SIGTERM")
def test_stopping_one_child_asks_it_to_stop_and_leaves_the_others_running(
    supervisor, live
):
    supervisor.spawn(A, "A", GRACEFUL)
    supervisor.spawn(B, "B", RUNS)
    live.update({A, B})
    eventually(ready(supervisor, A))

    supervisor.stop(A)
    supervisor.refresh()

    assert state_of(supervisor, A).state == "stopped"
    eventually(lambda: "bye" in state_of(supervisor, A).output)
    assert state_of(supervisor, B).state == "live"


class ClosedTerminal(io.TextIOBase):
    """The stdout of a launcher whose terminal has closed: every write fails."""

    def write(self, text: str) -> int:
        raise OSError(errno.EIO, "Input/output error")


@pytest.mark.posix(reason="the children say goodbye on SIGTERM")
def test_a_closed_terminal_takes_no_echo_and_the_children_are_still_read_and_stopped(
    monkeypatch, live
):
    """A launcher whose terminal has closed cannot print a line. The supervisor still reads each
    child's output, so that no pipe fills and blocks a child, and a stop still reaches every
    child (jms_kapps 32)."""
    monkeypatch.setattr(sys, "stdout", ClosedTerminal())
    supervisor = Supervisor(reading(live), stop_timeout=5.0)
    supervisor.spawn(A, "A", GRACEFUL)
    supervisor.spawn(B, "B", GRACEFUL)
    eventually(ready(supervisor, A))
    eventually(ready(supervisor, B))

    supervisor.stop_all()

    assert [state_of(supervisor, r).state for r in (A, B)] == ["stopped", "stopped"]
    eventually(lambda: all("bye" in state_of(supervisor, r).output for r in (A, B)))


# Ignores the stop, so a stop kills it at the timeout.
STUBBORN = python(
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready')\n"
    "time.sleep(60)\n"
)


@pytest.mark.posix(reason="the child ignores SIGTERM")
def test_a_child_that_ignores_the_stop_is_killed_after_the_timeout(live, echoed):
    supervisor = Supervisor(reading(live), echo=echoed.append, stop_timeout=0.5)
    supervisor.spawn(A, "A", STUBBORN)
    eventually(ready(supervisor, A))

    started = time.monotonic()
    supervisor.stop_all()

    assert time.monotonic() - started < 5.0
    notice = "A did not stop within 0.5 s, and was killed."
    assert notice in echoed
    # Killed, not stopped: a kill skips the deregistration a stop would have made.
    assert state_of(supervisor, A).state == "killed"
    assert state_of(supervisor, A).output[-1] == notice

    supervisor.stop(A)
    assert state_of(supervisor, A).state == "killed"


@pytest.mark.posix(reason="a child ignores SIGTERM")
def test_the_children_that_may_not_have_deregistered_are_the_killed_and_the_crashed(
    live,
):
    """A kill and an exit that no stop asked for both skip the deregistration. A child that runs
    is not among them, whatever its state says: a stop asks it, and it deregisters as it goes."""
    supervisor = Supervisor(reading(live), echo=lambda _: None, stop_timeout=0.5)
    try:
        supervisor.spawn(A, "A", python("raise SystemExit(3)"))
        supervisor.spawn(B, "B", STUBBORN)
        supervisor.spawn(C, "C", RUNS)
        live.update({A, B, C})
        eventually(ready(supervisor, B))
        supervisor.refresh()
        live.discard(C)  # C runs on, but its heartbeat goes stale.

        def a_failed() -> bool:
            supervisor.refresh()
            return state_of(supervisor, A).state == "failed"

        eventually(a_failed)
        assert state_of(supervisor, C).state == "failed"
        assert supervisor.may_not_have_deregistered() == {A}

        supervisor.stop_all()

        states = [state_of(supervisor, r).state for r in (A, B, C)]
        assert states == ["failed", "killed", "stopped"]
        assert supervisor.may_not_have_deregistered() == {A, B}
    finally:
        supervisor.stop_all()


def test_a_stop_that_fails_for_one_child_still_reaches_every_later_child(
    monkeypatch, live, echoed
):
    """A stop signal can fail: on Windows a CTRL_BREAK fails to a child that exited meanwhile, or
    when the launcher has no console. The failure is recorded, in the echo and in the child's
    output, and the stop goes on: every later child still gets its stop. The child whose stop
    failed runs on, and the stop kills it at its timeout (jms_kapps 15).

    The stop that succeeds is a kill here, which every platform has, so that the test needs no
    stop that a child handles itself."""
    asked: list[int] = []

    def request_stop(process: subprocess.Popen) -> None:
        asked.append(process.pid)
        if process.pid == failing:
            raise OSError(errno.EINVAL, "The parameter is incorrect")
        process.kill()

    monkeypatch.setattr(
        supervision, "PLATFORM", platform_with(request_stop=request_stop)
    )
    supervisor = Supervisor(reading(live), echo=echoed.append, stop_timeout=0.5)
    try:
        for resource, name in zip((A, B, C), "ABC", strict=True):
            supervisor.spawn(resource, name, RUNS)
        pids = [state_of(supervisor, r).pid for r in (A, B, C)]
        failing = pids[0]

        supervisor.stop_all()

        assert asked == pids
        notice = "Could not ask A to stop: [Errno 22] The parameter is incorrect"
        assert notice in echoed
        assert notice in state_of(supervisor, A).output
        states = [state_of(supervisor, r).state for r in (A, B, C)]
        assert states == ["killed", "stopped", "stopped"]
        assert supervisor.may_not_have_deregistered() == {A}
    finally:
        supervisor.stop_all()


def test_a_child_that_never_turns_live_has_failed_after_the_start_timeout(live):
    supervisor = Supervisor(reading(live), echo=lambda _: None, start_timeout=0.2)
    supervisor.spawn(A, "A", RUNS)
    try:
        time.sleep(0.3)
        supervisor.refresh()
        assert state_of(supervisor, A).state == "failed"

        # Late is not dead: a heartbeat that arrives after all turns it live.
        live.add(A)
        supervisor.refresh()
        assert state_of(supervisor, A).state == "live"
    finally:
        supervisor.stop_all()


def failed_lines(echoed: list[str]) -> list[str]:
    """The lines the supervisor printed of a child that turned `failed`."""
    return [line for line in echoed if " failed: " in line]


def test_a_child_that_exits_unasked_is_printed_failed_once_with_its_exit_code(
    supervisor, live, echoed
):
    """A bench launcher has no page, so a child that turns `failed` shows in the terminal: one
    line, with its name and its exit code (jms_kapps 31)."""
    live.add(A)
    supervisor.spawn(A, "Module 1", python("raise SystemExit(3)"))

    def failed() -> bool:
        supervisor.refresh()
        return state_of(supervisor, A).state == "failed"

    eventually(failed)
    supervisor.refresh()
    supervisor.stop_all()

    assert failed_lines(echoed) == ["Module 1 failed: its process exited with code 3."]


def test_a_child_that_exits_before_a_stop_finds_it_is_printed_failed_by_the_stop(
    echoed,
):
    """No read saw it exit; the stop finds it exited unasked, and prints that line itself."""
    supervisor = Supervisor(reading(set()), echo=echoed.append, stop_timeout=5.0)
    supervisor.spawn(A, "A", python("raise SystemExit(4)"))
    # A snapshot reads no state of the graph, and sets none: the child is still `starting`.
    eventually(lambda: not state_of(supervisor, A).stoppable)
    assert state_of(supervisor, A).state == "starting"

    supervisor.stop_all()

    assert state_of(supervisor, A).state == "failed"
    assert failed_lines(echoed) == ["A failed: its process exited with code 4."]


@pytest.mark.parametrize("found_by", ["the watch", "the stop"])
def test_a_child_that_exits_with_code_0_once_the_stop_has_begun_has_stopped(
    supervisor, live, echoed, found_by
):
    """A stop signal can reach a child before the stop asks it, as a terminal's Ctrl+C reaches
    every process of the launcher's process group. Once the stop has begun, a child that exits
    with code 0 has stopped, whether a read finds the exit or the stop does, and no line says it
    failed. An exit with any other code still fails (jms_kapps 70)."""
    live.update({A, B})
    supervisor.spawn(A, "A", python("raise SystemExit(0)"))
    supervisor.spawn(B, "B", python("raise SystemExit(3)"))
    eventually(lambda: not any(c.stoppable for c in supervisor.snapshot()))

    supervisor.begin_stop()
    if found_by == "the watch":
        supervisor.refresh()
    supervisor.stop_all()

    assert state_of(supervisor, A).state == "stopped"
    assert state_of(supervisor, B).state == "failed"
    assert failed_lines(echoed) == ["B failed: its process exited with code 3."]
    # It ended on the stop, so it deregistered first; one that failed may not have.
    assert supervisor.may_not_have_deregistered() == {B}


# A child served as a module is, by `run_process`, which puts itself in a process group of its own
# first, so that a signal to that group reaches it alone, as a terminal's reaches every process of
# a launcher's.
SERVED_IN_ITS_OWN_GROUP = python(
    "import os\n"
    "from types import SimpleNamespace\n"
    "from fastapi import FastAPI\n"
    "from jms_kapps.common.process import run_process\n"
    "os.setpgrp()\n"
    "run_process('127.0.0.1', lambda port: SimpleNamespace(app=FastAPI()),"
    " lambda address: 'serving on ' + address)\n"
)


@pytest.mark.posix(reason="a signal to a process group, and SIGHUP, are POSIX only")
@pytest.mark.parametrize(
    "stop", [signal.SIGTERM, getattr(signal, "SIGHUP", None)], ids=["SIGTERM", "SIGHUP"]
)
def test_a_child_that_a_stop_signal_to_its_process_group_ends_once_the_stop_has_begun_has_stopped(
    supervisor, live, echoed, stop
):
    """A served child that stops cleanly exits with code 0, whichever stop signal began its
    stop, so the supervisor reads it as stopped and prints no line that it failed (jms_kapps
    104)."""
    live.add(A)
    supervisor.spawn(A, "A", SERVED_IN_ITS_OWN_GROUP)
    eventually(
        lambda: any("serving on" in line for line in state_of(supervisor, A).output)
    )
    supervisor.refresh()

    supervisor.begin_stop()
    os.killpg(state_of(supervisor, A).pid, stop)
    eventually(lambda: not state_of(supervisor, A).stoppable)
    supervisor.refresh()
    supervisor.stop_all()

    assert state_of(supervisor, A).state == "stopped", echoed
    assert failed_lines(echoed) == []


def test_a_child_that_exits_with_code_0_before_the_stop_has_begun_has_failed(
    supervisor, live, echoed
):
    """Before the stop has begun, an exit with code 0 is an exit unasked, as any other."""
    live.add(A)
    supervisor.spawn(A, "A", python("raise SystemExit(0)"))
    eventually(lambda: not state_of(supervisor, A).stoppable)
    supervisor.refresh()

    supervisor.begin_stop()
    supervisor.stop_all()

    assert state_of(supervisor, A).state == "failed"
    assert failed_lines(echoed) == ["A failed: its process exited with code 0."]


def test_a_live_child_whose_service_stops_being_live_is_printed_failed_without_an_exit_code(
    supervisor, live, echoed
):
    """Its process still runs, so it has no exit code. It turns live again when its service
    does, and each turn to `failed` is one line."""
    supervisor.spawn(A, "A", RUNS)
    live.add(A)
    supervisor.refresh()

    live.discard(A)
    supervisor.refresh()
    supervisor.refresh()
    live.add(A)
    supervisor.refresh()
    live.discard(A)
    supervisor.refresh()

    assert failed_lines(echoed) == ["A failed: its service is not ready any more."] * 2


def test_a_child_that_does_not_turn_live_in_time_is_printed_failed_with_the_start_timeout(
    live, echoed
):
    supervisor = Supervisor(reading(live), echo=echoed.append, start_timeout=0.2)
    supervisor.spawn(A, "A", RUNS)
    try:
        time.sleep(0.3)
        supervisor.refresh()
        supervisor.refresh()
    finally:
        supervisor.stop_all()

    assert failed_lines(echoed) == ["A failed: it did not turn live within 0.2 s."]


@pytest.mark.posix(reason="the child says goodbye on SIGTERM")
def test_a_child_that_a_stop_ends_is_not_printed_failed(supervisor, live, echoed):
    live.add(A)
    supervisor.spawn(A, "A", GRACEFUL)
    eventually(ready(supervisor, A))
    supervisor.refresh()

    supervisor.stop_all()

    assert state_of(supervisor, A).state == "stopped"
    assert failed_lines(echoed) == []


RESERVE = IRI("http://example.org/plant#ReserveWorkflow")
RECEIVE = IRI("http://example.org/plant#ReceiveWorkflow")


def test_a_child_is_live_only_once_its_service_has_every_workflow_it_registers():
    """A middleware writes its service and its first heartbeat before its workflows, so for a
    moment its service is live without them: after a launch, a driver waited up to 4.5 s for the
    WMS's spawn workflow. The child turns live only once all of them are in the graph."""
    registered: list[IRI] = []
    asked: list[set[IRI]] = []

    def services(workflows: Collection[IRI]) -> list[Service]:
        asked.append(set(workflows))
        found = live_service(A, "http://127.0.0.1:4001", workflows)
        return [
            Service(
                found.iri,
                found.resource,
                found.address,
                found.heartbeat,
                {c: e for c, e in found.workflows.items() if c in registered},
            )
        ]

    supervisor = Supervisor(services, echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS, workflows=(RESERVE, RECEIVE))
    supervisor.spawn(B, "B", RUNS)
    try:
        supervisor.refresh()
        assert asked[-1] == {RESERVE, RECEIVE}
        assert state_of(supervisor, A).state == "starting"

        registered.append(RESERVE)
        supervisor.refresh()
        assert state_of(supervisor, A).state == "starting"
        assert state_of(supervisor, A).address is None

        registered.append(RECEIVE)
        supervisor.refresh()
        assert state_of(supervisor, A).state == "live"
        assert state_of(supervisor, A).address == "http://127.0.0.1:4001"
        # B registers no workflow, and has no service in this graph: it is still starting.
        assert state_of(supervisor, B).state == "starting"
    finally:
        supervisor.stop_all()


def test_the_readiness_gate_returns_once_every_child_is_live(live):
    supervisor = Supervisor(reading(live), echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS, workflows=(RESERVE,))
    supervisor.spawn(B, "B", RUNS)
    try:
        threading.Timer(0.3, lambda: live.update({A, B})).start()

        supervisor.wait_until_ready(timeout=10, interval=0.05)

        assert [c.state for c in supervisor.snapshot()] == ["live", "live"]
    finally:
        supervisor.stop_all()


def test_the_readiness_gate_fails_at_once_naming_a_child_that_exits_before_it_is_live(
    live,
):
    supervisor = Supervisor(reading(live), echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    supervisor.spawn(B, "B", python("print('no transit time'); raise SystemExit(3)"))
    started = time.monotonic()
    try:
        with pytest.raises(NotReady, match=r"(?s)^B is failed.*no transit time"):
            supervisor.wait_until_ready(timeout=30, interval=0.05)
        assert time.monotonic() - started < 10
    finally:
        supervisor.stop_all()


def test_the_readiness_gate_names_each_child_not_live_and_why_the_read_failed_when_it_runs_out():
    reads: list[str] = []

    def away(workflows: Collection[IRI]) -> list[Service]:
        reads.append("read")
        raise ConnectionError("the store is away")

    supervisor = Supervisor(away, echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    supervisor.spawn(B, "B", RUNS)
    try:
        with pytest.raises(
            NotReady,
            match=r"^not live within 0.3 s: A, B; the last read of the graph failed: "
            r"the store is away",
        ):
            supervisor.wait_until_ready(timeout=0.3, interval=0.05)
        assert len(reads) > 1, "a read that fails is tried again"
    finally:
        supervisor.stop_all()


class Heartbeats:
    """The services: those of the resources found live, at their addresses, or an error while the
    graph is away."""

    def __init__(self) -> None:
        self.live: set[IRI] = set()
        self.addresses: dict[IRI, str] = {}
        self.away: str | None = None

    def __call__(self, workflows: Collection[IRI]) -> list[Service]:
        if self.away is not None:
            raise ConnectionError(self.away)
        return live_services(self.live, workflows, self.addresses)


def test_a_live_child_keeps_the_address_of_its_service_from_the_read_that_found_it_live():
    """A module run by hand on the same resource can win a later read, and a switch must still
    reach the process this supervisor started."""
    heartbeats = Heartbeats()
    supervisor = Supervisor(heartbeats, echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    try:
        supervisor.refresh()
        assert state_of(supervisor, A).address is None

        heartbeats.live.add(A)
        heartbeats.addresses[A] = "http://127.0.0.1:4001"
        supervisor.refresh()
        heartbeats.addresses[A] = "http://127.0.0.1:4999"
        supervisor.refresh()
        assert state_of(supervisor, A).address == "http://127.0.0.1:4001"

        # Only a live process is worth a link.
        heartbeats.live.discard(A)
        supervisor.refresh()
        assert state_of(supervisor, A).state == "failed"
        assert state_of(supervisor, A).address is None
    finally:
        supervisor.stop_all()


def test_a_child_that_exits_while_the_graph_is_away_has_failed():
    heartbeats = Heartbeats()
    heartbeats.live.add(A)
    supervisor = Supervisor(heartbeats, echo=lambda _: None)
    supervisor.spawn(A, "A", python("import time; time.sleep(0.5)"))
    try:
        supervisor.refresh()
        assert state_of(supervisor, A).state == "live"

        heartbeats.away = "the graph is away"

        def failed() -> bool:
            with pytest.raises(ConnectionError):
                supervisor.refresh()
            return state_of(supervisor, A).state == "failed"

        eventually(failed)
    finally:
        supervisor.stop_all()


def test_while_the_graph_is_away_a_state_that_rests_on_it_is_unconfirmed():
    heartbeats = Heartbeats()
    heartbeats.live.add(A)
    supervisor = Supervisor(heartbeats, echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    supervisor.spawn(B, "B", python("raise SystemExit(3)"))
    try:
        supervisor.refresh()
        assert state_of(supervisor, A).unconfirmed_for is None
        assert supervisor.heartbeat_error() is None

        heartbeats.away = "the graph is away"

        def b_failed() -> bool:
            with pytest.raises(ConnectionError):
                supervisor.refresh()
            return state_of(supervisor, B).state == "failed"

        eventually(b_failed)
        # A's state is the last one read. B's exit is this process's own knowledge.
        assert state_of(supervisor, A).state == "live"
        assert state_of(supervisor, A).unconfirmed_for >= 0
        assert state_of(supervisor, B).unconfirmed_for is None
        assert supervisor.heartbeat_error() == "the graph is away"

        heartbeats.away = None
        supervisor.refresh()
        assert state_of(supervisor, A).unconfirmed_for is None
        assert supervisor.heartbeat_error() is None
    finally:
        supervisor.stop_all()


class HangingStore:
    """A store whose every read hangs until the test lets it go, as a GraphDB read can hang until
    the client's own timeout gives up, 60 s later."""

    def __init__(self) -> None:
        self.reading = threading.Event()
        self.released = threading.Event()

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        self.reading.set()
        self.released.wait()
        return {"results": {"bindings": []}}


@pytest.mark.posix(reason="kills the child with SIGKILL")
def test_a_read_that_hangs_does_not_delay_the_watch_finding_an_exit():
    """The watch finds a process that exits within one watch interval, while the read of the
    heartbeats it started before the exit still hangs, however long it hangs (jms_kapps 32)."""
    store = HangingStore()
    supervisor = Supervisor(reader(store), echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    try:
        supervisor.watch()
        assert store.reading.wait(timeout=10), "the watch never read the heartbeats"

        os.kill(state_of(supervisor, A).pid, signal.SIGKILL)
        exited = time.monotonic()

        eventually(
            lambda: state_of(supervisor, A).state == "failed",
            timeout=2 * WATCH_INTERVAL_SECONDS,
            interval=0.01,
        )
        assert time.monotonic() - exited <= 2 * WATCH_INTERVAL_SECONDS
        assert not store.released.is_set(), "the read must still hang"
    finally:
        store.released.set()
        supervisor.stop_all()


def test_a_state_is_unconfirmed_once_no_read_has_succeeded_for_a_while(monkeypatch):
    """A read can hang until the client gives up, and nothing has failed while it hangs."""
    monkeypatch.setattr(supervision, "UNCONFIRMED_AFTER_SECONDS", 0.2)
    heartbeats = Heartbeats()
    heartbeats.live.add(A)
    supervisor = Supervisor(heartbeats, echo=lambda _: None)
    supervisor.spawn(A, "A", RUNS)
    try:
        supervisor.refresh()
        assert state_of(supervisor, A).unconfirmed_for is None

        time.sleep(0.3)

        assert state_of(supervisor, A).unconfirmed_for >= 0.2
        assert supervisor.heartbeat_error() is None
    finally:
        supervisor.stop_all()


class Services:
    """Services in an rdflib graph, and the GraphDB client's `query` over them.

    The guard's query runs as it is written. A service is written the way the middleware writes
    one: bound to its resource, with an address and a heartbeat.
    """

    def __init__(self) -> None:
        self.graph = Graph()

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        rows = [
            {
                name: IRI(str(value)) if isinstance(value, URIRef) else value.toPython()
                for name, value in row.asdict().items()
            }
            for row in self.graph.query(query)
        ]
        return {"results": {"bindings": rows}}

    def service(
        self, resource: IRI, *, heartbeat_age: float | None, address: bool = True
    ) -> None:
        """Bind a service to `resource`. `heartbeat_age` None writes no heartbeat yet, and
        `address` False is a service that deregistered: the middleware removes its address."""
        service = URIRef(f"{resource}_service")
        self.graph.add((service, URIRef(SVC.isServiceOf), URIRef(resource)))
        if address:
            self.graph.add(
                (service, URIRef(SVC.address), Literal("http://127.0.0.1:40001"))
            )
        if heartbeat_age is not None:
            beat = datetime.now(UTC) - timedelta(seconds=heartbeat_age)
            self.graph.add(
                (
                    service,
                    URIRef(SVC.lastHeartbeat),
                    Literal(beat, datatype=XSD.dateTime),
                )
            )


PLANT = "http://example.org/plant#"
LIVE, KILLED, STARTING, DEREGISTERED, SECOND = (
    IRI(PLANT + local)
    for local in ("Live", "Killed", "Starting", "Deregistered", "Second")
)
FRESH = 2.0
STALE = MAX_HEARTBEAT_AGE_SECONDS * 3


@pytest.fixture
def services() -> Services:
    """One service of each kind the guard tells apart; only `LIVE` is live."""
    services = Services()
    services.service(LIVE, heartbeat_age=FRESH)
    services.service(KILLED, heartbeat_age=STALE)
    services.service(STARTING, heartbeat_age=None)
    services.service(DEREGISTERED, heartbeat_age=FRESH, address=False)
    return services


def test_a_resource_is_live_while_its_service_has_an_address_and_a_fresh_heartbeat(
    services,
):
    assert live_resources(services) == {LIVE}


def test_a_heartbeat_counts_as_fresh_up_to_the_age_it_is_given(services):
    assert live_resources(services, max_age_seconds=STALE * 2) == {LIVE, KILLED}


def test_the_guard_lets_a_launcher_go_on_when_no_service_is_live():
    services = Services()
    services.service(KILLED, heartbeat_age=STALE)
    services.service(DEREGISTERED, heartbeat_age=FRESH, address=False)

    refuse_if_live(reader(services), dataset="test-dataset")


def test_the_guard_refuses_while_a_service_is_live_and_names_every_live_resource(
    services,
):
    services.service(SECOND, heartbeat_age=FRESH)

    with pytest.raises(
        LiveInstanceError,
        match=r"^Live, Second carry a fresh heartbeat in test-dataset, so nothing was cleared",
    ):
        refuse_if_live(reader(services), dataset="test-dataset")


def test_the_guard_leaves_out_the_launchers_own_processes_that_may_not_have_deregistered(
    services,
):
    """A process of this launcher that a stop killed, or that exited on its own, may have left
    its service live, and the seed clears what it left. Any other live service still refuses."""
    refuse_if_live(reader(services), dataset="test-dataset", leave_out={LIVE})

    services.service(SECOND, heartbeat_age=FRESH)
    with pytest.raises(LiveInstanceError, match=r"^Second carries a fresh heartbeat"):
        refuse_if_live(reader(services), dataset="test-dataset", leave_out={LIVE})


def test_the_guard_refuses_as_a_failed_launch_when_the_graph_cannot_say_what_is_live():
    def away(workflows: Collection[IRI]) -> list[Service]:
        raise ConnectionError("the store is away")

    with pytest.raises(
        LaunchFailed, match="could not read which services are live: the store is away"
    ):
        refuse_if_live(away, dataset="test-dataset")


def test_a_resource_without_a_fragment_is_named_whole():
    """Another process's resource is an IRI this package did not choose."""
    unnamed = IRI("http://example.org/modules/7")

    assert display_names([SECOND, unnamed, LIVE]) == f"Live, Second, {unnamed}"


def test_the_guard_lets_a_forced_launcher_go_on_although_a_service_is_live(services):
    def unread(workflows: Collection[IRI]) -> list[Service]:
        raise AssertionError("a forced launch reads nothing")

    refuse_if_live(reader(services), dataset="test-dataset", force=True)
    refuse_if_live(unread, dataset="test-dataset", force=True)
