"""One FlexConveyorModule as its own process, behind its own middleware instance.

The process registers the module's service and its three workflows in the graph, Reserve, Receive
and the BoxTransportWorkflow, serves them over REST, takes every notification through the
middleware, and answers one command outside the workflows: arming and disarming each of its two
faults, `POST /faults/{name}`. It serves its handover event stream, and commits where it lives at
startup. Its activity feed is on, and so is
the middleware's state dashboard: the library's own boxes, and a tab named after the module with
three of its own, which draw the module from above and show its routing decisions and the
connections it read (`module_dashboard`). Uvicorn runs on the main thread, so that a signal reaches
it and the middleware deregisters on the way out. From the stop signal on, the module waits, the
graceful-shutdown bound at most, for the box it reserved itself for and for its own work, and ends
the transport that is its to end; the shutdown waits for that.

The move of a box off the module, its convey, is simulated: it waits the module's own transit time,
which the module reads from the graph once, when it starts (`fc:hasTransitTime`, written by the
launch's seed), and not at all where that is 0. A module whose graph holds none, or more than one,
does not start: its process ends with the reason.
A real FlexConveyor passes a move that drives its motor, and nothing else changes. Convey
is no workflow of a module: no peer calls it.

Run it as `python -m jms_kapps.uc2.process --resource-iri <module IRI>`, the command line
`module_command` builds. The Launcher does.

The WarehouseManagementSystem and the monitor run the same way, each behind a middleware instance
of its own. The three share the command line and the start of `common.process`, `process_parser`
and `run_process`, and the WarehouseManagementSystem also takes the module's call of a peer and
its notification helper.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import math
import os
import sys
import threading
import time
from typing import cast

import httpx
from fastapi import FastAPI, HTTPException
from kapps_ogm import OGM, Node
from kapps_semantic_middleware import Mode, SemanticMiddleware, possession
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.common.heartbeats import HeartbeatSettingRefused, heartbeat_interval
from jms_kapps.common.process import (
    begin_stop,
    process_parser,
    run_process,
    stop_event,
    stop_has_begun,
    stop_seconds_left,
)
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.discovery import GraphView, TransitTimeError, transit_times
from jms_kapps.uc2.handover_events import (
    HandoverEvents,
    mount_handover_events,
    stream_locator,
)
from jms_kapps.uc2.handshake import (
    Move,
    Notify,
    ReceiveRequest,
    ReceiveResult,
    ReserveRequest,
    ReserveResult,
)
from jms_kapps.uc2.module import (
    ARRIVAL_BOUND_SECONDS,
    DEFAULT_DEADLOCK_ALARM_SECONDS,
    DEFAULT_RETRY_SECONDS,
    FAULTS,
    Fault,
    FlexConveyorModule,
)
from jms_kapps.uc2.module_dashboard import register_boxes
from jms_kapps.uc2.strategies import (
    REAL,
    REAL_MODULE_STRATEGY,
    ModuleStrategy,
    UnknownStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.transport import TRANSPORT_SCOPE, TransportRecords, transport_from
from jms_kapps.uc2.vocabulary import FC

# A handshake step answers at once: Reserve from the graph, and Receive before the receiving module
# carries the box on.
STEP_TIMEOUT_SECONDS = 30.0

FAULTS_ROUTE = "/faults"

# The classes of the three workflows a module's process registers (`create_middleware`): a module
# is ready for its peers, and the run core's readiness gate lets it through, only once its service
# has every one of them.
MODULE_WORKFLOWS = (FC.ReserveWorkflow, FC.ReceiveWorkflow, FC.BoxTransportWorkflow)

# How long, counted from the stop signal, the shutdown of a module's process waits for the module's
# stop: its wait for the box it reserved itself for and for its own work, the graceful-shutdown
# bound at most, and the writes that follow. The Launcher kills a process `STOP_TIMEOUT_SECONDS`
# after it asked it to stop, and the middleware's last callbacks and the exit take up to about 2 s.
MODULE_STOP_SECONDS = STOP_TIMEOUT_SECONDS - 2.0

logger = logging.getLogger(__name__)


class FaultCommand(BaseModel):
    armed: bool


def module_command(
    resource: str,
    dataset: str,
    *,
    retry_seconds: float = DEFAULT_RETRY_SECONDS,
) -> list[str]:
    """The command line that runs the module `resource` against `dataset`, with
    `retry_seconds` between two Reserves it asks of an occupied or reserved module. The module's
    transit time is not on it: the module reads it from the graph."""
    return [
        sys.executable,
        "-m",
        "jms_kapps.uc2.process",
        "--resource-iri",
        str(resource),
        "--dataset",
        dataset,
        "--retry-time",
        str(retry_seconds),
    ]


def transit(seconds: float) -> Move:
    """The simulated move of a box: it waits `seconds`, then the box is there. At 0 it waits not
    at all."""

    def move(box: object, receiver: object) -> None:
        if seconds > 0:
            time.sleep(seconds)

    return move


def notify_with(middleware: SemanticMiddleware) -> Notify:
    """The middleware's notification, as a party sends one: `notify(operation, resource)`."""

    def notify(operation: object, resource: object) -> None:
        middleware.notify(operation=operation, resource=resource)

    return notify


def set_fault_switch(address: str, name: Fault, armed: bool) -> None:
    """Arm or disarm the fault `name` of the module whose service is at `address`, through its
    route.

    Raises:
        httpx.HTTPError: If the module cannot be reached, or does not take the command.
    """
    httpx.post(
        f"{address.rstrip('/')}{FAULTS_ROUTE}/{name}",
        json=FaultCommand(armed=armed).model_dump(),
        timeout=5.0,
    ).raise_for_status()


def mount_fault_switches(app: FastAPI, module: FlexConveyorModule) -> None:
    """Serve `module`'s fault switches on `app`: `POST /faults/{name}` with `{"armed": ...}`.

    Not a workflow: a fault is an operator's switch on this process, not a capability of the
    module, so the graph names none. A restart builds a new module, with every fault off. The
    route is left out of the module's API docs, which the Launcher page links: the Launcher is the
    writer of the switches, and holds what it commanded, so a person switches a fault there.
    """

    @app.post(FAULTS_ROUTE + "/{name}", include_in_schema=False)
    def switch(name: str, command: FaultCommand) -> FaultCommand:
        """Arm or disarm one fault of this module: `skip-reserve` or
        `complete-without-releasing`."""
        if name not in FAULTS:
            raise HTTPException(
                404, f"no fault {name!r}; the faults are {', '.join(FAULTS)}"
            )
        fault = cast(Fault, name)
        module.faults[fault] = command.armed
        return FaultCommand(armed=module.faults[fault])


def open_handover_event_stream(
    middleware: SemanticMiddleware, host: str, port: int
) -> HandoverEvents:
    """The handover event stream of the participant behind `middleware`, served on its app.

    Where the stream lives is committed in one `mw.commit()`, which replaces what an earlier start
    left, so a monitor finds it through the graph. The app keeps the stream as
    `app.state.handover_events`.
    """
    resource = IRI(str(middleware.resource))
    events = HandoverEvents(resource)
    mount_handover_events(middleware.app, events)
    with middleware.commit(entry=stream_locator(resource, f"http://{host}:{port}/")):
        pass
    middleware.app.state.handover_events = events
    return events


def call_peer(
    endpoint: str, request: BaseModel, timeout: float = STEP_TIMEOUT_SECONDS
) -> dict:
    """POST `request` to a peer's workflow endpoint and return its answer."""
    response = httpx.post(endpoint, json=request.model_dump(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def build(
    resource_iri: str,
    ogm: OGM,
    host: str,
    port: int,
    *,
    retry_seconds: float = DEFAULT_RETRY_SECONDS,
    move: Move | None = None,
    strategy: ModuleStrategy = REAL_MODULE_STRATEGY,
    strategy_name: str = REAL,
    deadlock_alarm_seconds: float = DEFAULT_DEADLOCK_ALARM_SECONDS,
) -> SemanticMiddleware:
    """The middleware instance for one module, with its three workflows, its notification
    handler, the fault command, its handover event stream and its state dashboard.

    The middleware's heartbeat interval is the environment's (`common.heartbeats`): the
    middleware's own default, unless the process was started with
    `JMS_KAPPS_HEARTBEAT_INTERVAL_SECONDS`, as a harness run with its heartbeats off starts it.

    The module's transit time, how long the simulated move of a box takes, is read from the graph
    once, here, with the longest one of any module, T: a box the module reserved itself for may
    take `ARRIVAL_BOUND_SECONDS` and T to arrive, or `ARRIVAL_BOUND_SECONDS` and two T for a box
    reserved ahead, at a look-ahead.

    Args:
        retry_seconds: How long the module waits before it asks again the Reserve of a module
            that refused to reserve itself, once its detour has refused too.
        move: The move of a box off the module, in place of the simulated one.
        strategy: The module strategy, which names the neighbours the module asks for a box.
        strategy_name: Its name, `--module-strategy`.
        deadlock_alarm_seconds: How long a box the module holds may wait for a Reserve before
            the module raises the deadlock alarm for it, `--deadlock-alarm-seconds`.

    Raises:
        HeartbeatSettingRefused: If the heartbeat interval variable holds no number of seconds.
            Nothing is read or registered then.
        TransitTimeError: If the graph holds no transit time for the module, or more than one.
            Nothing is registered then.
    """
    interval = heartbeat_interval(os.environ)
    times = transit_times(ogm.db, IRI(resource_iri))
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=resource_iri,
        service_class=str(FC.FlexConveyorModuleService),
        ogm=ogm,
        host=host,
        port=port,
        activity_feed=True,
        heartbeat_interval=interval,
    )
    # The middleware's state dashboard at `/dashboard`, for a person who opens it from the
    # Launcher page: the library's own boxes, what the instance holds and which of its writes the
    # store refused among them, and the module's own three, in a tab named after the module
    # (`register_boxes`, below).
    dashboard = enable_dashboard(middleware)
    # A module that a box arrives on reads the box with its transport.
    graph = GraphView(ogm.db, fetch=middleware.fetch)
    # Set by the stop signal: it ends a wait before a Reserve is asked again, and starts the
    # module's stop.
    stop_begun = stop_event(middleware.app)
    module = FlexConveyorModule(
        IRI(resource_iri),
        graph=graph,
        handover=functools.partial(possession.handover, middleware),
        call=call_peer,
        records=TransportRecords(middleware),
        notify=notify_with(middleware),
        move=move if move is not None else transit(times.own),
        retry_seconds=retry_seconds,
        wait=stop_begun.wait,
        arrival_bound=ARRIVAL_BOUND_SECONDS + times.longest,
        # A box reserved ahead first moves onto the module that asked, then onto this one.
        ahead_bound=ARRIVAL_BOUND_SECONDS + 2 * times.longest,
        events=open_handover_event_stream(middleware, host, port),
        # From the stop signal on, before uvicorn waits for the open connections.
        stopping=lambda: stop_has_begun(middleware.app),
        strategy=strategy,
        strategy_name=strategy_name,
        deadlock_alarm_seconds=deadlock_alarm_seconds,
    )

    # The module's stop (`FlexConveyorModule.stop`) starts with the stop signal, on a thread of
    # its own, beside uvicorn's wait for the open connections: it waits for the box the module
    # reserved itself for and for the module's own work, the graceful-shutdown bound at most, and
    # ends what is the module's to end. The shutdown, after the middleware has deregistered, waits
    # for it: the module's work runs on threads of its own, which the end of the process would
    # cut short. A server stopped without a signal, as a test's is, begins the stop there.
    def stop_when_it_begins() -> None:
        stop_begun.wait()
        module.stop(stop_seconds_left(middleware.app))

    stopping = threading.Thread(
        target=stop_when_it_begins,
        name=f"stop of {IRI(resource_iri).fragment}",
        daemon=True,
    )
    stopping.start()

    async def stop_the_module() -> None:
        begin_stop(middleware.app)
        await asyncio.to_thread(
            stopping.join, stop_seconds_left(middleware.app, MODULE_STOP_SECONDS)
        )

    middleware.add_callback("on_shutdown", stop_the_module)

    # At the origin of a spawn, the WMS's notification brings a transport that implements the
    # module's BoxTransportCapability: the middleware's intake reads it once, under the scope given
    # below, and runs this workflow with it, as the module's Receive runs it at every later
    # module. Nothing calls it over REST.
    @middleware.workflow(
        capability_class=FC.BoxTransportCapability,
        workflow_class=FC.BoxTransportWorkflow,
    )
    def box_transport(transport: Node) -> None:
        """Carry a transport on while it is assigned to this module."""
        module.box_transport(transport_from(transport))

    # Every other notification: none of the module's workflows takes it.
    def unmatched(operation: Node) -> None:
        logger.warning(
            "%s: notified of %s, which none of its workflows takes",
            IRI(resource_iri).fragment,
            IRI(str(operation.id)).fragment,
        )

    middleware.on_notification(unmatched, class_scope=TRANSPORT_SCOPE)

    @middleware.workflow(
        capability_class=FC.ReserveCapability, workflow_class=FC.ReserveWorkflow
    )
    def reserve(request: ReserveRequest) -> ReserveResult:
        """Reserve this module for a box that another module is about to hand over."""
        return module.reserve(request)

    @middleware.workflow(
        capability_class=FC.ReceiveCapability, workflow_class=FC.ReceiveWorkflow
    )
    def receive(request: ReceiveRequest) -> ReceiveResult:
        """The light barrier: a box handed to this module has arrived, and the module carries
        it on."""
        return module.receive(request)

    mount_fault_switches(middleware.app, module)
    # The seed wrote the station before any module started, and a run keeps it, so the
    # connections read once here are the ones the dashboard draws until the module's first
    # decision reads them again. They route nothing: every decision reads its own. The same read
    # finds the box the module possesses, so that a restarted module shows the box it holds.
    module.memory.read_at_start(graph.at_start(module.resource))
    register_boxes(dashboard, module, times.own)
    return middleware


def module_parser() -> argparse.ArgumentParser:
    """The command line of a module process, which `module_command` builds."""
    parser = process_parser("One FlexConveyorModule")
    parser.add_argument("--resource-iri", required=True, help="The module's IRI")
    parser.add_argument(
        "--retry-time",
        type=float,
        default=DEFAULT_RETRY_SECONDS,
        help="Seconds to wait before asking a module that refused to reserve itself again "
        f"(default: {DEFAULT_RETRY_SECONDS:g})",
    )
    parser.add_argument(
        "--module-strategy",
        default=REAL,
        help=f"The module strategy, by its name (default: {REAL})",
    )
    parser.add_argument(
        "--deadlock-alarm-seconds",
        type=float,
        default=DEFAULT_DEADLOCK_ALARM_SECONDS,
        help="Seconds a box the module holds may wait for a Reserve before the module raises the "
        f"deadlock alarm for it (default: {DEFAULT_DEADLOCK_ALARM_SECONDS:g})",
    )
    return parser


def alarm_time_problem(seconds: float) -> str | None:
    """Why `seconds` is no deadlock alarm time, or None where it is one: a positive number of
    seconds."""
    if math.isfinite(seconds) and seconds > 0:
        return None
    return f"--deadlock-alarm-seconds must be a positive number of seconds, not {seconds:g}"


def main() -> None:
    parser = module_parser()
    args = parser.parse_args()
    if args.retry_time <= 0:
        parser.error("--retry-time must be positive")
    problem = alarm_time_problem(args.deadlock_alarm_seconds)
    if problem is not None:
        parser.error(problem)
    # Before the store is read: a module given a strategy it does not know does not start. Its
    # process ends with the reason, which the Launcher page shows with the state `failed`.
    try:
        strategy = chosen(ModuleStrategy, args.module_strategy, found(ModuleStrategy))
    except UnknownStrategy as unknown:
        print(f"jms-uc2 module: {unknown}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    try:
        run_process(
            args.host,
            lambda port: build(
                args.resource_iri,
                OGM(db=connect(args.dataset)),
                args.host,
                port,
                retry_seconds=args.retry_time,
                strategy=strategy,
                strategy_name=args.module_strategy,
                deadlock_alarm_seconds=args.deadlock_alarm_seconds,
            ),
            lambda address: f"{args.resource_iri} on {address}",
        )
    except (HeartbeatSettingRefused, TransitTimeError) as error:
        # A module whose transit time the graph does not hold exactly once, or whose heartbeat
        # interval variable holds no number, does not start. Its process ends with the reason,
        # which the Launcher page shows with the state `failed`.
        print(f"jms-uc2 module: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
