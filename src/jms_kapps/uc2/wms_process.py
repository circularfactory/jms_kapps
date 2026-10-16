"""The WarehouseManagementSystem as its own process, behind its own middleware instance.

The process registers `:WarehouseManagementSystemService` and its two workflows in the graph,
serves them over REST, and takes every notification through the middleware's notification handler.
A spawn creates the box directly on its origin and notifies the origin, so no module calls a
workflow of the WarehouseManagementSystem, and it moves no box. (Changed on 2026-10-06, jms_kapps
41, decided by Etienne. Until then it registered three workflows: "The third workflow is its
Convey, which the origin of a spawn calls once it has reserved itself for the box".) A spawn
is how a person, or a program, starts a box on its journey: a POST to `spawn_box`. The spawn rate
is how many boxes a minute the WarehouseManagementSystem spawns on its own, each when it is due,
whether the one before it has ended or not: a POST to `set_spawn_rate`, where 0 stops (changed on
2026-10-07, jms_kapps 51, decided by Etienne; until then "one after another"). A person makes both
posts from the forms of the WarehouseManagementSystem's own dashboard, at `/dashboard`, in the tab
"WMS", which also shows its last spawns, the boxes it holds and the transports it opened, from its
memory. A program posts the same JSON to the same two routes. A notification is how a module
tells it that a transport has ended: the box is back, or the transport failed. It serves its
handover event stream, as a module does. Uvicorn runs on the main thread, so that a signal
reaches it and the middleware deregisters on the way out. From the stop signal on, the
WarehouseManagementSystem refuses every spawn, and its shutdown waits for every spawn of the timer
that is under way, so that no spawn is cut short between the create of its box and the
notification of its origin. (Changed on 2026-10-06, jms_kapps 41, decided by Etienne. Until then
"so that no transport is cut short". Changed again on 2026-10-07, jms_kapps 51, decided by
Etienne: the timer's spawns overlap. Until then "waits for a spawn of the timer that is under
way".)

Run it as `python -m jms_kapps.uc2.wms_process`, the command line `wms_command` builds. The
Launcher does.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections.abc import Callable, Iterable

from kapps_ogm import OGM, Node
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import GRACEFUL_SHUTDOWN_SECONDS, stop_has_begun
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.discovery import GraphView
from jms_kapps.uc2.process import (
    call_peer,
    notify_with,
    open_handover_event_stream,
    process_parser,
    run_process,
)
from jms_kapps.uc2.spawn_rate import SetSpawnRateRequest, SpawnRate, SpawnTimer
from jms_kapps.uc2.strategies import (
    REAL,
    REAL_WMS_STRATEGY,
    UnknownStrategy,
    WmsStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.transport import (
    ENDED_TRANSPORT_SCOPE,
    TransportRecords,
    transport_from,
)
from jms_kapps.uc2.vocabulary import FC, WMS
from jms_kapps.uc2.wms import (
    Choose,
    SpawnBoxRequest,
    SpawnBoxResult,
    WarehouseManagementSystem,
    seeded_choice,
)
from jms_kapps.uc2.wms_dashboard import TAB, register_boxes
from jms_kapps.uc2.wms_memory import WmsMemory

# The classes of the two workflows the WarehouseManagementSystem's process registers (`build`): it
# takes an order, a spawn or a spawn rate, and the run core's readiness gate lets it through, only
# once its service has both.
WMS_WORKFLOWS = (FC.SpawnBoxWorkflow, FC.SetSpawnRateWorkflow)


def wms_command(dataset: str, *, pick_seed: int | None = None) -> list[str]:
    """The command line that runs the WarehouseManagementSystem against `dataset`. It takes
    no transit time: it moves no box. (Changed on 2026-10-06, jms_kapps 41, decided by Etienne:
    until then it took the transit time of "the simulated move of a box onto its origin".) With
    `pick_seed` it ends with `--pick-seed <pick_seed>`: the seed of the generator it picks its
    modules with, unseeded where None. (Added on 2026-10-12, jms_kapps 93, interim name.)"""
    return [
        sys.executable,
        "-m",
        "jms_kapps.uc2.wms_process",
        "--dataset",
        dataset,
        *([] if pick_seed is None else ["--pick-seed", str(pick_seed)]),
    ]


def build(
    ogm: OGM,
    host: str,
    port: int,
    *,
    choose: Choose = random.choice,
    strategy: WmsStrategy = REAL_WMS_STRATEGY,
    strategy_name: str = REAL,
) -> SemanticMiddleware:
    """The WarehouseManagementSystem's middleware instance, with its two workflows, its
    notification handler, its handover event stream and its dashboard. `choose` picks a module
    out of several, at random unless a test says otherwise. (Added on 2026-10-06, jms_kapps 45, the
    build's reading: `choose`, so that a live test can cover every ordered pair of modules in a few
    boxes.) `strategy`, named `strategy_name`, picks the origin and the destination of a spawn
    among its candidates, with `choose` (added on 2026-10-10, jms_kapps 52, decided by Etienne on
    2026-10-07)."""
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(WMS),
        service_class=str(FC.WarehouseManagementSystemService),
        ogm=ogm,
        host=host,
        port=port,
        activity_feed=True,
    )
    graph = GraphView(ogm.db, fetch=middleware.fetch)
    wms = WarehouseManagementSystem(
        WMS,
        graph=graph,
        records=TransportRecords(middleware),
        # The origin's Reserve answers from the graph at once: the step timeout bounds it.
        call=call_peer,
        notify=notify_with(middleware),
        events=open_handover_event_stream(middleware, host, port),
        # From the stop signal on, before uvicorn waits for the open connections.
        stopping=lambda: stop_has_begun(middleware.app),
        choose=choose,
        strategy=strategy,
        strategy_name=strategy_name,
    )

    # A notification of a transport that ended implements a module's capability, none of the
    # WMS's, so the middleware's intake hands it to the handler, read once under this scope.
    def transport_ended(transport: Node) -> None:
        wms.transport_ended(transport_from(transport))

    middleware.on_notification(transport_ended, class_scope=ENDED_TRANSPORT_SCOPE)
    # The seed wrote the station before any party started, and a run keeps it, so the dashboard's
    # form offers the modules read once here: those a spawn may name, the entry and the exit
    # modules where the topology names them, and every module where it names none.
    origins, destinations = wms.origins_and_destinations()
    serve_spawning(
        middleware,
        wms.spawn,
        wms.memory,
        origins=origins,
        destinations=destinations,
    )
    return middleware


def serve_spawning(
    middleware: SemanticMiddleware,
    spawn: Callable[[SpawnBoxRequest], SpawnBoxResult],
    memory: WmsMemory,
    *,
    origins: Iterable[IRI] = (),
    destinations: Iterable[IRI] = (),
) -> SpawnTimer:
    """Serve on `middleware` what spawns boxes: the workflows `spawn_box` and `set_spawn_rate`,
    the timer behind the second, and the dashboard with the WarehouseManagementSystem's boxes.

    The dashboard is the middleware's own, with its boxes of the instance in the tab "System",
    "Refused writes" among them, and opens on the tab of the WarehouseManagementSystem's boxes.
    Its routes are the dashboard's; the two workflows are the only routes added here.

    Args:
        middleware: The WarehouseManagementSystem's middleware, not started yet.
        spawn: The WarehouseManagementSystem's spawn.
        memory: What the WarehouseManagementSystem knows of its own work.
        origins: The modules a spawn may name as its origin, which the dashboard's form offers:
            the entry modules of the FlexConveyor station, or every module where it names none.
        destinations: Those a spawn may name as its destination, which the form offers: its
            exit modules, or every module where it names none. (Changed on 2026-10-06,
            jms_kapps 45, decided by Etienne: roles are optional. Until then the two were
            `entry_modules`, "The entry modules of the FlexConveyor station, which the
            dashboard's form offers as the origin", and `exit_modules`, "Its exit modules, which
            the form offers as the destination".)

    Returns:
        The timer, stopped. It stops again when the middleware shuts down, once every spawn
        under way has ended, or a bound has passed. (Changed on 2026-10-07, jms_kapps 51, decided
        by Etienne. Until then "once the spawn under way, if there is one, has ended".)
    """
    timer = SpawnTimer(lambda: spawn(SpawnBoxRequest()))

    async def stop_the_timer() -> None:
        # The spawns under way started before the stop signal, so each ends within a create and
        # a notification after it, usually while uvicorn still waits for the open connections.
        # The bound keeps a spawn that hangs from holding the stop until the Launcher kills the
        # process.
        await asyncio.to_thread(timer.stop, GRACEFUL_SHUTDOWN_SECONDS)

    middleware.add_callback("on_shutdown", stop_the_timer)

    @middleware.workflow(
        capability_class=FC.SpawnBoxCapability, workflow_class=FC.SpawnBoxWorkflow
    )
    def spawn_box(request: SpawnBoxRequest) -> SpawnBoxResult:
        """Make a box, and send it from its origin module to its destination module and back.

        Leave out the origin to have a free entry module picked, and the destination to have an
        exit module picked; on a topology that names no such role, any free module, and any
        module but the origin. A picked destination is one the origin reaches, and a picked
        origin one that reaches a destination. (Changed on 2026-10-06, jms_kapps 45, decided by
        Etienne: roles are optional. Until then the sentence ended at "picked". The sentence on
        what a pick reaches added on 2026-10-07, jms_kapps 51, decided by Etienne.)
        """
        return spawn(request)

    @middleware.workflow(
        capability_class=FC.SetSpawnRateCapability,
        workflow_class=FC.SetSpawnRateWorkflow,
    )
    def set_spawn_rate(request: SetSpawnRateRequest) -> SpawnRate:
        """Spawn boxes at a steady rate, each with its origin and its destination left to the
        WarehouseManagementSystem; 0 stops. Answers the rate in force."""
        return SpawnRate(boxes_per_minute=timer.set(request.boxes_per_minute))

    register_boxes(
        # It opens on the WMS's own tab, where a person spawns boxes.
        enable_dashboard(middleware, first_tab=TAB),
        memory,
        timer,
        origins=origins,
        destinations=destinations,
    )
    return timer


def wms_parser() -> argparse.ArgumentParser:
    """The command line of the WarehouseManagementSystem's process, which `wms_command` builds."""
    parser = process_parser("The WarehouseManagementSystem of use case 2")
    parser.add_argument(
        "--wms-strategy",
        default=REAL,
        help=f"The WMS strategy, by its name (default: {REAL})",
    )
    parser.add_argument(
        "--pick-seed",
        type=int,
        default=None,
        help="Seed the generator that picks the modules of a spawn with this whole number, so "
        "that the same seed gives the same sequence of picks (default: unseeded)",
    )
    return parser


def main() -> None:
    args = wms_parser().parse_args()
    # Before the store is read: a WMS given a strategy it does not know does not start. Its
    # process ends with the reason, which the Launcher page shows with the state `failed`.
    # (Added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07.)
    try:
        strategy = chosen(WmsStrategy, args.wms_strategy, found(WmsStrategy))
    except UnknownStrategy as unknown:
        print(f"jms-uc2 WMS: {unknown}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    run_process(
        args.host,
        lambda port: build(
            OGM(db=connect(args.dataset)),
            args.host,
            port,
            choose=seeded_choice(args.pick_seed),
            strategy=strategy,
            strategy_name=args.wms_strategy,
        ),
        lambda address: f"{WMS} on {address}",
    )


if __name__ == "__main__":
    main()
