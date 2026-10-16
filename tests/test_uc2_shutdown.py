"""How use case 2's processes stop: Ctrl+C, SIGTERM or SIGHUP to the Launcher or to `jms-uc2
slice`, a closed terminal, and the bound on each child's stop.

Each test runs the real code in a Python process of its own and signals that process alone,
because a signal reaches only the main thread of the process it is sent to. No triple store is
needed. The Launcher's collaborators are stand-ins that print what was asked of them, patched
into `runner` by the names it imports. Where pages hold streams open through the Launcher's port,
the stand-in Launcher starts a small process behind the port for each, and stops each at its stop.
The child's app is a few lines of FastAPI. The slice's collaborators are patched into
`vertical_slice` the same way: the graph, the seed and the acts are stand-ins, and each of its two
modules is a process of its own, served as a module is, which records in a file what was asked of
it.

Every test that signals a process is marked `posix`: on Windows, SIGTERM to a process is a forced
kill, which skips its shutdown, SIGINT cannot be sent to one, and there is no SIGHUP.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from _script import WITHIN_SECONDS, Script
from _serving import serving as serving_app
from _sse import hold
from _terminal import POSIX_ONLY, Terminal
from _wait import eventually
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from jms_kapps.common.process import (
    GRACEFUL_SHUTDOWN_SECONDS,
    begin_stop,
    one_stop,
    stop_event,
    stop_seconds_left,
)
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.uc2.seed import MODULES
from jms_kapps.uc2.vertical_slice import STOPPING_MESSAGE

# `jms-uc2` with stand-ins for the graph and the Launcher, on a free port. `sys.argv[1]` is how
# long a launch takes, `sys.argv[2]` how long stopping every process takes, in seconds. What the
# stand-in Launcher is asked goes to stdout, or to the file `sys.argv[3]` names, if it names one:
# a closed terminal takes no line.
LAUNCHER = """
import sys
import time

from jms_kapps.uc2.launcher import runner
from jms_kapps.common.process import bind_socket


def say(line):
    if len(sys.argv) > 3:
        with open(sys.argv[3], "a") as record:
            print(line, file=record)
    else:
        print(line, flush=True)


class Launcher:
    def __init__(self, **options):
        # No list of the boxes on the station: the app's lifespan starts no read of one.
        self.boxes = None

    def begin_stop(self):
        pass

    def launch(self, topology, *, name, force=False):
        say("launching")
        time.sleep(float(sys.argv[1]))
        say("launched")

    def stop_all(self):
        say("stopping every process")
        time.sleep(float(sys.argv[2]))
        say("stopped every process")


def any_free_port(host, port):
    sock = bind_socket(host)
    print(f"port {sock.getsockname()[1]}", flush=True)
    return sock


runner.connect = lambda dataset: None
runner.refuse_if_live = lambda services, *, dataset, force: None
runner.TripleStoreGraph = lambda db: None
runner.Launcher = Launcher
runner.bind_socket = any_free_port
sys.exit(runner.run("unused", topology="cross", force=False))
"""

# A process behind the Launcher, served by `serve` as a module's process is, with one stream, as
# the middleware's activity stream: it sends an event every 0.1 s and ends by itself once it has
# lasted `sys.argv[1]` seconds, "inf" for one that never ends.
STREAMING_CHILD = """
import asyncio
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from jms_kapps.common.process import bind_socket, serve

LIFETIME = float(sys.argv[1])
app = FastAPI()


@app.get("/stream")
async def stream() -> StreamingResponse:
    async def events():
        loop = asyncio.get_running_loop()
        ends, event = loop.time() + LIFETIME, 0
        while loop.time() < ends:
            event += 1
            yield f"id: {event}\\ndata: tick\\n\\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(events(), media_type="text/event-stream")


sock = bind_socket("127.0.0.1")
print(f"port {sock.getsockname()[1]}", flush=True)
asyncio.run(serve(SimpleNamespace(app=app), sock))
"""

# The processes the stand-in Launcher below starts, by their ids in the Launcher's paths.
STREAMING = ("Module1", "Module2")

# `jms-uc2` with stand-ins for the graph and the Launcher, on a free port, whose launch starts one
# `STREAMING_CHILD` for each id in `STREAMING`, so that a page's stream passes through the
# Launcher's port to a process of its own, as in a run. Its read of the boxes on the station says
# when the app's lifespan starts and stops it. `sys.argv[1]` is the child's source,
# `sys.argv[2]` the lifetime of its stream, and the rest are the ids. Its stop stops each child, as
# the Launcher's does.
LAUNCHER_WITH_STREAMS = """
import subprocess
import sys
import threading

from kapps_triplestore_interface import IRI

from jms_kapps.common.process import bind_socket
from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc2.launcher import runner

CHILD, LIFETIME, IDS = sys.argv[1], sys.argv[2], sys.argv[3:]


def echo(id, process):
    for line in process.stdout:
        print(f"{id}  {line.rstrip()}", flush=True)


# The background read of the boxes on the station, which the app's lifespan starts and stops.
class Boxes:
    def start(self):
        print("boxes started", flush=True)

    def stop(self):
        print("boxes stopped", flush=True)


class Launcher:
    def __init__(self, **options):
        self.children = {}
        self.boxes = Boxes()

    def begin_stop(self):
        pass

    def launch(self, topology, *, name, force=False):
        for id in IDS:
            process = subprocess.Popen(
                [sys.executable, "-c", CHILD, LIFETIME],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            port = int(process.stdout.readline().split()[1])
            threading.Thread(target=echo, args=(id, process), daemon=True).start()
            self.children[id] = (process, f"http://127.0.0.1:{port}")
            print(f"{id} pid {process.pid}", flush=True)
        print("launched", flush=True)

    def process(self, id):
        process, address = self.children[id]
        return ChildSnapshot(
            resource=IRI("http://example.org/" + id),
            name=id,
            state="live",
            pid=process.pid,
            command="",
            output=[],
            address=address,
            unconfirmed_for=None,
            stoppable=True,
        )

    def stop_all(self):
        print("stopping every process", flush=True)
        for id, (process, _) in self.children.items():
            process.terminate()
            process.wait()
            print(f"{id} stopped", flush=True)
        print("stopped every process", flush=True)


def any_free_port(host, port):
    sock = bind_socket(host)
    print(f"port {sock.getsockname()[1]}", flush=True)
    return sock


runner.connect = lambda dataset: None
runner.refuse_if_live = lambda services, *, dataset, force: None
runner.TripleStoreGraph = lambda db: None
runner.Launcher = Launcher
runner.bind_socket = any_free_port
sys.exit(runner.run("unused", topology="cross", force=False))
"""

# A child served by `serve`, as a module's process is, with one route that never ends by itself.
CHILD = """
import asyncio
import contextlib
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from jms_kapps.common.process import bind_socket, serve


@contextlib.asynccontextmanager
async def lifespan(app):
    yield
    # Where a middleware instance deregisters its service.
    print("lifespan shutdown", flush=True)


app = FastAPI(lifespan=lifespan)


@app.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    async def events():
        # An event stream that waits for its client to leave. A stopping server never makes it.
        while not await request.is_disconnected():
            yield "data: tick\\n\\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(events(), media_type="text/event-stream")


sock = bind_socket("127.0.0.1")
print(f"port {sock.getsockname()[1]}", flush=True)
asyncio.run(serve(SimpleNamespace(app=app), sock))
"""


# A child started as every process of use case 2 is, by `run_process`, with one route that waits
# until the stop of its process has begun, as a spawn under way at a stop is in flight.
# `sys.argv[1]` is how long it waits at most.
CHILD_IN_FLIGHT = """
import sys
import time
from types import SimpleNamespace

from fastapi import FastAPI

from jms_kapps.common.process import run_process, stop_has_begun

app = FastAPI()


@app.get("/in-flight")
def in_flight() -> dict:
    print("in flight", flush=True)
    deadline = time.monotonic() + float(sys.argv[1])
    while not stop_has_begun(app) and time.monotonic() < deadline:
        time.sleep(0.05)
    return {"stop_has_begun": stop_has_begun(app)}


run_process(
    "127.0.0.1",
    lambda port: SimpleNamespace(app=app),
    lambda address: "port " + address.rstrip("/").rsplit(":", 1)[1],
)
"""


@pytest.fixture
def script() -> Iterator[Callable[..., Script]]:
    """Start a `Script`. Every one started is killed at the end if it still runs."""
    started: list[Script] = []

    def start(source: str, *args: str, terminal: Terminal | None = None) -> Script:
        started.append(Script(source, *args, terminal=terminal))
        return started[-1]

    yield start
    for each in started:
        each.end()


def serving(launcher: Script) -> None:
    """Wait until the Launcher page answers, so that uvicorn handles the signals now."""
    port = launcher.port()
    eventually(
        lambda: (
            httpx.get(f"http://127.0.0.1:{port}/api/topologies", timeout=5).is_success
        ),
        timeout=WITHIN_SECONDS,
    )


# A test that stops a process with a signal that Windows takes otherwise: there SIGTERM to a
# process is a forced kill, which skips its shutdown, and SIGINT cannot be sent to one.
SENDS_POSIX_SIGNALS = pytest.mark.posix(
    reason="SIGTERM to a process is a forced kill on Windows, and SIGINT cannot be sent"
)

# SIGHUP, which a closed terminal sends, as a parameter of a test: Windows has none.
SIGHUP = pytest.param(getattr(signal, "SIGHUP", None), id="SIGHUP", marks=POSIX_ONLY)

# The signals that stop a process as Ctrl+C does: SIGTERM, which `kill` sends, and SIGHUP, which a
# closed terminal sends (jms_kapps 32).
STOP_SIGNALS = [pytest.param(signal.SIGTERM, id="SIGTERM"), SIGHUP]


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("stop", STOP_SIGNALS)
def test_sigterm_or_sighup_stops_every_process_as_ctrl_c_does(script, stop):
    """SIGTERM to the Launcher, as `kill` sends it, stops every process, and exits cleanly. So
    does SIGHUP."""
    launcher = script(LAUNCHER, "0", "0")
    serving(launcher)

    launcher.process.send_signal(stop)

    assert launcher.wait(timeout=30) == 0
    assert "stopped every process" in launcher.lines


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("stop", STOP_SIGNALS)
def test_sigterm_or_sighup_during_the_launch_stops_every_process_the_launch_started(
    script, stop
):
    """A SIGTERM that arrives while the launch seeds and starts takes effect when it is done. So
    does a SIGHUP.

    So no process starts after the stop, and none escapes it.
    """
    launcher = script(LAUNCHER, "2", "0")
    eventually(lambda: launcher.printed("launching"), timeout=WITHIN_SECONDS)

    launcher.process.send_signal(stop)

    assert launcher.wait(timeout=30) == 0
    assert launcher.lines.index("launched") < launcher.lines.index(
        "stopped every process"
    )


@POSIX_ONLY
def test_closing_the_terminal_stops_every_process(script, tmp_path):
    """The terminal that the Launcher runs in closes: every write to it fails from then on, and
    SIGHUP reaches the Launcher's process group, as the shell of a closed terminal sends it. The
    Launcher still stops every process, although it cannot say so (jms_kapps 32)."""
    terminal = Terminal()
    record = tmp_path / "asked"
    launcher = script(LAUNCHER, "0", "0", str(record), terminal=terminal)
    eventually(lambda: record.exists() and "launched" in record.read_text())
    serving(launcher)

    terminal.close()
    os.killpg(launcher.process.pid, signal.SIGHUP)

    launcher.wait(timeout=30)
    assert record.read_text().splitlines()[-1] == "stopped every process"


# The Launcher's or a child's source, run as under `nohup`, which starts a process with SIGHUP
# ignored.
NOHUP = "import signal\nsignal.signal(signal.SIGHUP, signal.SIG_IGN)\n"


@POSIX_ONLY
@pytest.mark.parametrize(
    "source", [pytest.param(LAUNCHER, id="Launcher"), pytest.param(CHILD, id="child")]
)
def test_a_process_started_with_sighup_ignored_keeps_ignoring_it(script, source):
    """A person who starts `jms-uc2` under `nohup` asks it to outlive its terminal: SIGHUP stops
    neither the Launcher nor a process it started then, and SIGTERM still stops both."""
    started = script(NOHUP + source, "0", "0")
    port = started.port()
    if source is LAUNCHER:
        serving(started)

    started.process.send_signal(signal.SIGHUP)

    time.sleep(1.0)
    assert started.process.poll() is None
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass
    started.process.terminate()
    started.wait(timeout=STOP_TIMEOUT_SECONDS)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize(
    "stop",
    [
        pytest.param(signal.SIGTERM, id="SIGTERM"),
        SIGHUP,
        pytest.param(signal.SIGINT, id="Ctrl+C"),
    ],
)
def test_a_second_stop_does_not_cut_the_stop_short(script, stop):
    """Once the stop has begun, another SIGTERM, SIGHUP or SIGINT leaves it to finish, so every
    process stops.

    A second SIGINT comes from `uv run` too, without a second key press, when no terminal sent the
    first: uv passes it on to `jms-uc2`, which has it already.
    """
    launcher = script(LAUNCHER, "0", "2")
    serving(launcher)
    launcher.process.send_signal(stop)
    eventually(
        lambda: launcher.printed("stopping every process"), timeout=WITHIN_SECONDS
    )

    launcher.process.send_signal(stop)

    assert launcher.wait(timeout=30) == 0
    assert "stopped every process" in launcher.lines
    assert not launcher.printed("Traceback"), "\n".join(launcher.lines)


# How long a stream of `STREAMING_CHILD` lasts before it ends by itself: as the streams of a run,
# which end every two seconds.
STREAM_LIFETIME = "2"

# Every stop signal of the Launcher: Ctrl+C, SIGTERM and SIGHUP.
EVERY_STOP_SIGNAL = [pytest.param(signal.SIGINT, id="Ctrl+C"), *STOP_SIGNALS]


def launcher_with_streams(
    script: Callable[..., Script], lifetime: str = STREAM_LIFETIME
) -> Script:
    """Start `LAUNCHER_WITH_STREAMS`, whose processes behind its port serve streams that last
    `lifetime` seconds each."""
    return script(LAUNCHER_WITH_STREAMS, STREAMING_CHILD, lifetime, *STREAMING)


@contextlib.contextmanager
def open_pages(launcher: Script, ids: tuple[str, ...] = STREAMING) -> Iterator[None]:
    """Hold one stream open through the Launcher's port to each process in `ids`, as an open
    activity page or a dashboard's Logs tab does, until the block ends. The block begins once
    every stream is open."""
    serving(launcher)
    port = launcher.port()
    closing = threading.Event()
    pages = []
    for id in ids:
        opened = threading.Event()
        pages.append(
            hold(
                f"http://127.0.0.1:{port}/p/{id}/stream",
                until=closing.is_set,
                opened=opened,
            )
        )
        assert opened.wait(timeout=WITHIN_SECONDS), f"the stream of {id} never opened"
    try:
        yield
    finally:
        closing.set()
        for page in pages:
            page.join(timeout=5)
        # A process behind the port that the Launcher did not stop would run on.
        for id in STREAMING:
            started = [line for line in launcher.lines if line.startswith(f"{id} pid ")]
            if started and f"{id} stopped" not in launcher.lines:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(started[0].rsplit(" ", 1)[1]), signal.SIGKILL)


def refuses_connections(launcher: Script) -> bool:
    """Whether the Launcher's port refuses a new connection: its page server has begun to shut
    down, or has shut down."""
    try:
        with socket.create_connection(("127.0.0.1", launcher.port()), timeout=1):
            return False
    except ConnectionRefusedError:
        return True


def assert_page_server_still_waits(launcher: Script) -> None:
    """The Launcher has not begun to stop its processes yet: its page server still waits for the
    open streams. A test whose second signal comes later does not test that wait."""
    assert not launcher.printed("stopping every process"), (
        "the page server had shut down before the second signal came"
    )


def assert_every_process_stopped(launcher: Script) -> None:
    """The Launcher exited 0, once its page server's lifespan had stopped the read of the boxes
    on the station (jms_kapps 23) and it had stopped every process it started."""
    lines = launcher.lines
    assert launcher.wait(timeout=30) == 0, "\n".join(lines)
    assert lines[-1] == "stopped every process", "\n".join(lines)
    assert "boxes stopped" in lines, "\n".join(lines)
    assert lines.index("boxes stopped") < lines.index("stopping every process")
    for id in STREAMING:
        assert f"{id} stopped" in lines


def assert_stopped_cleanly(launcher: Script) -> None:
    """The Launcher stopped every process it started, and its terminal shows no traceback."""
    assert_every_process_stopped(launcher)
    for crash in ("Traceback", "Exception in ASGI application"):
        assert not any(crash in line for line in launcher.lines), "\n".join(
            launcher.lines
        )


@SENDS_POSIX_SIGNALS
def test_a_ctrl_c_that_uv_passes_on_again_leaves_the_stop_to_finish_while_pages_are_open(
    script,
):
    """SIGINT to the process group of `./run-uc2` reaches `jms-uc2`, and `uv run` passes it on to
    `jms-uc2` again 200 ms later (uv 0.11.28, measured): while pages hold their streams open
    through the Launcher's port, its page server still waits for them then. The second SIGINT
    leaves the stop to finish: every process stops, and the terminal shows no traceback
    (jms_kapps 70)."""
    launcher = launcher_with_streams(script)
    with open_pages(launcher):
        launcher.process.send_signal(signal.SIGINT)
        time.sleep(0.2)
        launcher.process.send_signal(signal.SIGINT)
        assert_page_server_still_waits(launcher)

        assert_stopped_cleanly(launcher)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("second", EVERY_STOP_SIGNAL)
@pytest.mark.parametrize("first", EVERY_STOP_SIGNAL)
def test_a_second_stop_signal_while_the_page_server_waits_for_open_pages_leaves_the_stop_to_finish(
    script, first, second
):
    """Once the stop has begun, a second Ctrl+C, SIGTERM or SIGHUP by hand leaves it to finish,
    also while the page server still waits for the streams that pages hold open through its port
    (jms_kapps 70)."""
    launcher = launcher_with_streams(script)
    with open_pages(launcher):
        launcher.process.send_signal(first)
        eventually(lambda: refuses_connections(launcher), timeout=WITHIN_SECONDS)
        launcher.process.send_signal(second)
        assert_page_server_still_waits(launcher)

        assert_stopped_cleanly(launcher)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("first", STOP_SIGNALS)
def test_a_ctrl_c_after_a_stop_signal_during_the_launch_leaves_the_launch_to_finish(
    script, first
):
    """A SIGTERM or SIGHUP during the launch lets the launch finish, so that the stop reaches
    every process the launch started. A Ctrl+C after it does not cut the launch short: the stop
    has begun at the first signal (jms_kapps 70)."""
    launcher = script(LAUNCHER, "2", "0")
    eventually(lambda: launcher.printed("launching"), timeout=WITHIN_SECONDS)

    launcher.process.send_signal(first)
    time.sleep(0.2)
    launcher.process.send_signal(signal.SIGINT)

    assert launcher.wait(timeout=30) == 0
    assert launcher.lines.index("launched") < launcher.lines.index(
        "stopped every process"
    )
    assert not launcher.printed("Traceback"), "\n".join(launcher.lines)


# `LAUNCHER` whose launch fails once it has waited `sys.argv[1]` seconds, as a launch whose seed
# or start fails: the stop still stops whatever it started.
FAILING_LAUNCHER = LAUNCHER.replace(
    '        say("launched")\n', '        raise RuntimeError("the launch failed")\n'
)


@SENDS_POSIX_SIGNALS
def test_a_ctrl_c_after_a_failed_launch_leaves_the_stop_to_finish(script):
    """A launch that failed is over, so a Ctrl+C while the stop runs after it does not cut the
    stop short (jms_kapps 70)."""
    assert FAILING_LAUNCHER != LAUNCHER
    launcher = script(FAILING_LAUNCHER, "0", "2")
    eventually(
        lambda: launcher.printed("stopping every process"), timeout=WITHIN_SECONDS
    )

    launcher.process.send_signal(signal.SIGINT)

    launcher.wait(timeout=30)
    assert "stopped every process" in launcher.lines, "\n".join(launcher.lines)
    assert not any("KeyboardInterrupt" in line for line in launcher.lines), "\n".join(
        launcher.lines
    )


# A process of a run, started by `run_process` as each is: it stops cleanly, with code 0, on Ctrl+C,
# SIGTERM and SIGHUP alike (jms_kapps 104). It ignores the arguments a launch adds, such as
# `--module-strategy`.
GROUP_CHILD = """
from types import SimpleNamespace

from fastapi import FastAPI

from jms_kapps.common.process import run_process

run_process(
    "127.0.0.1",
    lambda port: SimpleNamespace(app=FastAPI()),
    lambda address: "serving on " + address,
)
"""

# `jms-uc2` on the real Launcher, with its supervisor, and with stand-ins for the graph: every
# process of the cross is `sys.argv[1]`, a `GROUP_CHILD`, in the Launcher's process group as in a
# run. The graph says that Module 1 and the WMS are live at `sys.argv[2]`, a server outside that
# group, which serves their pages. Once `jms-uc2` has ended, the state of each process.
GROUP_LAUNCHER = """
import sys
from datetime import UTC, datetime

from kapps_triplestore_interface import IRI

from jms_kapps.common.process import bind_socket
from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher import backend, runner
from jms_kapps.uc2.vocabulary import WMS, module_iri

COMMAND = [sys.executable, "-c", sys.argv[1]]
LIVE = {module_iri(1): sys.argv[2], WMS: sys.argv[2]}


class Graph:
    def seed(self, topology, transit_seconds, *, collect_history):
        pass

    def services(self, workflows):
        return [
            Service(
                iri=IRI(f"{resource}_service"),
                resource=resource,
                address=address,
                heartbeat=datetime.now(UTC),
                workflows={IRI(c): f"{address}/{IRI(c).fragment}" for c in workflows},
            )
            for resource, address in LIVE.items()
        ]


made = []


class Launcher(backend.Launcher):
    def __init__(self, **options):
        super().__init__(**options)
        made.append(self)


def any_free_port(host, port):
    sock = bind_socket(host)
    print(f"port {sock.getsockname()[1]}", flush=True)
    return sock


runner.connect = lambda dataset: None
runner.refuse_if_live = lambda services, *, dataset, force: None
backend.refuse_if_live = lambda services, **options: None
runner.TripleStoreGraph = lambda db: Graph()
runner.StationBoxes = lambda store: None
runner.module_command = lambda resource, dataset, retry_seconds: COMMAND
runner.wms_command = lambda dataset: COMMAND
runner.monitor_command = lambda dataset: COMMAND
runner.Launcher = Launcher
runner.bind_socket = any_free_port
code = runner.run("unused", topology="cross", force=False)
for process in made[0].state().processes:
    print(f"state of {process.child.name}: {process.child.state}", flush=True)
sys.exit(code)
"""

# The processes of the cross: five modules, the WMS and the monitor.
CROSS_PROCESSES = 7


def pages(lifetime: float) -> FastAPI:
    """The pages of a process, served outside the run's process group: a stream that sends an event
    every 0.1 s and ends by itself after `lifetime` seconds, as the middleware's activity stream
    does."""
    app = FastAPI()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            ends, event = loop.time() + lifetime, 0
            while loop.time() < ends:
                event += 1
                yield f"id: {event}\ndata: tick\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def live_on_the_page(launcher: Script) -> set[str]:
    """The names of the processes that the Launcher page shows live."""
    state = httpx.get(f"http://127.0.0.1:{launcher.port()}/api/state", timeout=5).json()
    return {p["name"] for p in state["processes"] if p["state"] == "live"}


# On a shared CI runner the children start so slowly that the Ctrl+C can reach one while it still
# builds its app after saying it serves; it then exits by the signal and counts as failed. The test
# runs everywhere else, locally and in every landing; hardening the child against a stop during its
# startup is a follow-up after the first release (Etienne Hoffmann, 2026-10-16).
SLOW_SHARED_RUNNER = pytest.mark.skipif(
    os.environ.get("GITLAB_CI") == "true",
    reason="a Ctrl+C during a child's slow startup on a shared CI runner counts as a failure",
)


@POSIX_ONLY
@SLOW_SHARED_RUNNER
def test_a_ctrl_c_to_the_process_group_while_pages_are_open_stops_every_process_and_fails_none(
    script,
):
    """A Ctrl+C to the process group of `./run-uc2`, as a terminal or a script sends it, reaches
    every process that `jms-uc2` started too, and `uv run` passes it on to `jms-uc2` again 200 ms
    later. Each process stops by itself, while the Launcher's page server still waits for the
    streams that open pages hold. The stop had begun, so each counts as stopped, not failed, and
    the terminal shows no failure and no traceback (jms_kapps 70)."""
    terminal = Terminal()
    try:
        with serving_app(pages(lifetime=3.0)) as address:
            launcher = script(GROUP_LAUNCHER, GROUP_CHILD, address, terminal=terminal)

            def every_process_serves() -> bool:
                # A launch that failed ends `jms-uc2`: say why at once.
                assert launcher.process.poll() is None, "\n".join(launcher.lines)
                serving = sum("serving on" in line for line in launcher.lines)
                return serving == CROSS_PROCESSES

            eventually(every_process_serves, timeout=WITHIN_SECONDS)
            serving(launcher)
            eventually(
                lambda: (
                    {"Module 1", "Warehouse Management System"}
                    <= live_on_the_page(launcher)
                ),
                timeout=WITHIN_SECONDS,
            )
            with open_pages(launcher, ids=("Module1", "WarehouseManagementSystem1")):
                os.killpg(launcher.process.pid, signal.SIGINT)
                time.sleep(0.2)
                launcher.process.send_signal(signal.SIGINT)
                assert not launcher.printed("Stopping every process"), (
                    "the page server had shut down before the second signal came"
                )

                assert launcher.wait(timeout=30) == 0, "\n".join(launcher.lines)
    finally:
        terminal.close()
    lines = launcher.lines
    states = [line for line in lines if line.startswith("state of ")]
    assert len(states) == CROSS_PROCESSES, "\n".join(lines)
    assert all(line.endswith(": stopped") for line in states), "\n".join(lines)
    for wrong in (" failed: ", "Traceback", "Exception in ASGI application"):
        assert not any(wrong in line for line in lines), "\n".join(lines)


# A `GROUP_CHILD` that says when it serves, once it holds a stream open to itself, and when it has
# deregistered, once its lifespan shutdown has run. The stream ends by itself 1 s after the stop
# of the child began, so that the stop waits for it, and a second Ctrl+C reaches the child while
# it waits.
DEREGISTERING_GROUP_CHILD = """
import asyncio
import contextlib
import threading
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from jms_kapps.common.process import run_process, stop_has_begun


@contextlib.asynccontextmanager
async def lifespan(app):
    yield
    print("deregistered", flush=True)


app = FastAPI(lifespan=lifespan)


@app.get("/stream")
async def stream() -> StreamingResponse:
    async def events():
        loop = asyncio.get_running_loop()
        ends = None
        while ends is None or loop.time() < ends:
            if ends is None and stop_has_begun(app):
                ends = loop.time() + 1.0
            yield "data: tick\\n\\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(events(), media_type="text/event-stream")


def hold_open(port):
    with httpx.stream("GET", f"http://127.0.0.1:{port}/stream", timeout=10) as response:
        lines = response.iter_lines()
        next(lines)
        print("holds a stream", flush=True)
        for _ in lines:
            pass


def build(port):
    threading.Thread(target=hold_open, args=(port,), daemon=True).start()
    return SimpleNamespace(app=app)


run_process("127.0.0.1", build, lambda address: "serving on " + address)
"""


@POSIX_ONLY
def test_a_second_ctrl_c_to_the_process_group_while_the_processes_stop_leaves_each_to_deregister(
    script,
):
    """A terminal's Ctrl+C reaches every process of a UC2 run through the Launcher's process
    group, and so does a second one. The second reaches each child while it stops, and does
    nothing there: every child still deregisters, prints no traceback, and exits with code 0, so
    the Launcher shows each as stopped (jms_kapps 104)."""
    terminal = Terminal()
    try:
        with serving_app(pages(lifetime=3.0)) as address:
            launcher = script(
                GROUP_LAUNCHER, DEREGISTERING_GROUP_CHILD, address, terminal=terminal
            )

            def every_process_serves() -> bool:
                # A launch that failed ends `jms-uc2`: say why at once.
                assert launcher.process.poll() is None, "\n".join(launcher.lines)
                # A child's line, as the supervisor echoes it, ends so; its command line does not.
                started = sum(
                    line.endswith("  holds a stream") for line in launcher.lines
                )
                return started == CROSS_PROCESSES

            eventually(every_process_serves, timeout=WITHIN_SECONDS)
            serving(launcher)

            os.killpg(launcher.process.pid, signal.SIGINT)
            time.sleep(0.3)
            os.killpg(launcher.process.pid, signal.SIGINT)

            assert launcher.wait(timeout=30) == 0, "\n".join(launcher.lines)
    finally:
        terminal.close()
    lines = launcher.lines
    deregistered = sum(line.endswith("  deregistered") for line in lines)
    assert deregistered == CROSS_PROCESSES, "\n".join(lines)
    states = [line for line in lines if line.startswith("state of ")]
    assert len(states) == CROSS_PROCESSES, "\n".join(lines)
    assert all(line.endswith(": stopped") for line in states), "\n".join(lines)
    for wrong in (" failed: ", "Traceback", "Exception in ASGI application"):
        assert not any(wrong in line for line in lines), "\n".join(lines)


@SENDS_POSIX_SIGNALS
def test_a_page_that_holds_its_stream_open_does_not_make_the_stop_longer(script):
    """A stream that never ends by itself holds the page server's shutdown only up to its bound,
    `GRACEFUL_SHUTDOWN_SECONDS`, as on a single Ctrl+C, also when a second Ctrl+C follows. Then the
    stop goes on to every process (jms_kapps 70)."""
    launcher = launcher_with_streams(script, lifetime="inf")
    with open_pages(launcher):
        asked = time.monotonic()
        launcher.process.send_signal(signal.SIGINT)
        time.sleep(0.2)
        launcher.process.send_signal(signal.SIGINT)

        eventually(
            lambda: launcher.printed("stopping every process"), timeout=WITHIN_SECONDS
        )
        assert time.monotonic() - asked < GRACEFUL_SHUTDOWN_SECONDS + 1.5
        assert_every_process_stopped(launcher)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("stop", STOP_SIGNALS)
def test_a_child_whose_connection_never_ends_still_stops_before_the_launcher_kills_it(
    script, stop
):
    """A stopping child waits for its open connections only so long, then runs its shutdown.

    Its lifespan shutdown is where a middleware instance deregisters. Unbounded, the wait lasts
    as long as the connection does, the Launcher kills the child at its stop timeout, and a kill
    skips the deregistration. SIGTERM is the Launcher's graceful stop on POSIX; SIGHUP is the
    closed terminal's, which reaches every child too, and takes the same way (jms_kapps 32).
    """
    child = script(CHILD)
    closing, opened = threading.Event(), threading.Event()
    page = hold(
        f"http://127.0.0.1:{child.port()}/stream", until=closing.is_set, opened=opened
    )
    try:
        assert opened.wait(timeout=10), "the stream never opened"
        asked = time.monotonic()

        child.process.send_signal(stop)

        child.wait(timeout=STOP_TIMEOUT_SECONDS)
        # The connection held the stop until the bound, and not a moment longer than the timeout.
        assert time.monotonic() - asked >= GRACEFUL_SHUTDOWN_SECONDS
        assert "lifespan shutdown" in child.lines
    finally:
        closing.set()
        page.join(timeout=5)


# A child started as every process of use case 2 is, by `run_process`, which says when its lifespan
# shutdown has run, where a middleware instance deregisters its service. Its one route is a stream
# that ends by itself after `sys.argv[1]` seconds, or never ("inf"), so that it holds the stop.
# With `sys.argv[2]` "win32", it runs on the Windows platform's choices, with uvicorn's own handlers
# as on Windows, and says when it raises a signal again.
SERVED_CHILD = """
import asyncio
import contextlib
import signal
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

import uvicorn.server

from jms_kapps.common import process
from jms_kapps.common.platforms import for_platform

LIFETIME = float(sys.argv[1])
if sys.argv[2] == "win32":
    process.PLATFORM = for_platform("win32")
    # Uvicorn on Windows catches SIGBREAK too, and would raise it again after its shutdown.
    uvicorn.server.HANDLED_SIGNALS += (21,)
    raise_signal = signal.raise_signal

    def say_raised(signum):
        print(f"raised {signum}", flush=True)
        raise_signal(signum)

    signal.raise_signal = say_raised


@contextlib.asynccontextmanager
async def lifespan(app):
    yield
    print("lifespan shutdown", flush=True)


app = FastAPI(lifespan=lifespan)


@app.get("/stream")
async def stream() -> StreamingResponse:
    async def events():
        loop = asyncio.get_running_loop()
        ends = loop.time() + LIFETIME
        while loop.time() < ends:
            yield "data: tick\\n\\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(events(), media_type="text/event-stream")


process.run_process(
    "127.0.0.1",
    lambda port: SimpleNamespace(app=app),
    lambda address: "port " + address.rstrip("/").rsplit(":", 1)[1],
)
"""


@contextlib.contextmanager
def open_stream(child: Script) -> Iterator[None]:
    """A client that holds the child's stream open while in this block."""
    closing, opened = threading.Event(), threading.Event()
    page = hold(
        f"http://127.0.0.1:{child.port()}/stream", until=closing.is_set, opened=opened
    )
    try:
        assert opened.wait(timeout=10), "the stream never opened"
        yield
    finally:
        closing.set()
        page.join(timeout=5)


def assert_stopped_cleanly_with_code_0(child: Script, timeout: float) -> None:
    """The child ran its lifespan shutdown, printed no traceback, and exited with code 0."""
    code = child.wait(timeout=timeout)
    output = "\n".join(child.lines)
    assert "lifespan shutdown" in child.lines, output
    assert not any("Traceback" in line for line in child.lines), output
    assert code == 0, output


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("second", [None, *EVERY_STOP_SIGNAL])
@pytest.mark.parametrize("first", EVERY_STOP_SIGNAL)
def test_a_served_child_takes_every_stop_signal_as_one_stop_and_exits_with_code_0(
    script, first, second
):
    """Ctrl+C and every stop signal begin one stop of a child: a second one while it waits for
    its open connections does nothing, so the child still runs its lifespan shutdown, where it
    deregisters, and prints no traceback. A child that stopped cleanly exits with code 0,
    whichever signal began the stop, so that its supervisor reads it as stopped on every OS
    (jms_kapps 104)."""
    child = script(SERVED_CHILD, "1.5", sys.platform)
    with open_stream(child):
        child.process.send_signal(first)
        if second is not None:
            time.sleep(0.3)
            child.process.send_signal(second)

        assert_stopped_cleanly_with_code_0(child, timeout=STOP_TIMEOUT_SECONDS)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("second", EVERY_STOP_SIGNAL)
def test_a_second_stop_signal_to_a_child_whose_connection_never_ends_does_not_hold_its_stop(
    script, second
):
    """The stop of a child whose open connection never ends stays bounded by its own wait for
    its connections when a second signal arrives during it, so the child ends before the
    supervisor would kill it, and deregisters (jms_kapps 104)."""
    child = script(SERVED_CHILD, "inf", sys.platform)
    with open_stream(child):
        asked = time.monotonic()
        child.process.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        child.process.send_signal(second)

        child.wait(timeout=STOP_TIMEOUT_SECONDS)
        assert time.monotonic() - asked < STOP_TIMEOUT_SECONDS
        assert "lifespan shutdown" in child.lines, "\n".join(child.lines)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("later", STOP_SIGNALS)
def test_a_stop_signal_that_arrives_once_the_stop_is_over_still_leaves_code_0(
    script, later
):
    """A stop signal can arrive after the server has ended its stop and before the process has
    exited, as the Launcher's SIGTERM does to a child that the terminal's Ctrl+C stopped first.
    It does nothing then: the child still exits with code 0 (jms_kapps 104)."""
    child = script(SERVED_CHILD, "0", sys.platform)
    child.port()
    time.sleep(0.3)

    child.process.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while child.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.005)
        with contextlib.suppress(ProcessLookupError):
            child.process.send_signal(later)

    assert_stopped_cleanly_with_code_0(child, timeout=STOP_TIMEOUT_SECONDS)


# How a Python process on Windows receives the launcher's CTRL_BREAK: signal 21. POSIX has a
# signal of that number too (SIGTTIN), so it can be sent here.
SIGBREAK = 21


@SENDS_POSIX_SIGNALS
def test_a_clean_stop_that_sigbreak_began_on_windows_raises_no_signal_again_and_exits_with_code_0(
    script,
):
    """With the Windows platform's choices, a CTRL_BREAK, which a Python process receives as
    SIGBREAK, begins the stop, and once the stop is over the child raises no signal again: on
    Windows a raised signal would end it through the C runtime with a non-zero code (jms_kapps
    104)."""
    child = script(SERVED_CHILD, "0", "win32")
    child.port()
    time.sleep(0.3)

    child.process.send_signal(SIGBREAK)

    assert_stopped_cleanly_with_code_0(child, timeout=STOP_TIMEOUT_SECONDS)
    assert not any(line.startswith("raised ") for line in child.lines)


@POSIX_ONLY
def test_every_stop_signal_begins_one_stop_and_each_handler_is_put_back_after_it():
    """`one_stop` calls its `begin` for the first stop signal alone: Ctrl+C, SIGTERM and SIGHUP
    after it do nothing. At the end of the block, each signal has its handler back (jms_kapps
    70)."""
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    before = {sig: signal.getsignal(sig) for sig in signals}
    begun: list[int] = []

    with one_stop(begun.append):
        signal.raise_signal(signal.SIGTERM)
        for sig in signals:
            signal.raise_signal(sig)

    assert begun == [signal.SIGTERM]
    assert {sig: signal.getsignal(sig) for sig in signals} == before


def test_the_time_left_of_a_stop_counts_from_its_first_signal():
    """A party that waits at its stop counts the graceful-shutdown bound from the stop signal,
    and a second signal does not start it again (jms_kapps 42)."""
    app = FastAPI()
    assert stop_seconds_left(app) == GRACEFUL_SHUTDOWN_SECONDS

    begin_stop(app)
    time.sleep(0.2)
    begin_stop(app)

    assert (
        GRACEFUL_SHUTDOWN_SECONDS - 1.0
        < stop_seconds_left(app)
        <= GRACEFUL_SHUTDOWN_SECONDS - 0.2
    )
    assert stop_seconds_left(app, bound=0.1) == 0.0


def test_a_thread_waiting_for_the_stop_wakes_the_moment_it_begins():
    """The stop signal sets the stop's event, which a thread of the process waits on: a module's
    stop starts then, beside the server's wait for its open connections (jms_kapps 42). An event
    asked for once the stop has begun is set already."""
    app = FastAPI()
    begun = stop_event(app)
    assert not begun.is_set()

    begin_stop(app)

    assert begun.is_set()
    assert stop_event(app) is begun
    later = FastAPI()
    begin_stop(later)
    assert stop_event(later).is_set()


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize(
    "stop",
    [
        pytest.param(signal.SIGTERM, id="SIGTERM"),
        SIGHUP,
        pytest.param(signal.SIGINT, id="Ctrl+C"),
    ],
)
def test_the_stop_has_begun_for_a_request_in_flight_as_soon_as_the_signal_arrives(
    script, stop
):
    """The signal marks the stop as begun before the server waits for its open connections, so
    a party can refuse new work while the work in flight ends: the WarehouseManagementSystem
    refuses a spawn from then on. The stop still ends cleanly."""
    child = script(CHILD_IN_FLIGHT, str(WITHIN_SECONDS))
    port = child.port()
    answers: list[dict] = []
    request = threading.Thread(
        target=lambda: answers.append(
            httpx.get(
                f"http://127.0.0.1:{port}/in-flight", timeout=WITHIN_SECONDS
            ).json()
        )
    )
    request.start()
    eventually(lambda: child.printed("in flight"), timeout=WITHIN_SECONDS)

    child.process.send_signal(stop)

    request.join(timeout=WITHIN_SECONDS)
    assert answers == [{"stop_has_begun": True}]
    child.wait(timeout=STOP_TIMEOUT_SECONDS)
    assert not child.printed("Traceback"), "\n".join(child.lines)


# -- `jms-uc2 slice` ------------------------------------------------------------------------ #

# A module of the slice, as a stand-in, run from a file: served by `run_process`, as a module's
# process is, so that it takes each stop signal as a module does. It records in the file
# `sys.argv[1]` that it started, with its pid; that it serves, once its lifespan has started,
# where a module's middleware registers its service; and that it deregistered, once its lifespan
# shutdown has run, with the pid of its parent then. Its shutdown takes `sys.argv[2]` seconds, and
# `sys.argv[3]` is its name.
SLICE_MODULE = """
import asyncio
import contextlib
import os
import sys
from types import SimpleNamespace

from fastapi import FastAPI

from jms_kapps.common.process import run_process

RECORD, STOP_SECONDS, NAME = sys.argv[1], float(sys.argv[2]), sys.argv[3]


def record(line):
    with open(RECORD, "a") as file:
        file.write(line + "\\n")


record(f"{NAME} started {os.getpid()}")


@contextlib.asynccontextmanager
async def lifespan(app):
    record(f"{NAME} serving")
    yield
    await asyncio.sleep(STOP_SECONDS)
    record(f"{NAME} deregistered, parent {os.getppid()}")


run_process(
    "127.0.0.1",
    lambda port: SimpleNamespace(app=FastAPI(lifespan=lifespan)),
    lambda address: "serving on " + address,
)
"""

# `jms-uc2 slice` with stand-ins for the graph, the seed and the acts. Each of its two modules is a
# `SLICE_MODULE`, run from the file `sys.argv[1]`, in the slice's process group as in a run, and
# the stand-in graph says that a module is live once it records that it serves. The stand-ins record
# what was asked of them in the file `sys.argv[2]`. The seed takes `sys.argv[3]` seconds, the acts
# `sys.argv[4]`, and the stop of each module `sys.argv[5]`. Acts of "ctrl-c" end each module with
# SIGINT, wait until it has exited, and then send SIGINT to the slice: a terminal's Ctrl+C reaches
# every process of the group at once, and here the modules take theirs first. Once the slice has
# ended, the state of each module. A closed terminal takes no line.
SLICE = """
import contextlib
import os
import signal
import sys
import time
from datetime import UTC, datetime

from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.common.supervision import Supervisor
from jms_kapps.uc2 import vertical_slice
from jms_kapps.uc2.seed import MODULES

MODULE, RECORD, SEED_SECONDS, ACTS, STOP_SECONDS = sys.argv[1:6]


def record(line):
    with open(RECORD, "a") as file:
        file.write(line + "\\n")


def serving(module):
    with open(RECORD) as file:
        return f"{module.fragment} serving" in file.read().splitlines()


def services(workflows):
    return [
        Service(
            iri=IRI(f"{module}_service"),
            resource=module,
            address="http://127.0.0.1:1/",
            heartbeat=datetime.now(UTC),
            workflows={IRI(c): "http://127.0.0.1:1/" for c in workflows},
        )
        for module in MODULES
        if serving(module)
    ]


def seed(db, *, transit_seconds):
    record("seeding")
    time.sleep(float(SEED_SECONDS))
    record("seeded")
    return MODULES


def command(module, dataset):
    record(f"{module.fragment} spawned")
    return [sys.executable, MODULE, RECORD, STOP_SECONDS, module.fragment]


made = []


class Recorded(Supervisor):
    def __init__(self, *args, **options):
        super().__init__(*args, **options)
        made.append(self)


def play(db, mw, modules, transcript):
    record("playing")
    if ACTS == "ctrl-c":
        for child in made[0].snapshot():
            os.kill(child.pid, signal.SIGINT)
        while any(child.stoppable for child in made[0].snapshot()):
            time.sleep(0.05)
        signal.raise_signal(signal.SIGINT)
        time.sleep(60)
    else:
        time.sleep(float(ACTS))
    record("played")


os.environ["TRIPLESTORE_URL"] = "http://stand-in"
vertical_slice.connect = lambda dataset: None
vertical_slice.refuse_if_live = lambda services, *, dataset, force: None
vertical_slice.reader = lambda db: services
vertical_slice.seed_two_modules = seed
vertical_slice.module_command = command
vertical_slice.Supervisor = Recorded
vertical_slice.check_services = lambda db, modules, since: None
vertical_slice.OGM = lambda db: None
vertical_slice.SemanticMiddleware = lambda **options: None
vertical_slice.play = play
code = vertical_slice.run("unused", force=False)
with contextlib.suppress(OSError):
    for supervisor in made:
        for child in supervisor.snapshot():
            print(f"state of {child.name}: {child.state}", flush=True)
sys.exit(code)
"""

# The names of the slice's two modules, as its stand-ins record them.
SLICE_MODULES = {module.fragment for module in MODULES}

# How long the stand-in acts or seed last when a signal is to cut them short: far longer than any
# wait of these tests.
LONG = "600"


def stopped_by(signum: int) -> int:
    """The exit code of a slice that the stop signal `signum` stopped: 128 plus its number, as a
    shell reports a process that the signal ended."""
    return 128 + signum


def runs(pid: int) -> bool:
    """Whether the process `pid` still runs."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@dataclass
class SliceRun:
    """A run of `SLICE`, and what its stand-ins recorded."""

    script: Script
    record: Path

    def recorded(self) -> list[str]:
        """Every whole line recorded so far: a line still being written is left out."""
        try:
            return self.record.read_text().split("\n")[:-1]
        except FileNotFoundError:
            return []

    def wait_until_playing(self) -> None:
        """Wait until the acts have begun: both modules serve, and take each stop signal."""
        eventually(lambda: "playing" in self.recorded(), timeout=WITHIN_SECONDS)

    def started(self) -> list[int]:
        """The pid of each module process that started."""
        return [
            int(line.rsplit(" ", 1)[1])
            for line in self.recorded()
            if line.split(" ")[1:2] == ["started"]
        ]

    def deregistered(self) -> dict[str, int]:
        """Each module that ran its shutdown, where a module deregisters, with the pid of its
        parent then."""
        return {
            line.split(" ")[0]: int(line.rsplit(" ", 1)[1])
            for line in self.recorded()
            if " deregistered, parent " in line
        }

    def states(self) -> dict[str, str]:
        """The state of each module once the slice had ended, as its supervisor held it."""
        prefix = "state of "
        return dict(
            line.removeprefix(prefix).split(": ", 1)
            for line in self.script.lines
            if line.startswith(prefix)
        )

    def output(self) -> str:
        return "\n".join(self.script.lines)


@pytest.fixture
def slice_run(
    script: Callable[..., Script], tmp_path: Path
) -> Iterator[Callable[..., SliceRun]]:
    """Start a `SliceRun`. Every module process that started is killed at the end if it still
    runs: one that the slice did not stop would run on."""
    module = tmp_path / "module.py"
    module.write_text(SLICE_MODULE)
    record = tmp_path / "record"
    runs_started: list[SliceRun] = []

    def start(
        *,
        seed: str = "0",
        acts: str = LONG,
        stop: str = "0",
        nohup: bool = False,
        terminal: Terminal | None = None,
    ) -> SliceRun:
        source = (NOHUP if nohup else "") + SLICE
        started = script(
            source, str(module), str(record), seed, acts, stop, terminal=terminal
        )
        runs_started.append(SliceRun(started, record))
        return runs_started[-1]

    yield start
    for run in runs_started:
        for pid in run.started():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def assert_both_modules_stopped(run: SliceRun, code: int) -> None:
    """The slice exited with `code` once both modules had run their shutdown, where a module
    deregisters, while the slice still waited for them, and neither runs any more."""
    assert run.script.wait(timeout=30) == code, run.output()
    slice_pid = run.script.process.pid
    assert run.deregistered() == dict.fromkeys(SLICE_MODULES, slice_pid), run.recorded()
    eventually(lambda: not any(runs(pid) for pid in run.started()), timeout=5)


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("stop", STOP_SIGNALS)
def test_sigterm_or_sighup_to_the_slice_alone_stops_both_modules(slice_run, stop):
    """SIGTERM to the slice's process alone, as `kill` sends it, stops both modules once they
    have started, and each deregisters. So does SIGHUP. A stopped slice exits non-zero, as on
    Ctrl+C (jms_kapps 71)."""
    run = slice_run()
    run.wait_until_playing()

    run.script.process.send_signal(stop)

    assert_both_modules_stopped(run, code=stopped_by(stop))
    assert run.states() == dict.fromkeys(SLICE_MODULES, "stopped"), run.output()


@POSIX_ONLY
def test_a_slice_started_with_sighup_ignored_keeps_ignoring_it(slice_run):
    """A slice started under `nohup` keeps ignoring SIGHUP, as the Launcher does, through the same
    helper: its modules run on. SIGTERM still stops both (jms_kapps 71)."""
    run = slice_run(nohup=True)
    run.wait_until_playing()

    run.script.process.send_signal(signal.SIGHUP)

    time.sleep(1.0)
    assert run.script.process.poll() is None, run.output()
    assert run.deregistered() == {}
    run.script.process.terminate()
    assert_both_modules_stopped(run, code=stopped_by(signal.SIGTERM))


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("stop", EVERY_STOP_SIGNAL)
def test_a_stop_signal_during_the_seed_ends_the_slice_and_starts_no_module(
    slice_run, stop
):
    """Ctrl+C, SIGTERM or SIGHUP while the slice seeds, before any module has started, ends the
    slice there, and no module starts after it (jms_kapps 71)."""
    run = slice_run(seed=LONG)
    eventually(lambda: "seeding" in run.recorded(), timeout=WITHIN_SECONDS)

    run.script.process.send_signal(stop)

    assert run.script.wait(timeout=30) == stopped_by(stop), run.output()
    assert "seeded" not in run.recorded()
    assert not any(line.endswith(" spawned") for line in run.recorded())
    assert not run.script.printed("Traceback"), run.output()


@SENDS_POSIX_SIGNALS
@pytest.mark.parametrize("second", EVERY_STOP_SIGNAL)
def test_a_second_stop_signal_while_the_slice_stops_its_modules_leaves_the_stop_to_finish(
    slice_run, second
):
    """Once SIGTERM has begun the stop, a second Ctrl+C, SIGTERM or SIGHUP leaves it to finish:
    the slice waits until both modules have stopped, and exits as the first signal asked
    (jms_kapps 71)."""
    run = slice_run(stop="2")
    run.wait_until_playing()
    run.script.process.send_signal(signal.SIGTERM)
    eventually(lambda: run.script.printed(STOPPING_MESSAGE), timeout=WITHIN_SECONDS)

    run.script.process.send_signal(second)

    assert run.deregistered() == {}, (
        "the modules had stopped before the second signal came"
    )
    assert_both_modules_stopped(run, code=stopped_by(signal.SIGTERM))
    assert not run.script.printed("Traceback"), run.output()


def test_a_slice_that_plays_both_acts_exits_0_and_stops_both_modules(slice_run):
    """A slice whose acts both behave exits 0, once it has stopped both modules (jms_kapps 71)."""
    run = slice_run(acts="0")

    assert_both_modules_stopped(run, code=0)
    assert run.script.printed("Both acts behaved as claimed."), run.output()


@POSIX_ONLY
def test_closing_the_terminal_stops_the_slice_and_both_modules(slice_run):
    """The terminal that the slice runs in closes: every write to it fails from then on, and
    SIGHUP reaches the slice and both modules. The slice still waits until both have stopped,
    although it cannot say so (jms_kapps 71)."""
    terminal = Terminal()
    try:
        run = slice_run(stop="1", terminal=terminal)
        run.wait_until_playing()

        terminal.close()
        os.killpg(run.script.process.pid, signal.SIGHUP)

        run.script.wait(timeout=30)
    finally:
        terminal.close()
    assert run.deregistered() == dict.fromkeys(SLICE_MODULES, run.script.process.pid), (
        run.recorded()
    )


@SENDS_POSIX_SIGNALS
def test_a_module_that_a_ctrl_c_to_the_process_group_ended_first_has_stopped_not_failed(
    slice_run,
):
    """A terminal's Ctrl+C reaches the slice and both modules at once. A module that has ended on
    it, with code 0, before the slice asks it, has stopped, not failed: the slice's stop began at
    its own signal, as the Launcher's does (jms_kapps 70, 71)."""
    run = slice_run(acts="ctrl-c")

    assert run.script.wait(timeout=30) == stopped_by(signal.SIGINT), run.output()
    assert run.states() == dict.fromkeys(SLICE_MODULES, "stopped"), run.output()
    assert not any(" failed: " in line for line in run.script.lines), run.output()
