"""The WarehouseManagementSystem's logic, without a network or a triple store.

It spawns boxes onto real FlexConveyorModules, on the FlexConveyor station of `_station`, and is
notified by them when a transport ends. So a spawn is followed here all the way: onto the origin,
over one hop, and back.
"""

from __future__ import annotations

import logging
from importlib import resources
from uuid import UUID

import httpx
import pytest
from _station import (
    BoxNames,
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
    World,
    capability_of,
    spawned_box,
    store_refusal,
)
from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import TripleStoreResponseError
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, SH, XSD

from jms_kapps.uc2.discovery import ModulesAndConnections
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import ReserveRequest
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.vocabulary import (
    FC,
    FC_NS,
    FCI_NS,
    WMS,
    box_iri,
    local_name,
    module_iri,
    transport_iri,
)
from jms_kapps.uc2.wms import (
    SpawnBoxRequest,
    WarehouseManagementSystem,
)
from jms_kapps.uc2.wms import logger as wms_logger

M1, M2, M3 = module_iri(1), module_iri(2), module_iri(3)
B1, B2 = spawned_box(1), spawned_box(2)
N1 = local_name(B1)
T1, T2 = transport_iri(B1), transport_iri(B2)
TRANSPORT_1 = capability_of(module_iri(1))
REPORT = (
    f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"
    f'_:report <{SH.conforms}> "false"^^<{XSD.boolean}> .\n'
)
# What the triplestore interface raises when the store's shapes refuse a write.
REFUSAL = store_refusal(REPORT)


@pytest.fixture
def world():
    """Module 1, an entry module, connected to Module 2, an exit module."""
    world = World()
    world.connections = {(M1, M2)}
    world.wms_iri = WMS
    world.classes[FC.EntryModule] = [M1]
    world.classes[FC.ExitModule] = [M2]
    return world


@pytest.fixture
def network():
    return Network()


@pytest.fixture
def records(world):
    return Records(world)


@pytest.fixture
def tasks():
    return Tasks()


@pytest.fixture
def notifications(world, tasks):
    return Notifications(world, tasks)


@pytest.fixture
def moves(world):
    return Moves(world)


class Picks:
    """Every choice the WMS made, as what it chose from. It takes the last one."""

    def __init__(self) -> None:
        self.made: list[list] = []

    def __call__(self, options):
        self.made.append(list(options))
        return options[-1]


@pytest.fixture
def picks():
    return Picks()


@pytest.fixture
def starts(tasks):
    return Starts(tasks)


@pytest.fixture
def stop():
    return Stop()


@pytest.fixture
def timers():
    """The bound of a reservation, which passes only when a test fires it."""
    return Timers()


@pytest.fixture
def modules(world, network, records, notifications, moves, starts, timers):
    """Modules 1, 2 and 3, each of which its peers call and the notification of a transport
    assigned to it reaches. Module 3 is in no class until a test puts it in one."""
    for resource in (M1, M2, M3):
        module = FlexConveyorModule(
            resource,
            graph=Reads(world, resource),
            handover=MiddlewareHandover(world, resource),
            call=network.call,
            records=records,
            notify=notifications.notify,
            move=moves.move,
            start=starts,
            later=timers.later,
        )
        network.peers[resource] = module
        notifications.receivers[resource] = module.box_transport
    return network.peers


@pytest.fixture
def wms(world, records, network, notifications, picks, modules, stop):
    """The WMS, which asks the modules' Reserve, creates each box on its origin, and notifies
    the origin."""
    built = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=records,
        call=network.call,
        notify=notifications.notify,
        choose=picks,
        events=HandoverEvents(WMS, token="wms"),
        stopping=stop.has_begun,
        new_box=BoxNames(),
    )
    notifications.receivers[WMS] = built.transport_ended
    return built


def spawn(wms, origin=M1, destination=M2):
    return wms.spawn(
        SpawnBoxRequest(
            origin=None if origin is None else str(origin),
            destination=None if destination is None else str(destination),
        )
    )


def logged(caplog, level, logger=wms_logger) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == logger.name and r.levelno == level
    ]


def after_reserve(call, then):
    """`call`, which runs `then` once, right after the first call of a Reserve has answered: what
    happens between the origin's answer and the WMS's create."""
    pending = [then]

    def calling(endpoint: str, payload):
        answer = call(endpoint, payload)
        if endpoint.endswith(f"|{FC.ReserveWorkflow}") and pending:
            pending.pop()()
        return answer

    return calling


def test_a_spawn_creates_the_box_on_its_origin_with_its_transport_queued_and_assigned_to_it(
    wms, world, records
):
    """One create: the box, its transport and the box's first possession, on the origin
    (Etienne, 2026-10-06, jms_kapps 41)."""
    result = spawn(wms)

    assert result.accepted, result
    assert (result.box, result.operation) == (str(B1), str(T1))
    assert records.writes == [("open", B1, M1, M2, TRANSPORT_1, M1)]
    assert world.possessor_of(B1) == M1
    assert world.jobs[T1].assigned == M1
    assert world.jobs[T1].status == OperationStatus.QUEUED


def test_a_spawn_asks_the_origin_s_reserve_once_and_then_notifies_the_origin(
    wms, network, notifications
):
    """One Reserve call, one commit and one notification: the origin calls nothing of the WMS,
    and the WMS neither hands the box over nor moves it. The origin asks the Reserve of its own
    next module before it answers (added on 2026-10-06, jms_kapps 43, decided by Etienne)."""
    spawn(wms)

    assert network.calls == [("ReserveWorkflow", M1), ("ReserveWorkflow", M2)]
    assert notifications.sent == [(T1, M1)]


# -- The spawn under the look-ahead -------------------------------------------------------------- #


def via_module_3(world) -> None:
    """Module 1 hands boxes to Module 3, and Module 3 to Module 2, the exit module."""
    world.connections = {(M1, M3), (M3, M2)}


def test_the_wms_spawns_only_onto_an_origin_whose_next_module_accepts_at_once(
    wms, world, records, notifications
):
    """Module 3, the origin's next module, holds a box: the origin refuses, and nothing is
    written, so no box waits on the origin for a module that cannot take it (Etienne,
    2026-10-06, jms_kapps 43)."""
    via_module_3(world)
    world.place(box_iri(9), M3)

    result = spawn(wms)

    assert not result.accepted
    assert result.reason == (
        "Module1 refused to reserve: Module3, its next module, refused to reserve: occupied by "
        "Box9"
    )
    assert (records.writes, notifications.sent) == ([], [])


def test_a_spawn_reserves_the_origin_s_next_module_ahead_and_the_box_goes_on_there(
    wms, world, network, tasks, moves
):
    via_module_3(world)

    assert spawn(wms).accepted
    assert network.calls == [("ReserveWorkflow", M1), ("ReserveWorkflow", M3)]
    refused = network.peers[M3].reserve(
        ReserveRequest(box=str(B2), source_module=str(M1), destination=str(M2))
    )
    assert refused.refusal == "reserved"
    tasks.run()

    assert [receiver for box, receiver, _ in moves.moved if box == B1] == [M3, M2, WMS]


def test_once_the_stop_has_begun_a_spawn_is_refused_and_opens_no_transport(
    wms, world, records, moves, stop
):
    """A spawn of the workflow and one of the spawn rate's timer alike: both are this spawn."""
    stop.begun = True

    result = spawn(wms)

    assert (result.accepted, result.reason) == (False, "the system is stopping")
    assert (result.origin, result.destination) == (str(M1), str(M2))
    assert (result.box, result.operation) == (None, None)
    assert records.writes == []
    assert moves.moved == []
    assert world.jobs == {}
    assert [answer.result for answer in wms.memory.answers()] == [result]


@pytest.mark.parametrize(
    "graph", ["no entry module is free", "the graph has no module other than Module1"]
)
def test_once_the_stop_has_begun_a_spawn_says_so_whatever_else_stands_in_its_way(
    wms, world, records, stop, graph
):
    """A spawn of the timer does not name its modules, so the WMS picks them: the stop is the
    reason it gives, not what it would have found in the graph. (Changed on 2026-10-06, jms_kapps
    45, decided by Etienne: roles are optional, so a graph that names no exit module counts every
    module as one, and the second case is a graph of Module 1 alone. Until then it was "the graph
    has no exit module".)"""
    if graph == "no entry module is free":
        world.place(box_iri(7), M1)
    else:
        world.classes[FC.ExitModule] = []
    stop.begun = True

    result = spawn(wms, origin=None, destination=None)

    assert (result.accepted, result.reason) == (False, "the system is stopping")
    assert records.writes == []


def test_a_stop_that_begins_while_a_spawn_reads_the_graph_still_creates_no_box(
    world, records, network, notifications, modules, stop
):
    """The reads before the create take a while on a shared store, and the stop may begin during
    them: the WMS asks again as the last step before the commit that creates the box."""

    class StopWhileReading(Reads):
        def modules_and_connections(self) -> ModulesAndConnections:
            stop.begun = True
            return super().modules_and_connections()

    wms = WarehouseManagementSystem(
        WMS,
        graph=StopWhileReading(world, WMS),
        records=records,
        call=network.call,
        notify=notifications.notify,
        stopping=stop.has_begun,
    )

    result = spawn(wms)

    assert (result.accepted, result.reason) == (False, "the system is stopping")
    assert records.writes == []
    assert world.jobs == {}


def test_the_wms_reads_an_origins_capability_once(wms, world):
    spawn(wms)
    spawn(wms)

    asked = [read for read in world.reads if read[:2] == (WMS, "capability")]
    assert asked == [(WMS, "capability", (M1, FC.BoxTransportCapability))]


@pytest.mark.parametrize("origin", [M1, None], ids=["named", "picked by the WMS"])
def test_an_origin_reserved_for_another_box_refuses_the_spawn_and_nothing_is_written(
    wms, world, records, modules, network, notifications, origin
):
    """A module that passes a box onto the entry module has reserved it first: the WMS's Reserve
    is refused, and the spawn with it. It tries no other module, and waits for none."""
    modules[M1].reserve(ReserveRequest(box=str(box_iri(5)), source_module=str(M3)))

    result = spawn(wms, origin=origin)

    assert (result.accepted, result.reason, result.report) == (
        False,
        "Module1 refused to reserve: reserved for Box5",
        None,
    )
    assert (result.origin, result.box, result.operation) == (str(M1), None, None)
    assert records.writes == []
    assert world.jobs == {}
    assert network.calls == [("ReserveWorkflow", M1)]
    assert notifications.sent == []


def test_an_occupied_origin_refuses_the_spawn_before_anything_is_written(
    wms, world, records
):
    world.place(box_iri(7), M1)

    result = spawn(wms)

    assert (result.accepted, result.reason) == (
        False,
        "Module1 refused to reserve: occupied by Box7",
    )
    assert records.writes == []


def test_the_wms_logs_its_side_of_each_step_of_its_spawn(caplog, wms):
    """The origin's answer to its Reserve, the transport queued once the box is created on the
    origin, and the spawn once the origin is notified."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    spawn(wms)

    assert logged(caplog, logging.INFO) == [
        f"WarehouseManagementSystem1: Module1's ReserveWorkflow accepted {N1}",
        f"WarehouseManagementSystem1: {N1}_transport is queued",
        f"WarehouseManagementSystem1: spawned {N1} onto Module1, for Module2",
    ]


def test_the_wms_logs_a_reserve_that_the_origin_refused(caplog, wms, world):
    """One record of the refused spawn, with its reason. (Changed on 2026-10-07, jms_kapps 51,
    decided by Etienne: every spawn ends in one record. Until then the record read "refused a
    spawn onto Module1, for Module2: Module1 refused to reserve: occupied by Box7".)"""
    caplog.set_level(logging.INFO, logger="jms_kapps")
    world.place(box_iri(7), M1)

    spawn(wms)

    assert logged(caplog, logging.INFO) == [
        (
            "WarehouseManagementSystem1: spawn refused: Module1 refused to reserve: occupied "
            "by Box7 (origin Module1, destination Module2)"
        )
    ]


def test_a_module_that_asks_an_entry_module_the_wms_has_reserved_is_refused_as_reserved(
    wms, world, network, modules
):
    """The other way round: the WMS's Reserve came first, so a module that passes a box onto the
    entry module at the same moment is refused as reserved, and waits its turn, and the WMS's
    create is not refused (Etienne, 2026-10-05)."""
    refusals = []
    wms.call = after_reserve(
        network.call,
        lambda: refusals.append(
            modules[M1].reserve(
                ReserveRequest(box=str(box_iri(5)), source_module=str(M3))
            )
        ),
    )

    result = spawn(wms)

    [refused] = refusals
    assert (refused.accepted, refused.refusal) == (False, "reserved")
    assert result.accepted, result
    assert world.possessor_of(B1) == M1


def test_a_create_the_store_refuses_writes_nothing_and_the_origin_s_reservation_lapses(
    wms, world, records, timers
):
    """No notification comes for the box the origin reserved itself for, so its reservation
    lapses at its bound, and a later spawn onto it is accepted."""
    records.fails_with = REFUSAL
    first = spawn(wms)
    assert world.jobs == {}
    records.fails_with = None
    timers.fire()

    second = spawn(wms)

    assert (first.accepted, first.report) == (False, REPORT)
    assert (first.box, first.operation) == (None, None)
    assert second.accepted, second
    assert second.box == str(B2)


def test_a_create_the_store_refuses_is_answered_logged_and_emitted_with_the_report(
    caplog, wms, notifications
):
    """As when a neighbour with `skip-reserve` armed has filled the origin since its answer to
    Reserve: the store refuses the box on it. The answer, the log line and the event on the WMS's
    handover event stream carry the store's report, and nobody is notified."""
    wms.records.fails_with = REFUSAL

    result = spawn(wms)

    reason = f"the triple store refused creating {N1} on Module1"
    assert (result.accepted, result.reason, result.report) == (False, reason, REPORT)
    assert (result.origin, result.destination) == (str(M1), str(M2))
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING) == [
        f"WarehouseManagementSystem1: {reason}; the spawn is refused:\n{REPORT}"
    ]
    assert [
        (e.kind, e.resource, e.box, e.peer, e.transport, e.refusal and e.refusal.report)
        for e in wms.events.after(None)
    ] == [("commit_refused", str(WMS), str(B1), str(M1), None, REPORT)]
    assert [answer.result for answer in wms.memory.answers()] == [result]


def test_two_spawns_in_flight_take_two_boxes(wms, world, network, records, modules):
    """The first spawn's box is not in the graph yet while its origin's answer to Reserve is on
    its way, so the second does not count it there. The second goes from Module 3 to Module 4, so
    that no module the first one reserved stands in its way."""
    m4 = module_iri(4)
    network.peers[m4] = FlexConveyorModule(
        m4,
        graph=Reads(world, m4),
        handover=MiddlewareHandover(world, m4),
        call=network.call,
        records=records,
        notify=lambda operation, resource: None,
        move=lambda box, receiver: None,
    )
    world.connections.add((M3, m4))
    world.classes[FC.EntryModule] = [M1, M3]
    world.classes[FC.ExitModule] = [M2, m4]
    second = []
    wms.call = after_reserve(
        network.call, lambda: second.append(spawn(wms, origin=M3, destination=m4))
    )

    first = spawn(wms, origin=M1)

    assert first.accepted and second[0].accepted, (first, second)
    assert {first.box, second[0].box} == {str(B1), str(B2)}


def failing(call, workflow_class, error, *, after_the_call=False):
    """`call`, whose every call of a workflow of `workflow_class` fails with `error`: before it
    reaches the module, or after it, so that the module got the call and its answer is lost."""

    def calling(endpoint: str, payload):
        if not endpoint.endswith(f"|{workflow_class}"):
            return call(endpoint, payload)
        if after_the_call:
            call(endpoint, payload)
        raise error

    return calling


LOST = httpx.ReadTimeout("timed out")


def test_a_spawn_whose_origin_cannot_be_reached_is_refused_with_why_and_writes_nothing(
    caplog, wms, network, records, world
):
    """The origin's Reserve cannot be reached: nothing is written yet, so the spawn is refused
    with why, as a module says it of its next module, and nothing raises."""
    wms.call = failing(
        network.call, FC.ReserveWorkflow, httpx.ConnectError("connection refused")
    )

    result = spawn(wms)

    assert (result.accepted, result.reason) == (
        False,
        "Module1 is not reachable: connection refused",
    )
    assert (result.box, result.operation) == (None, None)
    assert records.writes == []
    assert world.jobs == {}
    assert logged(caplog, logging.WARNING) == []


def test_a_spawn_whose_answer_to_reserve_is_lost_is_refused_and_writes_nothing(
    wms, network, records, timers
):
    """The origin reserved itself, but its answer did not come back: the spawn is refused with
    why and writes nothing, and the origin's reservation lapses at its bound, so a later spawn
    onto it is accepted."""
    wms.call = failing(network.call, FC.ReserveWorkflow, LOST, after_the_call=True)

    result = spawn(wms)
    wms.call = network.call
    timers.fire()
    later = spawn(wms)

    assert (result.accepted, result.reason) == (
        False,
        "Module1 is not reachable: timed out",
    )
    assert later.accepted, later
    assert [write[0] for write in records.writes] == ["open"]


def test_a_stop_that_begins_once_the_origin_has_reserved_itself_creates_no_box(
    wms, world, records, network, stop, timers
):
    """The stop begins between the origin's answer to Reserve and the create: the WMS creates
    nothing and refuses with the stop as its reason. The origin's reservation lapses at its
    bound."""

    def begin_stop() -> None:
        stop.begun = True

    wms.call = after_reserve(network.call, begin_stop)

    result = spawn(wms)
    stop.begun = False
    timers.fire()
    later = spawn(wms)

    assert (result.accepted, result.reason) == (False, "the system is stopping")
    assert later.accepted, later
    assert [write[0] for write in records.writes] == ["open"]


def test_without_an_origin_the_wms_picks_a_free_entry_module(wms, world, picks):
    world.classes[FC.EntryModule] = [M1, M3]
    world.place(box_iri(7), M3)

    result = spawn(wms, origin=None, destination=None)

    assert result.accepted, result
    assert picks.made == [[M1], [M2]]
    assert world.possessor_of(B1) == M1


def test_with_no_free_entry_module_the_wms_spawns_nothing_and_says_why(
    wms, world, records
):
    world.place(box_iri(7), M1)

    result = spawn(wms, origin=None)

    assert not result.accepted
    assert result.reason == "no entry module is free"
    assert records.writes == []


@pytest.mark.parametrize("destination", [M3, None], ids=["named", "left to the WMS"])
def test_a_named_origin_that_is_not_an_entry_module_is_refused_before_anything_is_written(
    wms, world, records, destination
):
    """Where the topology names entry modules, a named origin must be one (Etienne, 2026-10-05).
    (Changed on 2026-10-06, jms_kapps 45, decided by Etienne: the role holds only where the
    topology names it. Until then the reason read: "A box from a module that is no entry module
    could meet one from an entry module head-on, which the load check of the topology does not
    cover".)"""
    world.classes[FC.ExitModule] = [M2, M3]

    result = spawn(wms, origin=M2, destination=destination)

    assert (result.accepted, result.reason, result.report) == (
        False,
        "Module2 is not an entry module",
        None,
    )
    assert result.origin == str(M2)
    assert records.writes == []
    assert wms.memory.answers()[0].why == "Module2 is not an entry module"


@pytest.mark.parametrize("origin", [M1, None], ids=["named", "left to the WMS"])
def test_a_named_destination_that_is_not_an_exit_module_is_refused_before_anything_is_written(
    wms, world, records, origin
):
    world.classes[FC.EntryModule] = [M1, M3]

    result = spawn(wms, origin=origin, destination=M3)

    assert (result.accepted, result.reason, result.report) == (
        False,
        "Module3 is not an exit module",
        None,
    )
    assert result.destination == str(M3)
    assert records.writes == []


def test_a_module_that_is_an_entry_and_an_exit_may_start_one_box_and_end_another(
    wms, world, tasks
):
    """Module 2 ends Box 1, then starts Box 2. (Changed on 2026-10-06, jms_kapps 43, decided by
    Etienne: Module 1's look-ahead reserves Module 2 for Box 1, so Box 2 starts once Box 1 is
    back. Until then both spawns came one after the other, with no box carried on between.)"""
    world.connections.add((M2, M1))
    world.classes[FC.EntryModule] = [M1, M2]
    world.classes[FC.ExitModule] = [M1, M2]

    first = spawn(wms, origin=M1, destination=M2)
    tasks.run()
    second = spawn(wms, origin=M2, destination=M1)

    assert first.accepted, first
    assert second.accepted, second


# -- Roles are optional (jms_kapps 45, decided by Etienne on 2026-10-06) ------------------------ #


def no_roles(world, *modules):
    """The topology names no role: `modules` are FlexConveyorModules, and none is an entry module
    or an exit module."""
    world.classes.clear()
    world.classes[FC.FlexConveyorModule] = list(modules)


def test_where_the_topology_names_no_roles_a_box_starts_on_any_free_module_for_any_other(
    wms, world, picks
):
    """Module 1 holds a box, so the WMS picks the origin among Modules 2 and 3, and then the
    destination among the modules other than the origin that it reaches. (Changed on 2026-10-07,
    jms_kapps 51, decided by Etienne: the WMS picks only a destination that the origin reaches, so
    Module 2 hands boxes on to Module 1, and Module 3 reaches both. Until then the connections
    were Module 1 to Module 2 and Module 3 to Module 2, and the docstring ended "among the modules
    other than the origin".)"""
    no_roles(world, M1, M2, M3)
    world.connections = {(M1, M2), (M2, M1), (M3, M2)}
    world.place(box_iri(7), M1)

    result = spawn(wms, origin=None, destination=None)

    assert result.accepted, result
    assert picks.made == [[M2, M3], [M1, M2]]
    assert (result.origin, result.destination) == (str(M3), str(M2))
    assert world.possessor_of(B1) == M3


@pytest.mark.parametrize(
    ("origin", "destination"),
    [(M3, M2), (M2, M1)],
    ids=["Module3 to Module2", "Module2 to Module1"],
)
def test_where_the_topology_names_no_roles_a_spawn_may_name_any_two_different_modules(
    wms, world, origin, destination
):
    no_roles(world, M1, M2, M3)
    world.connections = {(M1, M2), (M2, M1), (M3, M2)}

    result = spawn(wms, origin=origin, destination=destination)

    assert result.accepted, result
    assert world.possessor_of(B1) == origin


def test_where_the_topology_names_entry_modules_only_a_box_may_end_on_any_other_module(
    wms, world, picks
):
    world.classes[FC.ExitModule] = []
    world.classes[FC.FlexConveyorModule] = [M2, M3]
    world.connections = {(M1, M2), (M2, M3)}

    result = spawn(wms, origin=None, destination=None)

    assert result.accepted, result
    assert picks.made == [[M1], [M2, M3]]
    assert result.destination == str(M3)


def test_where_the_topology_names_exit_modules_only_a_box_may_start_on_any_free_module_that_reaches_one(
    wms, world, picks
):
    """Module 2, the only exit module, reaches no exit module other than itself, so it is no
    origin. (Changed on 2026-10-07, jms_kapps 51, decided by Etienne: the WMS picks only an origin
    that reaches a destination. Until then the test was named "..._may_start_on_any_free_module",
    and the WMS picked the origin among Modules 1, 2 and 3.)"""
    world.classes[FC.EntryModule] = []
    world.classes[FC.FlexConveyorModule] = [M1, M3]
    world.connections = {(M1, M2), (M3, M2)}

    result = spawn(wms, origin=None, destination=None)

    assert result.accepted, result
    assert picks.made == [[M1, M3], [M2]]
    assert result.origin == str(M3)


@pytest.mark.parametrize(
    ("unnamed", "wrong", "reason"),
    [
        (FC.ExitModule, (M2, M3), "Module2 is not an entry module"),
        (FC.EntryModule, (M1, M3), "Module3 is not an exit module"),
    ],
    ids=["entry modules named only", "exit modules named only"],
)
def test_a_named_spawn_is_held_to_a_role_only_where_the_topology_names_it(
    wms, world, records, unnamed, wrong, reason
):
    """Module 1 to Module 2 is accepted either way: Module 2 needs no role where exit modules are
    not named, and Module 1 none where entry modules are not."""
    world.classes[unnamed] = []
    world.classes[FC.FlexConveyorModule] = [M1, M2, M3]
    world.connections = {(M1, M2), (M2, M3)}

    refused = spawn(wms, *wrong)
    assert records.writes == []
    accepted = spawn(wms, origin=M1, destination=M2)

    assert (refused.accepted, refused.reason) == (False, reason)
    assert accepted.accepted, accepted


def test_where_the_topology_names_no_roles_a_refusal_says_why_in_those_terms(
    wms, world, records
):
    no_roles(world, M1, M2)
    world.place(box_iri(7), M1)
    world.place(box_iri(8), M2)

    reasons = [
        spawn(wms, origin=origin, destination=destination).reason
        for origin, destination in [
            (None, None),
            (module_iri(9), M2),
            (M1, module_iri(9)),
            (M1, M1),
        ]
    ]
    no_roles(world, M1)
    reasons.append(spawn(wms, origin=M1, destination=None).reason)

    assert reasons == [
        "no module is free",
        "Module9 is not a module",
        "Module9 is not a module",
        "a box's origin and destination must differ",
        "the graph has no module other than Module1",
    ]
    assert records.writes == []


def test_the_modules_a_spawn_may_name_are_those_of_each_role_or_else_every_module(
    wms, world
):
    """What the dashboard's form offers: the entry modules as the origin and the exit modules as
    the destination, and every module for a role the topology does not name."""
    named = wms.origins_and_destinations()
    world.classes[FC.EntryModule] = []
    world.classes[FC.FlexConveyorModule] = [M3]
    exits_only = wms.origins_and_destinations()
    no_roles(world, M1, M2, M3)
    none_named = wms.origins_and_destinations()

    assert named == ([M1], [M2])
    assert exits_only == ([M2, M3], [M2])
    assert none_named == ([M1, M2, M3], [M1, M2, M3])


def test_each_spawn_names_its_box_by_a_new_uuid_whatever_boxes_the_graph_holds(
    world, records, network, notifications, tasks, modules
):
    """The WMS as it runs, with no names of a test's: a box named by a number in the graph gives
    no number to the next box, and two spawns name two boxes, each `Box-<UUID>` in the instances'
    namespace. (Changed on 2026-10-12, jms_kapps 92, decided by Sören on jms_kapps 85: "a new box
    gets a UUID". Until then each spawn took the next free box number, Box 5 after Box 4.)"""
    world.place(box_iri(4), WMS, origin=M1, destination=M2, status=OperationStatus.DONE)
    wms = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=records,
        call=network.call,
        notify=notifications.notify,
    )

    first = spawn(wms)
    tasks.run()  # the first box's journey, back to the WMS
    second = spawn(wms)

    boxes = [IRI(str(answer.box)) for answer in (first, second)]
    assert first.accepted and second.accepted, (first, second)
    assert boxes[0] != boxes[1]
    for box in boxes:
        assert str(box).startswith(f"{FCI_NS}Box-")
        name = str(box).removeprefix(f"{FCI_NS}Box-")
        assert str(UUID(name)) == name


def test_a_failed_record_the_store_refuses_is_logged_and_the_spawn_still_answers(
    caplog, wms, world, records, notifications
):
    notifications.undelivered_to[M1] = "Module1 has no live Service"
    records.fail_fails_with = REFUSAL

    result = spawn(wms)

    assert (result.accepted, result.reason) == (
        False,
        "Module1 is not reachable: Module1 has no live Service",
    )
    assert world.jobs[T1].status == OperationStatus.QUEUED
    assert logged(caplog, logging.WARNING)[-1] == (
        f"WarehouseManagementSystem1: the triple store refused recording {N1}_transport "
        f"failed:\n{REPORT}"
    )


def test_a_store_error_that_is_no_shacl_refusal_raises(wms, records):
    """Only a `ShaclValidationError` is the store's refusal, even when another error's text
    quotes a report."""
    error = TripleStoreResponseError(f"Error while querying GraphDB (500) - {REPORT}")
    records.fails_with = error

    with pytest.raises(TripleStoreResponseError) as raised:
        spawn(wms)

    assert raised.value is error


def test_a_spawn_that_is_refused_before_the_store_answers_carries_no_report(wms):
    """A box that would start and end on one module: the WMS refuses, and the store refused
    nothing."""
    result = spawn(wms, origin=M1, destination=M1)

    assert (result.accepted, result.report) == (False, None)


def test_a_spawn_whose_notification_does_not_reach_the_origin_fails_the_transport_with_why(
    caplog, wms, world, records, notifications
):
    """The origin's process went after its answer to Reserve, as at a stop: the box is on the
    origin, but nothing would start the origin's BoxTransportWorkflow. The WMS, which created it
    there and cannot reach the origin, ends the transport failed, so it is not left queued."""
    notifications.undelivered_to[M1] = "Module1 has no live Service"

    result = spawn(wms)

    assert not result.accepted
    assert result.reason == "Module1 is not reachable: Module1 has no live Service"
    assert (result.box, result.operation) == (str(B1), str(T1))
    assert world.possessor_of(B1) == M1
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == (
        "Module1 is not reachable: Module1 has no live Service"
    )
    assert [write[0] for write in records.writes] == ["open", "fail"]
    assert logged(caplog, logging.WARNING) == [
        (
            f"WarehouseManagementSystem1: {N1} is on Module1: Module1 is not reachable: "
            "Module1 has no live Service"
        )
    ]


@pytest.mark.parametrize("stopping", [False, True], ids=["origin gone", "at a stop"])
def test_a_spawn_whose_create_the_store_admitted_is_recorded_as_spawned_though_its_origin_was_not_notified(
    caplog, wms, world, notifications, stop, stopping
):
    """A spawn counts at its answer: created once the store admitted its create. So the one record
    of a spawn whose origin cannot be notified is the record of the box spawned, beside the
    warning that the origin was not told; the answer still says why. (Added on 2026-10-07,
    jms_kapps 51, decided by Etienne: "A spawn counts at its answer: created when the store
    admitted its create".)"""
    caplog.set_level(logging.INFO, logger="jms_kapps")
    notifications.undelivered_to[M1] = "Module1 has no live Service"
    if stopping:

        def notify_at_the_stop(operation, resource):
            # The stop begins after the create, while the WMS notifies the origin.
            stop.begun = True
            raise NotificationUndelivered("connection refused")

        wms.notify = notify_at_the_stop

    result = spawn(wms)

    assert not result.accepted
    assert (result.box, result.operation) == (str(B1), str(T1))
    records_of_spawns = [
        line
        for line in logged(caplog, logging.INFO)
        if ": spawned " in line or ": spawn refused: " in line
    ]
    assert records_of_spawns == [
        f"WarehouseManagementSystem1: spawned {N1} onto Module1, for Module2"
    ]
    [warning] = logged(caplog, logging.WARNING)
    assert warning.startswith(f"WarehouseManagementSystem1: {N1} is on Module1: ")


def test_ctrl_c_between_the_create_and_the_notification_ends_the_transport_once_by_the_wms(
    caplog, wms, world, records, notifications, modules, stop
):
    """Ctrl+C reaches the WMS once it has created Box 1 on Module 1, and Module 1, whose server
    takes no new connection once its stop has begun, cannot be notified. The WMS ends the
    transport failed, once, and at its own stop Module 1 finds that transport ended and writes
    nothing (Etienne, 2026-10-06, jms_kapps 42); the stop as the WMS's reason is the build's
    reading."""
    opening = records.open

    def opened_then_ctrl_c(*args, **kwargs):
        operation = opening(*args, **kwargs)
        stop.begun = True
        return operation

    records.open = opened_then_ctrl_c
    notifications.undelivered_to[M1] = "Module1 has no live Service"

    result = spawn(wms)
    modules[M1].stop(within=0.0)

    assert (result.accepted, result.reason) == (False, "the system is stopping")
    assert world.possessor_of(B1) == M1
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "the system is stopping"
    assert [write[0] for write in records.writes] == ["open", "fail"]
    assert logged(caplog, logging.WARNING) == [
        (
            f"WarehouseManagementSystem1: {N1} is on Module1: the system is stopping; Module1 "
            "is not reachable: Module1 has no live Service"
        )
    ]
    assert logged(caplog, logging.WARNING, module_logger) == [
        f"Module1: {N1}_transport is failed, so it is left alone"
    ]


def test_the_origin_leaves_alone_a_transport_the_wms_ended_when_it_could_not_notify_it(
    caplog, wms, world, records, notifications, timers
):
    """At the bound of its reservation, the origin finds the box on it and its transport ended
    by the WMS: it frees its reservation, writes nothing and tells no one."""
    notifications.undelivered_to[M1] = "Module1 is busy"
    spawn(wms)
    del notifications.undelivered_to[M1]
    writes = list(records.writes)

    timers.fire()

    assert records.writes == writes
    assert world.jobs[T1].result == "Module1 is not reachable: Module1 is busy"
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING, module_logger) == [
        f"Module2: {N1} did not come from Module1 within 30 s, so it frees its reservation",
        f"Module1: {N1}_transport is failed, so it is left alone",
    ]


def test_a_box_cannot_start_and_end_on_the_same_module(wms, world, records):
    """Not even on a module that is an entry and an exit."""
    world.classes[FC.ExitModule].append(M1)

    result = spawn(wms, origin=M1, destination=M1)

    assert not result.accepted
    assert result.reason == "a box's origin and destination must differ"
    assert records.writes == []


def test_a_spawn_onto_a_module_that_never_registered_writes_nothing(
    wms, world, records
):
    world.unregistered_capabilities.add(M1)

    result = spawn(wms)

    assert not result.accepted
    assert result.reason == (
        "Module1 offers no BoxTransportCapability: it has never registered its "
        "BoxTransportWorkflow"
    )
    assert records.writes == []


def test_a_spawned_box_goes_over_one_hop_and_back_and_its_transport_is_done(
    caplog, wms, world, tasks, modules
):
    caplog.set_level(logging.INFO, logger="jms_kapps")

    spawn(wms)
    tasks.run()

    assert world.possessor_of(B1) == WMS
    assert world.jobs[T1].status == OperationStatus.DONE
    assert logged(caplog, logging.INFO)[-1] == (
        f"WarehouseManagementSystem1: accepted {N1} back, and {N1}_transport is done"
    )


def test_a_spawned_box_starts_its_journey_once(
    wms, world, network, records, notifications, tasks, modules
):
    """The WMS's notification starts the origin, and the WMS calls no Receive. Between the two
    modules, Module 2's Receive starts Module 2, and no notification passes."""
    spawn(wms)
    tasks.run()

    assert records.writes.count(("start", T1)) == 1
    assert network.calls == [
        ("ReserveWorkflow", M1),
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M2),
        ("ReceiveWorkflow", M2),
    ]
    assert notifications.sent == [(T1, M1), (T1, WMS)]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_the_wms_logs_a_transport_that_failed(caplog, wms, world):
    world.place(B1, M2, origin=M2, destination=M1, status=OperationStatus.FAILED)
    world.jobs[T1].result = "Module2 has no route to Module1"

    wms.transport_ended(world.transport(T1))

    assert logged(caplog, logging.WARNING) == [
        f"WarehouseManagementSystem1: {N1}_transport failed: Module2 has no route to Module1"
    ]


def test_a_failed_transport_costs_the_wms_no_read_of_its_own(wms, world):
    world.place(B1, M2, origin=M2, destination=M1, status=OperationStatus.FAILED)

    wms.transport_ended(world.transport(T1))

    assert [read for read in world.reads if read[0] == WMS] == []


def test_a_done_transport_costs_the_wms_one_ask_for_its_box(wms, world):
    """(Changed on 2026-10-12, jms_kapps 92, decided by Sören on jms_kapps 85: one `ASK` about the
    one box. Until then it cost one read of what the WMS holds, every box.)"""
    world.place(B1, WMS, origin=M1, destination=M2, status=OperationStatus.DONE)

    wms.transport_ended(world.transport(T1))

    assert [read for read in world.reads if read[0] == WMS] == [
        (WMS, "holds", (WMS, B1))
    ]


def test_the_wms_accepts_nothing_it_does_not_hold(caplog, wms, world):
    world.place(B1, M2, origin=M1, destination=M2, status=OperationStatus.DONE)

    wms.transport_ended(world.transport(T1))

    assert logged(caplog, logging.INFO) == []
    assert logged(caplog, logging.WARNING) == [
        (
            f"WarehouseManagementSystem1: notified of {N1}_transport, which is done, and it "
            f"does not hold {N1}"
        )
    ]


def test_the_uc2_ontology_has_no_convey_term():
    """A module's convey is its own move, and the WMS spawns a box directly on its origin, so no
    party exposes a Convey workflow, and the ontology names neither its workflow class nor its
    capability class (Etienne, 2026-10-06, jms_kapps 41)."""
    graph = Graph()
    for entry in resources.files("jms_kapps.uc2").joinpath("ontology").iterdir():
        if entry.name.endswith(".ttl"):
            graph.parse(data=entry.read_text(encoding="utf-8"), format="turtle")
    terms = {term for triple in graph for term in triple}

    assert len(graph) > 0
    assert URIRef(f"{FC_NS}ConveyWorkflow") not in terms
    assert URIRef(f"{FC_NS}ConveyCapability") not in terms


# -- Only a reachable exit (jms_kapps 51, decided by Etienne on 2026-10-07) ------------------- #

M4 = module_iri(4)


@pytest.fixture
def two_parts(world, network, records, notifications, moves, starts, timers, modules):
    """Two parts with no connection between them, each with an entry and an exit module: Module 1
    to Module 2, and Module 3 to Module 4."""
    module_4 = FlexConveyorModule(
        M4,
        graph=Reads(world, M4),
        handover=MiddlewareHandover(world, M4),
        call=network.call,
        records=records,
        notify=notifications.notify,
        move=moves.move,
        start=starts,
        later=timers.later,
    )
    network.peers[M4] = module_4
    notifications.receivers[M4] = module_4.box_transport
    world.connections = {(M1, M2), (M3, M4)}
    world.classes[FC.EntryModule] = [M1, M3]
    world.classes[FC.ExitModule] = [M2, M4]
    return world


def test_the_wms_picks_only_a_destination_its_origin_reaches(wms, two_parts, picks):
    """Module 3, the last free entry module, can reach Module 4 and not Module 2."""
    result = spawn(wms, origin=None, destination=None)

    assert result.accepted, result
    assert picks.made == [[M1, M3], [M4]]
    assert (result.origin, result.destination) == (str(M3), str(M4))


def test_for_a_named_origin_the_wms_picks_among_the_destinations_it_reaches(
    wms, two_parts, picks
):
    result = spawn(wms, origin=M1, destination=None)

    assert result.accepted, result
    assert picks.made == [[M2]]


def test_for_a_named_destination_the_wms_picks_among_the_free_origins_that_reach_it(
    wms, two_parts, picks
):
    result = spawn(wms, origin=None, destination=M2)

    assert result.accepted, result
    assert picks.made == [[M1]]


def test_a_named_destination_the_named_origin_cannot_reach_is_refused_before_anything_is_written(
    wms, two_parts, records, network
):
    result = spawn(wms, origin=M1, destination=M4)

    assert (result.accepted, result.reason) == (
        False,
        "Module4 cannot be reached from Module1",
    )
    assert (result.origin, result.destination) == (str(M1), str(M4))
    assert records.writes == []
    assert network.calls == []


@pytest.mark.parametrize(
    ("origin", "destination", "reason"),
    [
        (M1, None, "no exit module can be reached from Module1"),
        (None, M4, "no free entry module can reach Module4"),
        (None, None, "no free entry module can reach an exit module"),
    ],
    ids=["destination picked", "origin picked", "both picked"],
)
def test_where_no_pick_is_reachable_the_spawn_is_refused_with_why(
    wms, two_parts, records, network, origin, destination, reason
):
    """Module 1 leads nowhere, and Module 3, the other entry module, holds a box."""
    two_parts.connections = {(M3, M4)}
    two_parts.place(box_iri(7), M3)

    result = spawn(wms, origin=origin, destination=destination)

    assert (result.accepted, result.reason) == (False, reason)
    assert records.writes == []
    assert network.calls == []


def test_where_the_topology_names_no_roles_an_unreachable_pick_says_why_in_those_terms(
    wms, world, records
):
    no_roles(world, M1, M2, M3)
    world.connections = {(M2, M3)}

    reasons = [
        spawn(wms, origin=origin, destination=destination).reason
        for origin, destination in [(M1, None), (None, M1)]
    ]

    assert reasons == [
        "no module can be reached from Module1",
        "no free module can reach Module1",
    ]
    assert records.writes == []


def test_the_wms_reads_the_connections_with_the_modules_in_one_read(wms, two_parts):
    """A spawn makes no more store reads than before: the modules with their types, now with the
    connections; what each origin it may pick holds; the origin's capability. (Changed on
    2026-10-12, jms_kapps 92: the read of every box, which numbered the next box, went.)"""

    spawn(wms, origin=None, destination=None)

    assert [name for reader, name, _ in two_parts.reads if reader == WMS] == [
        "modules_and_connections",
        "boxes_held_by",
        "boxes_held_by",
        "capability",
    ]
