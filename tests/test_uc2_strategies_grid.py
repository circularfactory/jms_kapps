"""The two test strategies, offline on the shipped grid, and answers outside the
contract.

The module strategy `south` asks only the southern neighbour, from the connections' directions;
the WMS strategy `exit-below` sends each box to the exit straight below its origin, from the
connections. Both are registered in the tests only, through a fake set of entry points
(`_strategies`). A box goes as they say, and everything around the choice stays the module's and
the WMS's: the look-ahead, the bound of a reservation, the end of a transport that cannot go on.
The fakes in `_station` stand in for the graph, the network and the middleware, as in
`test_uc2_look_ahead.py`.
"""

from __future__ import annotations

import logging

import pytest
from _station import (
    MiddlewareHandover,
    Moves,
    Network,
    Notifications,
    Reads,
    Records,
    Starts,
    Stop,
    Tasks,
    Timers,
    Waits,
    World,
)
from _strategies import REAL_ENTRIES, TEST_ENTRIES, Answers, Pair, entry_points
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.strategies import (
    REAL_MODULE_STRATEGY,
    ModuleStrategy,
    WmsStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import (
    FC,
    FCI_NS,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
)
from jms_kapps.uc2.wms import SpawnBoxRequest, WarehouseManagementSystem
from jms_kapps.uc2.wms import logger as wms_logger

M = {n: module_iri(n) for n in range(1, 31)}
B1, B2 = box_iri(1), box_iri(2)
T1 = transport_iri(B1)

RETRY_SECONDS = 0.1
ARRIVAL_BOUND_SECONDS = 30.0
AHEAD_BOUND_SECONDS = 31.0

# The test strategies, as the tests register them.
REGISTERED = entry_points(REAL_ENTRIES, TEST_ENTRIES)
SOUTH = chosen(ModuleStrategy, "south", found(ModuleStrategy, REGISTERED))
EXIT_BELOW = chosen(WmsStrategy, "exit-below", found(WmsStrategy, REGISTERED))


@pytest.fixture
def world():
    """The shipped grid: six entry modules on the top row, five rows, six exit modules at the
    bottom, each connection with its direction."""
    world = World()
    world.wms_iri = WMS
    for module in load_topology("grid").modules:
        for connection in module.connections:
            to = IRI(connection.to, base=FCI_NS)
            world.connections.add((module.resource, to))
            world.directions[(module.resource, to)] = connection.direction
        for role, role_class in (
            ("EntryModule", FC.EntryModule),
            ("ExitModule", FC.ExitModule),
        ):
            if role in module.types:
                world.classes[role_class].append(module.resource)
    return world


@pytest.fixture
def tasks():
    return Tasks()


@pytest.fixture
def network():
    return Network()


@pytest.fixture
def notifications(world, tasks):
    return Notifications(world, tasks)


@pytest.fixture
def moves(world):
    return Moves(world)


@pytest.fixture
def waits():
    return Waits()


@pytest.fixture
def timers():
    return Timers()


@pytest.fixture
def station(world, network, tasks, notifications, moves, waits, timers):
    """Builds every module of the grid with the module strategy it is given, named `name`."""
    records = Records(world)

    def build(
        strategy: ModuleStrategy = REAL_MODULE_STRATEGY, name: str = "real"
    ) -> dict[IRI, FlexConveyorModule]:
        built = {}
        for resource in M.values():
            module = FlexConveyorModule(
                resource,
                graph=Reads(world, resource),
                handover=MiddlewareHandover(world, resource),
                call=network.call,
                records=records,
                notify=notifications.notify,
                move=moves.move,
                start=Starts(tasks),
                retry_seconds=RETRY_SECONDS,
                wait=waits.wait,
                later=timers.later,
                arrival_bound=ARRIVAL_BOUND_SECONDS,
                ahead_bound=AHEAD_BOUND_SECONDS,
                events=HandoverEvents(resource, token=resource.fragment),
                stopping=Stop().has_begun,
                strategy=strategy,
                strategy_name=name,
            )
            network.peers[resource] = module
            notifications.receivers[resource] = module.box_transport
            built[resource] = module
        return built

    return build


def wms_with(world, network, notifications, strategy, name, choose):
    return WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=Records(world),
        call=network.call,
        notify=notifications.notify,
        choose=choose,
        events=HandoverEvents(WMS, token="wms"),
        strategy=strategy,
        strategy_name=name,
    )


def moved(moves: Moves, box: IRI) -> list[IRI]:
    return [receiver for moved_box, receiver, _ in moves.moved if moved_box == box]


def carry_on(world: World, modules, box: IRI, on: IRI, destination: IRI) -> IRI:
    """Put `box` on `on`, bound for `destination`, and let `on` carry it on."""
    operation = world.place(box, on, destination=destination)
    transport = world.transport(operation)
    assert transport is not None
    modules[on].box_transport(transport)
    return operation


def first(options):
    return options[0]


# -- The two test strategies ---------------------------------------------------------------- #


def test_the_wms_strategy_sends_the_box_to_the_exit_straight_below_its_origin(
    world, network, notifications, tasks, station, moves
):
    """Module 1 holds a box, so the first free origin is Module 2. The real WMS strategy would
    send the box to Module 25, the first exit Module 2 reaches; `exit-below` sends it to Module 26,
    straight below. The modules ask only southward, and the box goes 2, 8, 14, 20, 26 and back."""
    modules = station(SOUTH, "south")
    world.held[M[1]].append(box_iri(99))
    wms = wms_with(world, network, notifications, EXIT_BELOW, "exit-below", first)

    answer = wms.spawn(SpawnBoxRequest())
    tasks.run()

    assert answer.accepted, answer.reason
    assert (answer.origin, answer.destination) == (str(M[2]), str(M[26]))
    box = IRI(str(answer.box))
    assert moved(moves, box) == [M[8], M[14], M[20], M[26], WMS]
    assert world.jobs[transport_iri(box)].status == OperationStatus.DONE
    assert modules[M[26]].memory.decision_count() == 1


def test_a_box_goes_as_the_module_strategy_says_and_not_on_a_shortest_path(
    world, station, moves
):
    """From Module 8 a shortest path to Module 25 runs west, over Module 7. `south` asks Module 14
    instead, then Module 20; at Module 20 the box cannot go on, since Module 26, straight below,
    leads to no southern neighbour, and its look-ahead refuses: the transport ends there, with
    why."""
    modules = station(SOUTH, "south")

    operation = carry_on(world, modules, B1, M[8], M[25])

    assert moved(moves, B1) == [M[14], M[20]]
    job = world.jobs[operation]
    assert job.status == OperationStatus.FAILED
    assert "Module26 has no route to Module25" in str(job.result)


def test_the_routing_decision_shows_the_strategy_s_neighbours_and_the_ties_as_before(
    world, station
):
    """The dashboard's "Routing decisions" keep their columns: next and detour are the strategy's
    two neighbours, and ties are, as before, the other modules first on a shortest path. From
    Module 8 toward Module 25 that is Module 7 alone."""
    modules = station(SOUTH, "south")

    carry_on(world, modules, B1, M[8], M[25])

    [decision] = modules[M[8]].memory.decisions()
    assert (decision.next_module, decision.detour, decision.ties) == (
        M[14],
        None,
        (M[7],),
    )


def test_a_southern_neighbour_that_holds_a_box_is_not_entered(
    world, station, moves, waits, timers, network
):
    """The look-ahead still applies: Module 7 accepts Box 1 only once Module 13, its southern
    neighbour, accepts a plain Reserve, and Module 13 holds Box 2. So Box 1 stays on Module 1,
    whose strategy names no detour, and Module 1 waits and asks again. Once Module 13 is free, Box
    1 goes on, and the bound of each reservation still applies: 30 s for Module 7's, and 31 s for
    Module 13's, reserved ahead."""
    modules = station(SOUTH, "south")
    world.place(B2, M[13], destination=M[25])

    def module_13_is_freed():
        assert moved(moves, B1) == []
        world.held[M[13]].remove(B2)

    waits.meanwhile.append(module_13_is_freed)

    operation = carry_on(world, modules, B1, M[1], M[25])

    assert waits.waited == [RETRY_SECONDS]
    assert ("ReserveWorkflow", M[13]) in network.calls
    assert moved(moves, B1) == [M[7], M[13], M[19], M[25], WMS]
    assert world.jobs[operation].status == OperationStatus.DONE
    assert ARRIVAL_BOUND_SECONDS in timers.seconds
    assert AHEAD_BOUND_SECONDS in timers.seconds


# -- Answers outside the contract ------------------------------------------------------------- #


def warnings_of(caplog, logger) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == logger.name and r.levelno == logging.WARNING
    ]


def test_of_more_than_two_neighbours_the_module_keeps_the_first_two(world, station):
    modules = station(Answers({M[8]: [M[14], M[7], M[9]]}), "three")

    carry_on(world, modules, B1, M[8], M[25])

    [decision] = modules[M[8]].memory.decisions()
    assert (decision.next_module, decision.detour) == (M[14], M[7])


def test_a_neighbour_no_connection_leads_to_is_dropped_with_one_warning_naming_the_strategy(
    world, station, moves, caplog
):
    """Module 8 leads to Module 14 and Module 7, not to Module 9: it asks Module 7 alone, and the
    box goes on from there."""
    modules = station(Answers({M[8]: [M[9], M[7]]}), "elsewhere")

    with caplog.at_level(logging.WARNING, logger=module_logger.name):
        carry_on(world, modules, B1, M[8], M[25])

    [warning] = warnings_of(caplog, module_logger)
    assert "elsewhere" in warning and "Module9" in warning
    assert moved(moves, B1) == [M[7], M[13], M[19], M[25], WMS]
    [decision] = modules[M[8]].memory.decisions()
    assert (decision.next_module, decision.detour) == (M[7], None)


def test_where_no_neighbour_is_left_the_box_cannot_go_on_as_when_no_path_leads_on(
    world, station, moves
):
    modules = station(Answers({M[8]: [M[9]]}), "elsewhere")

    operation = carry_on(world, modules, B1, M[8], M[25])

    assert moved(moves, B1) == []
    job = world.jobs[operation]
    assert job.status == OperationStatus.FAILED
    assert "Module8 has no route to Module25" in str(job.result)


def test_a_wms_pair_that_is_not_a_candidate_refuses_the_spawn_and_is_logged(
    world, network, notifications, station, caplog
):
    """Module 7 is no exit module of the grid."""
    station()
    wms = wms_with(world, network, notifications, Pair(M[1], M[7]), "aside", first)

    with caplog.at_level(logging.INFO, logger=wms_logger.name):
        answer = wms.spawn(SpawnBoxRequest())

    reason = "the strategy aside chose Module1 to Module7, which is not a candidate"
    assert (answer.accepted, answer.reason) == (False, reason)
    assert any(
        f"spawn refused: {reason}" in r.getMessage()
        for r in caplog.records
        if r.name == wms_logger.name
    )
    assert world.jobs == {}
