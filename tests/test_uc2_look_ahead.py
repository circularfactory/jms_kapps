"""Routing that keeps boxes flowing, in one module's handshake logic, without a network or a triple
store: look-ahead acceptance, one detour before waiting, longest-waiting first, and the fault that
still collides.

- **Look-ahead acceptance.** A module accepts a Reserve only if it is free and either it is the
  box's destination, or its own next module accepts a plain Reserve for the box at once. That inner
  ask looks no further ahead. Otherwise the module refuses at once and keeps no reservation. The
  box then goes to the module reserved ahead, whose reservation is bound by 30 s and two transit
  times.
- **One detour before waiting.** When the next module declines, the giving module tries one other
  neighbour from which the destination can still be reached, never the module the box came from;
  only if that one declines too does it wait and ask again.
- **Longest-waiting first.** A module that becomes free serves the box that has waited longest. A
  box refused through a look-ahead keeps its place in the order of the module that refused it, the
  inner ask included, and the WarehouseManagementSystem's Reserve for a spawn waits behind every box
  in that order. A box leaves the order when it moves elsewhere or its transport ends.
- **The fault still collides.** `skip-reserve` makes a module accept without looking ahead and hand
  its box on without asking, so on a busy station it meets an occupied module.

The fakes in `_station` stand in for the graph, the network and the middleware, as in
`test_uc2_module.py`.
"""

from __future__ import annotations

import logging
from collections import defaultdict

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
    store_refusal,
)
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import ReceiveRequest, ReserveRequest
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.vocabulary import WMS, box_iri, module_iri, transport_iri

M1, M2, M3, M4, M5, M6 = (module_iri(n) for n in range(1, 7))
B1, B2, B3, B9 = box_iri(1), box_iri(2), box_iri(3), box_iri(9)
T1, T2 = transport_iri(B1), transport_iri(B2)

RETRY_SECONDS = 0.1
# The store's refusal of a second box on a module, as far as these tests read it.
POSSESSION_REPORT = "A FlexConveyorModule may possess at most one Box at a time."
# The bounds of a run whose moves take 3 s: 30 s and the transit time for a box reserved by its
# giving module, and 30 s and two transit times for a box reserved ahead.
ARRIVAL_BOUND_SECONDS = 33.0
AHEAD_BOUND_SECONDS = 36.0


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
def stops():
    """Whether the stop of each module's process has begun, by module."""
    return defaultdict(Stop)


@pytest.fixture
def station(world, network, tasks, waits, timers, moves, clock, stops):
    """Builds the modules it is given, on one network, by resource."""
    records = Records(world)
    notifications = Notifications(world, tasks)
    built: dict[IRI, FlexConveyorModule] = {}

    def build(*resources: IRI) -> dict[IRI, FlexConveyorModule]:
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
                arrival_bound=ARRIVAL_BOUND_SECONDS,
                ahead_bound=AHEAD_BOUND_SECONDS,
                clock=clock,
                events=HandoverEvents(resource, token=resource.fragment),
                stopping=stops[resource].has_begun,
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


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == module_logger.name]


# -- Look-ahead acceptance ---------------------------------------------------------------------- #


def test_the_destination_accepts_a_free_module_without_looking_ahead(
    world, network, station
):
    world.connections = {(M1, M2)}
    m = station(M2)

    assert reserve(m[M2], B1, M1, M2).accepted
    assert network.calls == []


def test_a_module_accepts_once_its_own_next_module_accepts_a_plain_reserve_at_once(
    world, network, station
):
    """Module 2 is not the destination: it asks Module 3, its next module toward Module 3, before
    it answers Module 1. Module 3 has then reserved itself for the box too."""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2, M3)

    assert reserve(m[M2], B1, M1, M3).accepted
    assert network.calls == [("ReserveWorkflow", M3)]
    refused = reserve(m[M3], B2, M2, M3)
    assert (refused.accepted, refused.refusal) == (False, "reserved")


def test_the_inner_ask_looks_no_further_ahead(world, network, station):
    world.connections = {(M1, M2), (M2, M3), (M3, M4)}
    m = station(M2, M3, M4)

    assert reserve(m[M2], B1, M1, M4).accepted
    assert network.calls == [("ReserveWorkflow", M3)]


def test_a_module_whose_next_module_refuses_refuses_at_once_and_keeps_no_reservation(
    world, timers, station
):
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2, M3)
    world.place(B9, M3)

    refused = reserve(m[M2], B1, M1, M3)

    assert (refused.accepted, refused.refusal) == (False, "blocked")
    assert refused.reason == (
        "Module3, its next module, refused to reserve: occupied by Box9"
    )
    assert timers.seconds == []


def test_a_module_whose_next_module_cannot_be_reached_refuses_no_live_route_on(
    world, network, station
):
    """No refusal that passes: the giving module treats the module as one that is not
    reachable, and leaves it out. The look-ahead leaves out a next module that cannot be reached, and refuses
    "no live route on" where none is left."""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2)
    world.unregistered.add(M3)

    refused = reserve(m[M2], B1, M1, M3)

    assert (refused.accepted, refused.refusal) == (False, "no live route on")
    assert refused.reason == "no live route to Module3: Module3 is not reachable"


def test_a_reservation_ahead_is_bound_by_30_s_and_two_transit_times(
    world, timers, station
):
    world.connections = {(M1, M2), (M2, M3), (M3, M4)}
    m = station(M2, M3)

    assert reserve(m[M2], B1, M1, M4).accepted

    # Module 3's reservation ahead, then Module 2's own.
    assert timers.seconds == [AHEAD_BOUND_SECONDS, ARRIVAL_BOUND_SECONDS]


def test_a_box_goes_on_over_module_3_looking_ahead_at_every_hop(
    world, tasks, moves, network, station
):
    world.connections = {(M1, M2), (M2, M3), (M3, M4)}
    m = station(M1, M2, M3, M4)
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert moved(moves, B1) == [M2, M3, M4, WMS]
    assert network.calls[:3] == [
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M3),
        ("ReceiveWorkflow", M2),
    ]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_the_box_goes_on_to_the_module_reserved_ahead(world, tasks, moves, station):
    """Module 2 reserved Module 3 ahead for Box 1. A shorter way to the destination that appears
    meanwhile does not take the box from the module reserved ahead."""
    world.connections = {(M1, M2), (M2, M3), (M3, M4)}
    m = station(M1, M2, M3, M4)
    world.place(B1, M1, destination=M4)
    assert reserve(m[M2], B1, M1, M4).accepted
    world.connections.add((M2, M4))
    world.held[M1].remove(B1)
    world.held[M2].append(B1)
    world.jobs[T1].assigned = M2

    m[M2].receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    assert moved(moves, B1) == [M3, M4, WMS]


def test_a_free_module_looks_ahead_away_from_the_module_the_box_comes_from(
    world, network, station
):
    """Two ways lead from Module 2 to Module 5: straight back over Module 1, where the box comes
    from, and on over Module 3. Module 2 asks Module 3."""
    world.connections = {(M1, M2), (M2, M1), (M1, M5), (M2, M3), (M3, M4), (M4, M5)}
    m = station(M2, M3)

    assert reserve(m[M2], B1, M1, M5).accepted
    assert network.calls == [("ReserveWorkflow", M3)]


# -- One detour before waiting ---------------------------------------------------------------- #

# A diamond: Module 1 hands boxes to Module 2 and to Module 3, and both to Module 4, the exit.
DIAMOND = {(M1, M2), (M1, M3), (M2, M4), (M3, M4)}


def test_a_declined_reserve_leads_to_one_detour_over_the_module_it_ties_with(
    world, tasks, moves, network, station
):
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert network.calls[:3] == [
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M3),
        ("ReserveWorkflow", M4),
    ]
    assert moved(moves, B1) == [M3, M4, WMS]
    (decision,) = m[M1].memory.decisions()
    assert (decision.next_module, decision.detour, decision.detoured) == (M2, M3, True)
    assert decision.refusals == ("occupied",)


def test_when_the_detour_declines_too_the_module_waits_and_asks_both_again(
    caplog, world, tasks, moves, waits, network, station
):
    caplog.set_level(logging.INFO, logger=module_logger.name)
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B2, M3)
    waits.meanwhile = [lambda: world.held[M2].remove(B9)]
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert waits.waited == [RETRY_SECONDS]
    assert [step for step in network.calls[:3]] == [
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M3),
        ("ReserveWorkflow", M2),
    ]
    assert moved(moves, B1)[0] == M2
    assert logged(caplog)[:2] == [
        "Module1: Module2 refused to reserve: occupied by Box9; asking Module3 instead",
        "Module1: Module3 refused to reserve: occupied by Box2; asking again in 0.1 s",
    ]
    (decision,) = m[M1].memory.decisions()
    assert (decision.detour, decision.detoured) == (M3, False)
    assert decision.detour_refusals == ("occupied",)
    assert decision.retries == 1


def test_the_detour_is_never_the_module_the_box_came_from(
    world, tasks, waits, network, station
):
    """Box 1 came to Module 2 from Module 1, which links on to the destination too: Module 2
    waits for Module 3, its next module, and steps back to nobody."""
    world.connections = {(M1, M2), (M2, M1), (M1, M5), (M2, M3), (M3, M4), (M4, M5)}
    m = station(M2, M3, M4, M5)
    world.place(B9, M3)
    waits.meanwhile = [lambda: world.held[M3].remove(B9)]
    world.place(B1, M2, origin=M1, destination=M5)

    m[M2].receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    asked = [peer for step, peer in network.calls if step == "ReserveWorkflow"]
    assert M1 not in asked
    assert waits.waited == [RETRY_SECONDS]


def test_a_detour_the_box_cannot_be_carried_on_from_is_asked_no_more(
    world, tasks, waits, network, station
):
    """Module 3, the detour, cannot be reached: Module 1 waits for Module 2 alone."""
    world.connections = set(DIAMOND)
    m = station(M1, M2, M4)
    world.unregistered.add(M3)
    world.place(B9, M2)
    waits.meanwhile = [lambda: None, lambda: world.held[M2].remove(B9)]
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    asked = [peer for step, peer in network.calls if step == "ReserveWorkflow"]
    assert asked[:4] == [M2, M2, M2, M4]
    assert waits.waited == [RETRY_SECONDS] * 2


# -- Longest-waiting first ---------------------------------------------------------------------- #


def test_a_module_that_becomes_free_serves_the_box_that_asked_first(world, station):
    world.connections = {(M1, M3), (M2, M3)}
    m = station(M3)
    world.place(B9, M3)
    world.place(B1, M1, destination=M3)
    world.place(B2, M2, destination=M3)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    assert reserve(m[M3], B2, M2, M3).refusal == "occupied"
    world.held[M3].remove(B9)

    second = reserve(m[M3], B2, M2, M3)
    first = reserve(m[M3], B1, M1, M3)

    assert (second.accepted, second.refusal) == (False, "behind")
    assert second.reason == "Box1 has waited longer"
    assert first.accepted


def test_a_box_refused_through_a_look_ahead_keeps_its_place_ahead_of_one_that_asked_later(
    world, station
):
    """Box 1, on Module 1, asked Module 2, whose next module, Module 3, was occupied. Box 2, on
    Module 4, asked Module 3 later. Once Module 3 is free, Box 1 comes first, through Module 2's
    inner ask."""
    world.connections = {(M1, M2), (M2, M3), (M4, M3)}
    m = station(M2, M3)
    world.place(B9, M3)
    world.place(B1, M1, destination=M3)
    world.place(B2, M4, destination=M3)
    assert reserve(m[M2], B1, M1, M3).refusal == "blocked"
    assert reserve(m[M3], B2, M4, M3).refusal == "occupied"
    world.held[M3].remove(B9)

    later = reserve(m[M3], B2, M4, M3)
    earlier = reserve(m[M2], B1, M1, M3)

    assert (later.accepted, later.refusal) == (False, "behind")
    assert earlier.accepted


def test_the_wms_s_reserve_for_a_spawn_waits_behind_every_box_already_waiting(
    world, station
):
    """Module 1, an entry module, also takes boxes from Module 5. Box 1 waited there; the WMS's
    Reserve for a new box comes behind it, and takes no place."""
    world.connections = {(M5, M1), (M1, M2)}
    m = station(M1, M2)
    world.place(B9, M1)
    world.place(B1, M5, destination=M2)
    assert reserve(m[M1], B1, M5, M2).refusal == "occupied"
    world.held[M1].remove(B9)

    spawn = reserve(m[M1], B3, WMS, M2)
    box = reserve(m[M1], B1, M5, M2)

    assert (spawn.accepted, spawn.refusal) == (False, "behind")
    assert spawn.reason == "Box1 has waited longer"
    assert box.accepted


def test_a_spawn_that_asks_first_is_served_first(world, station):
    world.connections = {(M5, M1), (M1, M2)}
    m = station(M1, M2)

    assert reserve(m[M1], B3, WMS, M2).accepted
    assert reserve(m[M1], B1, M5, M2).refusal == "reserved"


def test_a_box_that_moved_elsewhere_leaves_the_order(world, station):
    world.connections = {(M1, M3), (M2, M3), (M1, M4)}
    m = station(M3)
    world.place(B9, M3)
    world.place(B1, M1, destination=M3)
    world.place(B2, M2, destination=M3)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    world.held[M3].remove(B9)
    world.held[M1].remove(B1)
    world.held[M4].append(B1)
    world.jobs[T1].assigned = M4

    assert reserve(m[M3], B2, M2, M3).accepted


def test_a_box_whose_transport_ended_leaves_the_order(world, station):
    world.connections = {(M1, M3), (M2, M3)}
    m = station(M3)
    world.place(B9, M3)
    world.place(B1, M1, destination=M3)
    world.place(B2, M2, destination=M3)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    world.held[M3].remove(B9)
    world.jobs[T1].status = OperationStatus.FAILED

    assert reserve(m[M3], B2, M2, M3).accepted


def test_a_box_that_has_stopped_asking_comes_first_no_more_and_keeps_its_place(
    world, clock, station
):
    """Box 1 waits on Module 1 for something else first, and has not asked Module 3 for a while:
    Box 2 is served. Once Box 1 asks again, it is ahead of Box 3, which asked after it."""
    world.connections = {(M1, M3), (M2, M3), (M4, M3)}
    m = station(M3)
    world.place(B9, M3)
    for box, on in ((B1, M1), (B2, M2), (B3, M4)):
        world.place(box, on, destination=M3)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    assert reserve(m[M3], B2, M2, M3).refusal == "occupied"
    clock.now += 60
    assert reserve(m[M3], B3, M4, M3).refusal == "occupied"
    world.held[M3].remove(B9)

    assert reserve(m[M3], B2, M2, M3).accepted
    world.held[M3].append(B2)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    world.held[M3].remove(B2)
    m[M3].receive(ReceiveRequest(box=B2, source_module=M2))

    assert reserve(m[M3], B3, M4, M3).refusal == "behind"
    assert reserve(m[M3], B1, M1, M3).accepted


def test_a_box_that_arrived_leaves_the_order(world, tasks, station):
    world.connections = {(M1, M3), (M2, M3)}
    m = station(M3)
    world.place(B9, M3)
    world.place(B1, M1, destination=M3)
    world.place(B2, M2, destination=M3)
    assert reserve(m[M3], B1, M1, M3).refusal == "occupied"
    world.held[M3].remove(B9)
    assert reserve(m[M3], B1, M1, M3).accepted
    m[M3].receive(ReceiveRequest(box=B1, source_module=M1))
    world.held[M1].remove(B1)

    assert reserve(m[M3], B2, M2, M3).accepted


# -- The fault still collides ------------------------------------------------------------------- #


def test_skip_reserve_makes_a_module_accept_without_looking_ahead(
    world, network, station
):
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2, M3)
    m[M2].faults["skip-reserve"] = True
    world.place(B9, M3)

    assert reserve(m[M2], B1, M1, M3).accepted
    assert network.calls == []


def test_the_wms_s_reserve_looks_ahead_also_on_an_origin_with_skip_reserve(
    world, network, station
):
    """The WMS spawns a box only onto an origin whose next module accepts at once."""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M1, M2)
    m[M1].faults["skip-reserve"] = True
    world.place(B9, M2)

    refused = reserve(m[M1], B1, WMS, M3)

    assert (refused.accepted, refused.refusal) == (False, "blocked")
    assert network.calls == [("ReserveWorkflow", M2)]


def test_skip_reserve_hands_the_box_on_without_asking_and_meets_an_occupied_module(
    world, tasks, network, waits, moves, station
):
    """Module 1 skips Reserve and the detour for its own hop: the store refuses the handover onto
    the occupied Module 2. Then it asks properly, and takes the detour."""
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    m[M1].faults["skip-reserve"] = True
    world.place(B9, M2)
    world.place(B1, M1, destination=M4)
    # The store refuses a second box on Module 2.
    m[M1].handover.fails_with = [store_refusal(POSSESSION_REPORT)]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert m[M1].handover.calls[0][0] == M2
    assert [e.kind for e in m[M1].events.after(None)] == ["commit_refused"]
    assert [peer for step, peer in network.calls if step == "ReserveWorkflow"][:2] == [
        M2,
        M3,
    ]
    assert moved(moves, B1) == [M3, M4, WMS]


# -- What goes wrong ---------------------------------------------------------------------------- # #


class ConnectionsThatFail(Reads):
    """A module's reads, whose read of the connections fails as a store error does."""

    def connections(self):
        raise RuntimeError("the store did not answer")


def test_a_look_ahead_that_fails_keeps_no_reservation(world, station):
    """An error of the store during the look-ahead is raised, and the module is free again for the
    next box."""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2, M3)
    m[M2].graph = ConnectionsThatFail(world, M2)

    with pytest.raises(RuntimeError):
        reserve(m[M2], B1, M1, M3)

    m[M2].graph = Reads(world, M2)
    assert reserve(m[M2], B1, M1, M3).accepted


def test_a_next_module_that_cannot_carry_the_box_on_is_answered_by_the_detour(
    world, tasks, moves, station
):
    """Module 2, the next module, refuses for good, since Module 4 behind it cannot be reached:
    Module 1 asks its detour, Module 3, before it ends the transport, and the box goes there."""
    world.connections = {(M1, M2), (M1, M3), (M2, M4), (M3, M5), (M5, M4)}
    m = station(M1, M2, M3, M5)
    world.unregistered.add(M4)
    world.place(B1, M1, destination=M4)

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    assert moved(moves, B1)[:1] == [M3]


# -- A stop -------------------------------------------------------------------- #


class ConnectionsThatStop(Reads):
    """A module's reads, whose read of the connections comes as the stop of its process begins."""

    def __init__(self, world: World, reader: IRI, stop: Stop) -> None:
        super().__init__(world, reader)
        self.stop = stop

    def connections(self):
        self.stop.begun = True
        return super().connections()


def test_a_module_whose_stop_begins_during_its_look_ahead_asks_no_reserve_and_keeps_none(
    world, network, timers, station, stops
):
    """Once its stop has begun, a module asks no Reserve, so its look-ahead asks none either,
    and it refuses with "the system is stopping"."""
    world.connections = {(M1, M2), (M2, M3)}
    m = station(M2, M3)
    m[M2].graph = ConnectionsThatStop(world, M2, stops[M2])

    refused = reserve(m[M2], B1, M1, M3)

    assert (refused.accepted, refused.refusal, refused.reason) == (
        False,
        None,
        "the system is stopping",
    )
    assert network.calls == []
    assert timers.seconds == []


def test_a_module_whose_stop_begins_while_it_waits_asks_no_detour(
    world, tasks, waits, network, station, stops
):
    """Both ways are taken when Module 1 asks; its stop begins while it waits: it asks no
    Reserve again, not its detour either, and ends the transport, "the system is stopping"."""
    world.connections = set(DIAMOND)
    m = station(M1, M2, M3, M4)
    world.place(B9, M2)
    world.place(B2, M3)
    world.place(B1, M1, destination=M4)

    def stop_begins() -> None:
        stops[M1].begun = True

    waits.meanwhile = [stop_begins]

    m[M1].box_transport(world.transport(T1))
    tasks.run()

    asked = [peer for step, peer in network.calls if step == "ReserveWorkflow"]
    assert asked == [M2, M3]
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "the system is stopping"
