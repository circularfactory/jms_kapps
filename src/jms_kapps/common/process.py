"""The start that every process of the use cases shares: its command line, its port and its server.

Each participant of a use case runs as a process of its own, behind a middleware instance of its
own: a FlexConveyorModule, the WarehouseManagementSystem and the monitor of use case 2, and the
learner of use case 1. `process_parser` is the command line they share, and `run_process` binds a
free port, builds the middleware for it and serves it until a signal stops the process, with its
API docs by relative paths, so that they work through use case 2's Launcher as at the process's
own address. A process whose middleware has an activity feed shows its own records there too,
from INFO up, beside the middleware's: a module's and the WMS's activity page shows the steps that
process takes. Uvicorn runs on the main thread, so that a signal reaches it and the middleware
deregisters on the way out. SIGHUP, which a closing terminal sends, stops a process as SIGTERM does
(`hangup_stops`). Every process takes Ctrl+C and every stop signal as one stop (`one_stop`), so a
second one never cuts its stop short: each process this server serves, and use case 2's Launcher.
After a clean stop the server raises no signal again, so the process exits with code 0 on every OS,
whatever began its stop. (Changed on 2026-10-12, jms_kapps 104, decided by Sören: the demos are
OS-agnostic from the outside. Until then "A process that must not let a second stop signal cut its
stop short, as use case 2's Launcher, takes every stop signal as one stop (`one_stop`).", and a
process ended as the stop signal ends it.)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator
from types import FrameType

import uvicorn
from fastapi import FastAPI
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.activity import ActivityFeed

from jms_kapps.common.api_docs import relative_api_docs
from jms_kapps.common.platforms import PLATFORM
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import DEFAULT_DATASET

# How long a stopping process waits for its open connections. Then uvicorn cancels the requests
# that still run, and runs the lifespan shutdown, where the middleware deregisters the service.
# Without a bound, one connection that never ends holds the stop until the Launcher kills the
# process, and a kill skips the deregistration. The Launcher kills a process that has not exited
# `STOP_TIMEOUT_SECONDS` after it asked it to stop, so the bound is derived from that and takes
# half. The other half is for the deregistration and the exit, which take about 1 s, for what a
# party still writes once its own wait at the stop has ended, and a margin. A module waits at its
# stop for the box it reserved itself for and for its own work, this bound at most, counted from
# the stop signal and beside this wait, not after it. The middleware's activity stream ends by
# itself after 2 s, so an open activity page ends before the bound does. Uvicorn takes the bound
# in whole seconds. (Changed on 2026-10-06, jms_kapps 42, decided by Etienne: a module waits at its
# stop. Until then "The other half is for the deregistration and the exit, which take about 1 s,
# and the rest is a margin.")
GRACEFUL_SHUTDOWN_SECONDS = STOP_TIMEOUT_SECONDS // 2

# This package's logger: the parent of every logger of the use cases' code.
_OWN_LOGGER = "jms_kapps"


def bind_socket(host: str, port: int = 0) -> socket.socket:
    """Bind `port`, or an OS-assigned one, and keep the socket, so no other process takes it.

    An OS-assigned port is bound with address reuse, as the middleware's factory demo binds it. A
    fixed port, as the Launcher's page's, is bound with it only where it cannot let a second
    socket in (`Platform.REUSE_FIXED_PORT`): a second socket on the fixed port is how a second
    Launcher is refused.

    Raises:
        OSError: If the port is in use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if port == 0 or PLATFORM.REUSE_FIXED_PORT:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(1)
    return sock


def server_config(app: FastAPI) -> uvicorn.Config:
    """How a process serves `app`: with warnings only, and its stop bounded above.

    The children serve through `serve`. Use case 2's Launcher serves its page with this too, because it
    stops the children only once the page's server has shut down.
    """
    return uvicorn.Config(
        app=app,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


def begin_stop(app: FastAPI) -> None:
    """Mark the stop of the process that serves `app` as begun. `serve` does it the moment a
    stop signal arrives. The first mark keeps the time it was made, so that a party counts its
    waits from the first signal, and sets the stop's event (`stop_event`), which wakes a thread
    that waits for the stop. It takes no lock that the main thread holds, so it is safe in a
    signal handler. (Changed on 2026-10-06, jms_kapps 42, decided by Etienne: a module waits at
    its stop. Until then "One assignment and no lock, so it is safe in a signal handler.")"""
    if not stop_has_begun(app):
        app.state.stop_began_at = time.monotonic()
    app.state.stop_has_begun = True
    event = getattr(app.state, "stop_event", None)
    if event is not None:
        event.set()


def stop_event(app: FastAPI) -> threading.Event:
    """The event that the stop of the process that serves `app` sets the moment it begins, for a
    thread that waits for the stop: made at the first call, and set already if the stop has
    begun. The stop signal only sets it, so a thread, not the signal handler, does the work."""
    event = getattr(app.state, "stop_event", None)
    if event is None:
        event = app.state.stop_event = threading.Event()
        if stop_has_begun(app):
            event.set()
    return event


def stop_has_begun(app: FastAPI) -> bool:
    """Whether the stop of the process that serves `app` has begun: from the moment the stop
    signal arrives, while the requests in flight still run and before the middleware
    deregisters."""
    return bool(getattr(app.state, "stop_has_begun", False))


def stop_seconds_left(app: FastAPI, bound: float = GRACEFUL_SHUTDOWN_SECONDS) -> float:
    """How many seconds of `bound` are left since the stop of the process that serves `app`
    began: all of it while the stop has not begun, and 0 once it has passed. A party that waits
    at its stop, as a module waits for the box it reserved itself for, waits this long."""
    began = getattr(app.state, "stop_began_at", None)
    if began is None:
        return bound
    return max(0.0, began + bound - time.monotonic())


@contextlib.contextmanager
def hangup_stops(handler: Callable[[int, FrameType | None], object]) -> Iterator[None]:
    """While in this block, SIGHUP calls `handler`, the handler of this process's stop signals.

    A terminal that closes sends SIGHUP to every process it started: to `jms-uc2`, and to each
    process `jms-uc2` started, which share its process group. At its default action SIGHUP ends a
    process on the spot, and no middleware deregisters its service. Taken as a stop signal, it
    stops the process the way SIGTERM does, within the same bounds.

    Nothing changes where SIGHUP is ignored: a person who starts a run under `nohup` asks it to
    outlive the terminal, and every process the run starts inherits the ignored signal. Nothing
    changes on Windows, which has no SIGHUP, nor off the main thread, where Python sets no
    signal handler.
    """
    hangup = PLATFORM.HANGUP
    if (
        hangup is None
        or threading.current_thread() is not threading.main_thread()
        or signal.getsignal(hangup) == signal.SIG_IGN
    ):
        yield
        return
    previous = signal.signal(hangup, handler)
    try:
        yield
    finally:
        # None is a handler that Python did not install, which it cannot put back. The default
        # action takes its place.
        signal.signal(hangup, signal.SIG_DFL if previous is None else previous)


@contextlib.contextmanager
def one_stop(begin: Callable[[int], object]) -> Iterator[None]:
    """While in this block, every stop signal to this process begins one stop: the first calls
    `begin` with its number, and every later one does nothing.

    The stop signals are Ctrl+C (SIGINT), the platform's (`Platform.STOP_SIGNALS`: SIGTERM, which
    `kill` sends, and on Windows the signal that a CTRL_BREAK arrives as), and SIGHUP, which a
    closing terminal sends, unless it is ignored (`hangup_stops`). A second one comes from a second
    key press, and also without one: when a script or a test sends SIGINT to the process group of
    `uv run`, uv passes it on to its child again 200 ms later (uv 0.11.28, measured), and the
    child has it already. From a terminal, uv leaves it to the terminal. Left to act, a later
    signal would cut the stop short: the processes not stopped yet would keep running, killed
    later or not at all, and the stop would end in a traceback.

    `begin` runs on the main thread, between two steps of whatever runs there, and it may raise,
    as `KeyboardInterrupt`, to cut that short. The block must last until the stop has ended: a
    handler put back sooner lets a later signal cut the stop short again. Nothing else may set a
    handler for these signals inside the block; uvicorn does while it serves, unless its
    `capture_signals` is turned off. A process started inside the block takes each signal at its
    default action: a handler, unlike an ignored signal, is not inherited.

    Nothing changes off the main thread, where Python sets no signal handler. At the end of the
    block, each signal gets back the handler it had.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    begun = False

    def handler(signum: int, frame: FrameType | None) -> None:
        nonlocal begun
        if begun:
            return
        begun = True
        begin(signum)

    handlers_before = {
        sig: signal.signal(sig, handler)
        for sig in (signal.SIGINT, *PLATFORM.STOP_SIGNALS)
    }
    try:
        with hangup_stops(handler):
            yield
    finally:
        for sig, previous in handlers_before.items():
            # None is a handler that Python did not install, which it cannot put back. Python's
            # own takes its place: KeyboardInterrupt for SIGINT, the default action for the rest.
            if previous is None:
                previous = (
                    signal.default_int_handler
                    if sig == signal.SIGINT
                    else signal.SIG_DFL
                )
            signal.signal(sig, previous)


class _StopMarkingServer(uvicorn.Server):
    """Uvicorn's server, which takes Ctrl+C and every stop signal as one stop (`one_stop`) and
    marks the stop of its app as begun the moment the first arrives: before it waits for the
    open connections, and before the lifespan shutdown. A later signal does nothing, so it never
    makes uvicorn skip the shutdown, where the middleware deregisters. SIGHUP is a stop signal
    here too (`hangup_stops`)."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(server_config(app))
        self._app = app

    def _begin(self, signum: int) -> None:
        begin_stop(self._app)
        self.should_exit = True

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        """One stop for every signal, in place of uvicorn's handlers. Uvicorn's would force an
        exit on a second Ctrl+C, and raise each signal it caught again once the shutdown is over.
        No signal is raised again here, so a process that stopped cleanly exits with code 0 on
        every OS, whatever began its stop: on Windows a raised signal ends the process through
        the C runtime, with a non-zero code. (Changed on 2026-10-12, jms_kapps 104, decided by
        Sören: the demos are OS-agnostic from the outside. Until then "Uvicorn's own stop
        signals, and SIGHUP as one of them. SIGHUP's handler is set inside uvicorn's and put back
        first, so that uvicorn raises a SIGHUP again once the shutdown is over, as it raises a
        SIGTERM: the process then ends as that signal ends it.")"""
        with one_stop(self._begin):
            yield


async def serve(middleware: SemanticMiddleware, sock: socket.socket) -> None:
    """Serve the middleware's app on `sock` until a signal stops it, within the bound above.

    The signal marks the stop as begun at once (`stop_has_begun`), so a party can refuse new
    work while the requests in flight end.
    """
    await _StopMarkingServer(middleware.app).serve(sockets=[sock])


async def _serve_to_the_end(
    middleware: SemanticMiddleware, sock: socket.socket
) -> None:
    """Serve as `serve` does, and then ignore Ctrl+C and every stop signal until the process
    exits. The stop is over then, and the handlers that `one_stop` put back would let a late
    signal end a process that stopped cleanly with a non-zero code: the Launcher's SIGTERM to a
    child that a terminal's Ctrl+C had stopped first, for one. Only a process of its own does
    this, never `serve`, which a caller in the same process may go on from. (Added on
    2026-10-15, jms_kapps 104.)"""
    await serve(middleware, sock)
    for sig in (signal.SIGINT, *PLATFORM.STOP_SIGNALS):
        signal.signal(sig, signal.SIG_IGN)
    if PLATFORM.HANGUP is not None:
        signal.signal(PLATFORM.HANGUP, signal.SIG_IGN)


def process_parser(description: str) -> argparse.ArgumentParser:
    """The command line of a process with a middleware: its dataset, and the host it binds."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--dataset", default=DEFAULT_DATASET, help="The dataset of the triple store"
    )
    parser.add_argument("--host", default="127.0.0.1", help="The host to bind")
    return parser


def _log_to_stderr() -> None:
    """Log this package's records from INFO up, and every other logger's from WARNING up.

    This package's INFO records matter: a module logs each step it accepts, and the vertical slice
    reads those lines. So the level of the `jms_kapps` logger is set here. A library's INFO
    records, such as `httpx`'s one line per request, would bury those steps, so the root logger
    is at WARNING.

    The setup takes over with `force=True`, whatever a dependency did at import: the middleware's
    transport, `transitional_sync_middleware`, calls `basicConfig` at INFO and attaches a stderr
    handler to the root logger, and without `force` the call here would leave that handler and
    its level in place. It keeps Python's default format, so a line reads the same as before.

    The order matters. `force` removes every handler on the root logger at the moment of the call,
    which is only that import-time handler, so this runs before the middleware is built. The
    middleware's Logs-tab handler is attached to the root logger at build time, after this call. The
    middleware's activity handler sits on the middleware's package logger, and this package's
    (see `feed_own_log`) on the `jms_kapps` logger, so the call does not touch them.
    """
    logging.basicConfig(level=logging.WARNING, force=True)
    logging.getLogger(_OWN_LOGGER).setLevel(logging.INFO)


class _ActivityFeedHandler(logging.Handler):
    """Writes each record from INFO up into a middleware's activity feed, as the middleware's own
    handler writes the middleware's records: its time, its level, its logger and its message."""

    def __init__(self, feed: ActivityFeed) -> None:
        super().__init__(logging.INFO)
        self._feed = feed

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._feed.append(
                timestamp=record.created,
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
            )
        except Exception:  # noqa: BLE001 - reported as logging reports it, never raised
            self.handleError(record)


def feed_own_log(middleware: object) -> None:
    """Write this package's records, from INFO up, into the activity feed of `middleware`, next to
    the middleware's own entries. A middleware without a feed, as UC1's learner's, is left as it
    is. `run_process` calls it once in each process, so each record appears once.

    The middleware's feed listens to the middleware's own logger only, so without this a module's
    or the WMS's activity page shows the middleware's heartbeat and none of the process's steps.
    The records share the feed's capacity with the middleware's.
    """
    feed = getattr(middleware, "activity_feed", None)
    if feed is None:
        return
    logging.getLogger(_OWN_LOGGER).addHandler(_ActivityFeedHandler(feed))


def run_process(
    host: str,
    build_for_port: Callable[[int], SemanticMiddleware],
    announcement: Callable[[str], str],
) -> None:
    """Bind a free port on `host`, build the middleware for it, and serve until a signal stops it,
    with its API docs by relative paths (see `relative_api_docs`), and with this package's records
    on its activity page, if it has one (see `feed_own_log`).

    Args:
        host: The host to bind.
        build_for_port: Builds the middleware instance for the port that was bound.
        announcement: The line to print once the process is built, given the address it serves
            on.
    """
    _log_to_stderr()
    sock = bind_socket(host)
    port = sock.getsockname()[1]
    middleware = build_for_port(port)
    # Its docs, like each of its pages, work through the Launcher's port as at its own.
    relative_api_docs(middleware.app)
    feed_own_log(middleware)
    print(announcement(f"http://{host}:{port}/"), flush=True)
    try:
        asyncio.run(_serve_to_the_end(middleware, sock))
    except KeyboardInterrupt:
        # A Ctrl+C while the event loop starts, before the server takes the signals: nothing
        # serves yet, so nothing is left to stop. Once it serves, a Ctrl+C begins the stop as every stop signal does, and raises
        # nothing. (Changed on 2026-10-12, jms_kapps 104, decided by Sören. Until then "Uvicorn
        # has shut down cleanly by the time Ctrl+C arrives here.")
        pass
