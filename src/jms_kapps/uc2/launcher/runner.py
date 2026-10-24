"""`jms-uc2`: seed a topology, start its processes, serve the Launcher page, and stop on Ctrl+C.

SIGTERM stops it the same way, and so do SIGHUP, which a closed terminal sends, and a CTRL_BREAK on
Windows: every process it started is stopped, and deregisters its service. The platform's switch
says which of these signals the platform has (`Platform.STOP_SIGNALS`, `Platform.HANGUP`). Once
the stop has begun, another Ctrl+C or stop signal to `jms-uc2` does not cut its stop short, from
the first signal to the end of the stop: not while the page's server still waits for the streams
that open pages hold, and not while every process stops (`one_stop`). A closed terminal takes no
line, and the stop goes on without one.

The page is on one fixed port, the only address worth a bookmark, and the only port a person needs:
every other process takes a free port and registers it in the graph, and the Launcher passes each
process's pages through on its own port, under `/p/<id>/`. Over SSH, WSL or VS Code, forwarding
this one port reaches every page of the run. The fixed port is not 8080, which the middleware's
transferunits demo takes.
"""

from __future__ import annotations

import contextlib
import signal
import sys
from collections.abc import Iterator

import uvicorn

from jms_kapps.common.process import bind_socket, one_stop, server_config
from jms_kapps.common.services import reader
from jms_kapps.common.supervision import LaunchFailed, LiveInstanceError, refuse_if_live
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher, TripleStoreGraph, launchable
from jms_kapps.uc2.launcher.boxes import StationBoxes
from jms_kapps.uc2.module import DEFAULT_DEADLOCK_ALARM_SECONDS, DEFAULT_RETRY_SECONDS
from jms_kapps.uc2.monitor.process import monitor_command
from jms_kapps.uc2.process import module_command
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS
from jms_kapps.uc2.strategies import REAL
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.wms_process import wms_command

LAUNCHER_HOST = "127.0.0.1"
LAUNCHER_PORT = 8090


class _PageServer(uvicorn.Server):
    """Uvicorn's server, which leaves every stop signal to the Launcher's own handler
    (`one_stop`), which sets `should_exit` on the first and does nothing on every later one.

    While it serves, uvicorn sets handlers of its own. A second Ctrl+C there forces its exit:
    its wait for the open streams ends at once, the lifespan shutdown is skipped, and every
    request still under way is cancelled, each with a traceback in the terminal. A page that holds
    a stream open through the Launcher's port, as an activity page or a dashboard's Logs tab, makes
    that wait last until its stream ends, up to two seconds. Once uvicorn has shut down, it puts
    back the handlers it found and raises the signal again. Here it does neither: its wait for the
    open streams keeps its bound, `GRACEFUL_SHUTDOWN_SECONDS`, whatever signals follow.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def run(
    dataset: str,
    *,
    topology: str,
    force: bool,
    transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
    retry_seconds: float = DEFAULT_RETRY_SECONDS,
    module_strategy: str = REAL,
    wms_strategy: str = REAL,
    collect_history: bool = False,
    deadlock_alarm_seconds: float = DEFAULT_DEADLOCK_ALARM_SECONDS,
) -> int:
    """Run use case 2 from `topology` against `dataset` until Ctrl+C, SIGTERM or SIGHUP, with
    each module of a topology without a transit time of its own taking `transit_seconds` to
    convey a box (which is the default of the Launcher page's field too), and each module waiting
    `retry_seconds` before it asks an occupied or reserved module again. The first launch runs the
    modules with the module strategy `module_strategy` and the WarehouseManagementSystem with the
    WMS strategy `wms_strategy`, which the Launcher page's two pickers then start at. With
    `collect_history`, the first
    launch's reset turns on the store's history collection, and the Launcher page's checkbox
    "collect history" then starts on. Each module of a launch that names no deadlock alarm time
    raises the alarm for a
    box that has waited `deadlock_alarm_seconds` for a Reserve, which is the default of the
    Launcher page's field "deadlock alarm (s)" too.

    Returns the exit code.
    """
    try:
        chosen = launchable(load_topology(topology))
    except ValueError as error:
        print(f"jms-uc2: {error}", file=sys.stderr)
        return 2
    db = connect(dataset)
    # `launch` below asks the same guard, because every launch must. Asked here first, the
    # refusal comes before the fixed port is bound and before anything is printed, and it names
    # `--force`, which only the command line has.
    try:
        refuse_if_live(reader(db), dataset=dataset, force=force)
    except LiveInstanceError as error:
        print(f"jms-uc2: {error} Or pass --force to clear it anyway.", file=sys.stderr)
        return 1
    except LaunchFailed as error:
        print(f"jms-uc2: {error}", file=sys.stderr)
        return 1
    # Before anything is seeded: a second Launcher must not clear the graph and then fail here.
    try:
        sock = bind_socket(LAUNCHER_HOST, LAUNCHER_PORT)
    except OSError as error:
        print(f"jms-uc2: port {LAUNCHER_PORT} is not free: {error}", file=sys.stderr)
        return 1

    address = f"http://{LAUNCHER_HOST}:{LAUNCHER_PORT}/"
    launcher = Launcher(
        graph=TripleStoreGraph(db),
        dataset=dataset,
        module_command=lambda resource: module_command(
            resource, dataset, retry_seconds=retry_seconds
        ),
        wms_command=wms_command(dataset),
        page_address=address,
        monitor_command=monitor_command(dataset),
        transit_seconds=transit_seconds,
        module_strategy=module_strategy,
        wms_strategy=wms_strategy,
        collect_history=collect_history,
        deadlock_alarm_seconds=deadlock_alarm_seconds,
        # A client of its own, so that its read in the background shares no connection with
        # the supervisor's.
        boxes=StationBoxes(connect(dataset)),
    )
    # The page's open connections are bounded as the children's are, because every child is
    # stopped only once the page's server has shut down.
    server = _PageServer(server_config(create_app(launcher)))
    launching = True

    def begin(signum: int) -> None:
        """Begin the stop: the page's server exits as soon as it serves, and then every process
        is stopped. A process that the same signal ended meanwhile, as a terminal's Ctrl+C ends
        every process of the run, has stopped, not failed. A signal during the launch lets the
        launch finish, so that the stop reaches every process the launch started; only a Ctrl+C
        cuts the launch short, as it always has.
        """
        launcher.begin_stop()
        server.should_exit = True
        if launching and signum == signal.SIGINT:
            raise KeyboardInterrupt

    with one_stop(begin):
        try:
            print(f"Seeding {dataset} with the topology {topology!r}", flush=True)
            launcher.launch(chosen, name=topology, force=force)
            launching = False
            server.run(sockets=[sock])
        except KeyboardInterrupt:
            # A Ctrl+C during the launch. A clean stop is not an error, so it does not read like
            # a crash.
            pass
        finally:
            # A launch that failed is over too: a first Ctrl+C from here on leaves the stop to
            # finish.
            launching = False
            # A terminal that has closed takes no line: the write fails, and the stop that its
            # SIGHUP began goes on.
            with contextlib.suppress(OSError):
                print("\nStopping every process...", flush=True)
            launcher.stop_all()
    return 0
