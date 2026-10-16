"""Use case 2 copes with failure, in one module's handshake logic, without a network or a triple
store (Etienne, 2026-10-11, jms_kapps 77):

- **Busy: the wait stays.** A refusal that passes keeps the module asking, with no time limit.
- **Crash: route around.** A next module that is not reachable is left out for the box, and the
  module chooses again, the module the box came from now a valid choice; a left-out module stays
  out while the box is on the module, and one warning names each module it leaves out.
- **The look-ahead routes around too**, and refuses "no live route on" where no live module is
  left; the asker treats that like a module that is not reachable.
- **Failure only with no live route left**, with a reason that names the destination and each
  module left out.
- **Deadlock alarm.** A box that waits past the alarm time raises one "possible deadlock:" WARNING
  and one `deadlock_alarm` event, and keeps waiting.

A crash is a peer that the fake network cannot reach, or that the fake graph names no live
workflow of. The fakes in `_station` stand in for the graph, the network and the middleware, as
in `test_uc2_look_ahead.py`.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Sequence

import httpx
import pytest
from _station import (
    MiddlewareHandover,
    Moves,
    Network,
    Notifications,
    Reads,
    Records,
    Stop,
    Tasks,
    Timers,
    Waits,
    World,
)
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import ReceiveRequest, ReserveRequest
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.strategies import REAL_MODULE_STRATEGY, ModuleStrategy
from jms_kapps.uc2.vocabulary import FC, WMS, box_iri, module_iri, transport_iri
from jms_kapps.uc2.wms import SpawnBoxRequest, WarehouseManagementSystem

M1, M2, M3, M4, M5, M6 = (module_iri(n) for n in range(1, 7))
B1, B2, B9 = box_iri(1), box_iri(2), box_iri(9)
T1 = transport_iri(B1)

RETRY_SECONDS = 0.1
# The deadlock alarm time of these tests' modules, in seconds of their clock.
ALARM_SECONDS = 5.0
# What a call raises when the peer's process is gone and its port is closed.
REFUSED = httpx.ConnectError("[Errno 111] Connection refused")
NO_LIVE_RESERVE = "the graph names no live ReserveWorkflow of it"

# A diamond: Module 1 hands boxes to Module 2 and to Module 3, and both to Module 4, the exit.
DIAMOND = {(M1, M2), (M1, M3), (M2, M4), (M3, M4)}


class Clock:
    """The time a module reads, which the test moves on."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world():
    world = World()
    world.wms_iri = WMS
    return world


@pytest.fixture
def network():
    return Network()


@pytest.fixture
def tasks():
    return Tasks()


@pytest.fixture
def waits():
    return Waits()


@pytest.fixture
def timers():
    return Timers()


@pytest.fixture
def moves(world):
    return Moves(world)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def records(world):
    return Records(world)


@pytest.fixture
def notifications(world, tasks):
    return Notifications(world, tasks)


@pytest.fixture
def station(world, network, tasks, waits, timers, moves, clock, records, notifications):
    """Builds the modules it is given, on one network, by resource, each with the strategy it is
    given, `real` unless a test names another."""
    stops: dict[IRI, Stop] = defaultdict(Stop)
    built: dict[IRI, FlexConveyorModule] = {}

    def build(
        *resources: IRI, strategy: ModuleStrategy = REAL_MODULE_STRATEGY
    ) -> dict[IRI, FlexConveyorModule]:
        for resource in resources:
            module = FlexConveyorModule(
                resource,
                graph=Reads(world, resource),
                handover=MiddlewareHandover(world, resource),
                call=network.call,
                records=records,
                notify=notifications.notify,
                move=moves.move,
                start=tasks.start,
                retry_seconds=RETRY_SECONDS,
                wait=waits.wait,
                later=timers.later,
                clock=clock,
                events=HandoverEvents(resource, token=resource.fragment),
                stopping=stops[resource].has_begun,
                strategy=strategy,
                deadlock_alarm_seconds=ALARM_SECONDS,
            )
            network.peers[resource] = module
            notifications.receivers[resource] = module.box_transport
            built[resource] = module
        return built

    return build


def reserve(
    module: FlexConveyorModule, box: IRI, giver: IRI, destination: IRI
) -> object:
    """`giver`'s Reserve of `module` for `box`, bound for `destination`."""
    return module.reserve(
        ReserveRequest(box=box, source_module=giver, destination=destination)
    )


def moved(moves: Moves, box: IRI) -> list[IRI]:
    """Where `box` was moved, in order."""
    return [receiver for moved_box, receiver, _ in moves.moved if moved_box == box]


def asked(network: Network, by_calls: int | None = None) -> list[IRI]:
    """Every module whose Reserve a call reached, in order."""
    return [
        peer for step, peer in network.calls[:by_calls] if step == "ReserveWorkflow"
    ]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == module_logger.name and r.levelno == logging.WARNING
    ]


def alarms(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [m for m in warnings(caplog) if m.startswith("possible deadlock:")]


def crash(world: World, network: Network, module: IRI, how: str) -> None:
    """`module`'s process is gone: the graph names no live workflow of it, or its port refuses
    the call."""
    if how == "no live workflow":
        world.unregistered.add(module)
    else:
        network.fails_with[module] = REFUSED


CRASHES = ["no live workflow", "call refused"]


def why_not_reachable(module: IRI, how: str) -> str:
    detail = (
        NO_LIVE_RESERVE
        if how == "no live workflow"
        else "[Errno 111] Connection refused"
    )
    return f"{module.fragment} is not reachable: {detail}"


# -- Busy: the wait stays ------------------------------------------------------------------------ #


def test_a_busy_next_module_and_a_busy_detour_past_the_alarm_time_keep_the_module_asking(
    world, tasks, waits, clock, moves, station
):
    """Module 2 and Module 3 hold boxes for 12 s, past the alarm time of 5 s: Module 1 asks both
    again after every wait, with no time limit, and the transport stays `running` until Module 2
    is free and the box goes on."""
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B2, M3)
    world.place(B1, M1, destination=M4)
    while_waiting: list[str] = []

    def two_seconds_pass() -> None:
        clock.now += 2.0
        while_waiting.append(world.jobs[T1].status)

    def module_2_frees_itself() -> None:
        two_seconds_pass()
        world.held[M2].remove(B9)

    waits.meanwhile = [two_seconds_pass] * 5 + [module_2_frees_itself]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert waits.waited == [RETRY_SECONDS] * 6
    assert while_waiting == [OperationStatus.RUNNING] * 6
    assert moved(moves, B1) == [M2, M4, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


# -- Crash: route around ------------------------------------------------------------------------- #


@pytest.mark.parametrize("how", CRASHES)
def test_a_next_module_that_is_not_reachable_is_left_out_and_the_box_takes_a_free_detour(
    caplog, world, tasks, waits, moves, network, notifications, station, how
):
    world.connections = set(DIAMOND)
    m = station(M1, M3, M4)
    crash(world, network, M2, how)
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert moved(moves, B1) == [M3, M4, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE
    assert waits.waited == []
    assert notifications.sent == [(T1, WMS)]
    assert warnings(caplog) == [
        f"Module1: leaves Module2 out for Box1: {why_not_reachable(M2, how)}"
    ]


def test_with_a_busy_detour_the_module_waits_and_the_box_goes_there_once_it_is_free(
    world, tasks, waits, moves, network, station
):
    """Module 2 cannot be reached, and Module 3 holds a box: Module 1 waits, asks Module 3 again,
    and asks Module 2 no more, though its process answers again meanwhile: a left-out module
    stays out while the box is on the module."""
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    network.fails_with[M2] = REFUSED
    world.place(B2, M3)
    world.place(B1, M1, destination=M4)
    waits.meanwhile = [
        lambda: network.fails_with.pop(M2),
        lambda: world.held[M3].remove(B2),
    ]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert waits.waited == [RETRY_SECONDS] * 2
    # Module 3's own look-ahead asks Module 4 once it is free.
    assert asked(network)[:4] == [M2, M3, M3, M3]
    assert moved(moves, B1) == [M3, M4, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


# Two-way between Module 1 and Module 2; Module 2 goes on to Module 5 over Module 3, and Module 1
# over Module 4.
TWO_WAY = {(M1, M2), (M2, M1), (M2, M3), (M3, M5), (M1, M4), (M4, M5)}


def test_on_two_way_connections_the_box_goes_back_to_the_module_it_came_from_and_on_another_path(
    caplog, world, tasks, moves, network, station
):
    """Box 1 came to Module 2 from Module 1, and Module 3, its next module, has crashed: the only
    live neighbour is Module 1, the module the box came from, which is now a valid choice. The
    box goes back there and arrives over Module 4."""
    world.connections = set(TWO_WAY)
    m = station(M1, M2, M4, M5)
    crash(world, network, M3, "call refused")
    world.place(B1, M2, origin=M1, destination=M5)

    m[M2].receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    assert moved(moves, B1) == [M1, M4, M5, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE
    assert warnings(caplog) == [
        (
            "Module2: leaves Module3 out for Box1: Module3 is not reachable: "
            "[Errno 111] Connection refused"
        )
    ]


class Recording(ModuleStrategy):
    """`real`, which keeps what each ask gave it: the module the box came from, and the modules
    a connection of the asking module leads to."""

    label = "Recording"

    def __init__(self) -> None:
        self.asked: list[tuple[IRI | None, set[IRI]]] = []

    def neighbours_to_ask(
        self,
        module: IRI,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> Sequence[IRI]:
        self.asked.append(
            (came_from, {c.target for c in connections if c.source == module})
        )
        return REAL_MODULE_STRATEGY.neighbours_to_ask(
            module, destination, came_from, ahead, connections
        )


def test_the_strategy_contract_keeps_its_signature_and_is_asked_again_without_the_left_out_module(
    world, tasks, network, station
):
    """The module asks its strategy again as it always asks it: with the connections to each
    left-out module removed, and with no module the box came from."""
    assert list(inspect.signature(ModuleStrategy.neighbours_to_ask).parameters) == [
        "self",
        "module",
        "destination",
        "came_from",
        "ahead",
        "connections",
    ]
    world.connections = set(TWO_WAY)
    recording = Recording()
    m = station(M2, strategy=recording)
    station(M1, M4, M5)
    crash(world, network, M3, "call refused")
    world.place(B1, M2, origin=M1, destination=M5)

    m[M2].receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    assert recording.asked == [(M1, {M1, M3}), (None, {M1})]


# -- Failure only with no live route left ------------------------------------------------------- #


def test_with_the_next_module_and_the_one_the_box_came_from_not_reachable_the_transport_fails(
    caplog, world, tasks, moves, network, notifications, station
):
    world.connections = set(TWO_WAY)
    m = station(M2, M4, M5)
    crash(world, network, M3, "call refused")
    crash(world, network, M1, "no live workflow")
    world.place(B1, M2, origin=M1, destination=M5)

    m[M2].receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    reason = "no live route to Module5: Module3 and Module1 are not reachable"
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == reason
    assert world.possessor_of(B1) == M2
    assert moved(moves, B1) == []
    assert notifications.sent == [(T1, WMS)]
    assert warnings(caplog) == [
        (
            "Module2: leaves Module3 out for Box1: Module3 is not reachable: "
            "[Errno 111] Connection refused"
        ),
        f"Module2: leaves Module1 out for Box1: Module1 is not reachable: {NO_LIVE_RESERVE}",
        f"Module2: Box1 stays here: {reason}",
    ]


# -- The look-ahead routes around too ---------------------------------------------------------- #


def test_at_its_look_ahead_a_module_leaves_out_a_next_module_that_is_not_reachable(
    caplog, world, network, timers, station
):
    """Module 2 is asked for Box 1, bound for Module 5: its next module, Module 3, cannot be
    reached, so it looks ahead to Module 4, the next one its strategy names, and accepts once
    Module 4 accepts. The module that holds the box, Module 1, stays excluded."""
    world.connections = {(M1, M2), (M2, M1), (M2, M3), (M3, M5), (M2, M4), (M4, M6)}
    world.connections |= {(M6, M5), (M1, M5)}
    m = station(M2, M4)
    crash(world, network, M3, "call refused")

    answer = reserve(m[M2], B1, M1, M5)

    assert answer.accepted
    assert asked(network) == [M3, M4]
    assert warnings(caplog) == [
        (
            "Module2: leaves Module3 out for Box1: Module3 is not reachable: "
            "[Errno 111] Connection refused"
        )
    ]


def test_with_no_live_module_left_the_look_ahead_refuses_no_live_route_on_and_keeps_none(
    world, network, timers, station
):
    """Module 3 is the only next module, and its call is refused. (With no live workflow of it in
    the graph, `test_uc2_look_ahead` checks the same answer.)"""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2)
    crash(world, network, M3, "call refused")

    refused = reserve(m[M2], B1, M1, M3)

    assert (refused.accepted, refused.refusal) == (False, "no live route on")
    assert refused.reason == "no live route to Module3: Module3 is not reachable"
    assert timers.seconds == []


def test_the_asker_routes_around_a_module_with_no_live_route_on(
    caplog, world, tasks, moves, network, station
):
    """Module 2's own next module, Module 6, has crashed, so Module 2 has no live route on toward
    Module 5: Module 1 treats Module 2 as a module that is not reachable, leaves it out, and the
    box goes over Module 3."""
    world.connections = {(M1, M2), (M2, M6), (M6, M5), (M1, M3), (M3, M4), (M4, M5)}
    m = station(M1, M2, M3, M4, M5)
    crash(world, network, M6, "call refused")
    world.place(B1, M1, destination=M5)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert moved(moves, B1) == [M3, M4, M5, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE
    assert warnings(caplog) == [
        (
            "Module2: leaves Module6 out for Box1: Module6 is not reachable: "
            "[Errno 111] Connection refused"
        ),
        (
            "Module1: leaves Module2 out for Box1: Module2 refused to reserve: no live route to "
            "Module5: Module6 is not reachable"
        ),
    ]


def test_a_spawn_on_an_origin_with_no_live_route_on_is_refused_with_that_reason_and_writes_nothing(
    world, network, records, notifications, station
):
    world.connections = {(M1, M2), (M2, M3)}
    world.classes[FC.FlexConveyorModule] = [M1, M2, M3]
    station(M1, M3)
    crash(world, network, M2, "no live workflow")
    wms = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=records,
        call=network.call,
        notify=notifications.notify,
        events=HandoverEvents(WMS, token="wms"),
    )

    result = wms.spawn(SpawnBoxRequest(origin=str(M1), destination=str(M3)))

    assert not result.accepted
    assert result.reason == (
        "Module1 refused to reserve: no live route to Module3: Module2 is not reachable"
    )
    assert records.writes == []
    assert world.jobs == {}


# -- The deadlock alarm --------------------------------------------------------------------------- #


def test_a_box_that_waits_past_the_alarm_time_raises_one_alarm_and_keeps_waiting(
    caplog, world, tasks, waits, clock, moves, station
):
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B2, M3)
    world.place(B1, M1, destination=M4)

    def three_seconds_pass() -> None:
        clock.now += 3.0

    def module_2_frees_itself() -> None:
        three_seconds_pass()
        world.held[M2].remove(B9)

    waits.meanwhile = [three_seconds_pass] * 4 + [module_2_frees_itself]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert alarms(caplog) == [
        "possible deadlock: Module1 has waited 6 s to hand Box1 on, asking Module2 and Module3"
    ]
    [event] = m[M1].events.after(None)
    assert event.kind == "deadlock_alarm"
    assert (event.resource, event.box, event.transport) == (str(M1), str(B1), str(T1))
    assert event.asked == [str(M2), str(M3)]
    assert (
        event.reason
        == "Module1 has waited 6 s to hand Box1 on, asking Module2 and Module3"
    )
    # The box kept waiting, and nothing failed.
    assert moved(moves, B1) == [M2, M4, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_a_box_handed_on_in_time_raises_no_alarm(
    caplog, world, tasks, waits, clock, moves, station
):
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B2, M3)
    world.place(B1, M1, destination=M4)

    def module_2_frees_itself() -> None:
        clock.now += 4.0
        world.held[M2].remove(B9)

    waits.meanwhile = [module_2_frees_itself]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert alarms(caplog) == []
    assert m[M1].events.after(None) == []
    assert moved(moves, B1) == [M2, M4, WMS]
