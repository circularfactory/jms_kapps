"""The handshake logic of one module, without a network or a triple store.

The module is given what it reads from the graph, what it writes, the middleware's handover and
notification, its move, and a way to call a peer. The fakes in `_station` stand in for all of
them, so every refusal can be driven directly, and the work a module starts as its own runs only
when the test runs it. That the real ones fit together is the live tests' job to prove.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

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
    capability_of,
    store_refusal,
)
from kapps_ogm import Node
from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import TripleStoreResponseError
from rdflib.namespace import RDF, SH, XSD

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import (
    ReceiveRequest,
    ReceiveResult,
    ReserveRequest,
)
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.transport import Transport, reason_and_report, transport_from
from jms_kapps.uc2.vocabulary import (
    FC,
    FCI_NS,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
)

M1, M2, M3, M4, M5 = (module_iri(n) for n in range(1, 6))
B1, B2, B3 = box_iri(1), box_iri(2), box_iri(3)
T1, T2 = transport_iri(B1), transport_iri(B2)

# How long a module waits before it asks a Reserve again, in these tests.
RETRY_SECONDS = 0.1

# How long a module that reserved itself waits for the box's Receive, in these tests: the bound of
# 30 s and a transit time of 3 s, as a run's module is given it.
ARRIVAL_BOUND_SECONDS = 33.0

# Two store errors that are no refusal, in the form GraphDB 10.8.5 answers them through the
# triplestore interface: a dataset that does not exist, and an update the store cannot parse.
UNKNOWN_REPOSITORY = "Error while querying GraphDB (404) - Unknown dataset: jms-kapps"
MALFORMED_UPDATE = (
    "Error while querying GraphDB (400) - MALFORMED QUERY: closing brace missing"
)

# What a call raises when the peer's process is gone and its port is closed.
REFUSED = httpx.ConnectError("[Errno 111] Connection refused")


def report(focus: IRI, shape: IRI, message: str) -> str:
    """A validation report in the form GraphDB 10.8.5 sends one: N-Quads, one result."""
    return (
        f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"
        f'_:report <{SH.conforms}> "false"^^<{XSD.boolean}> .\n'
        f"_:report <{SH.result}> _:result .\n"
        f"_:result <{RDF.type}> <{SH.ValidationResult}> .\n"
        f"_:result <{SH.focusNode}> <{focus}> .\n"
        f"_:result <{SH.sourceShape}> <{shape}> .\n"
        f'_:result <{SH.resultMessage}> "{message}" .\n'
    )


FIRST_REPORT = report(
    M2,
    FC.FlexConveyorModulePossessionShape,
    "A FlexConveyorModule may possess at most one Box at a time.",
)
SECOND_REPORT = report(B1, FC.RunningTransportBoxPossessedShape, "The second refusal.")


def recorded(world: World, transport: IRI) -> tuple[str, str | None]:
    """The reason and the report that `transport`'s record carries."""
    return reason_and_report(world.jobs[transport].result)


@pytest.fixture
def world():
    """Module 1 connected to Module 2, and the WMS."""
    world = World()
    world.connections = {(M1, M2)}
    world.wms_iri = WMS
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


@pytest.fixture
def waits():
    return Waits()


@pytest.fixture
def timers():
    return Timers()


@pytest.fixture
def stops():
    """Whether the stop of each module's process has begun, by module: none has."""
    return defaultdict(Stop)


@pytest.fixture
def parts(world, network, records, tasks, notifications, moves, waits, timers, stops):
    """What every module of a test is given, by name."""
    return {
        "world": world,
        "network": network,
        "records": records,
        "tasks": tasks,
        "notifications": notifications,
        "moves": moves,
        "waits": waits,
        "timers": timers,
        "stops": stops,
    }


def module(
    resource,
    *,
    world,
    network,
    records,
    tasks,
    notifications,
    moves,
    waits,
    timers,
    stops,
):
    built = FlexConveyorModule(
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
        events=HandoverEvents(resource, token=resource.fragment),
        stopping=stops[resource].has_begun,
    )
    network.peers[resource] = built
    notifications.receivers[resource] = built.box_transport
    return built


@pytest.fixture
def m1(parts):
    return module(M1, **parts)


@pytest.fixture
def m2(parts):
    return module(M2, **parts)


@pytest.fixture
def station(parts):
    """Builds the modules it is given, beside Module 1 and Module 2 if a test asks for them."""

    def build(*resources: IRI) -> list[FlexConveyorModule]:
        return [module(r, **parts) for r in resources]

    return build


def logged(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """What the modules logged at `level`, in order."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == module_logger.name and r.levelno == level
    ]


def running_on_module_1(world, box=B1):
    """`box` on Module 1, its transport running toward Module 2 and assigned to Module 1."""
    return world.transport(world.place(box, M1, destination=M2))


# -- The BoxTransportWorkflow ------------------------------------------------------------------ #


def test_the_box_transport_workflow_starts_a_queued_transport_and_carries_the_box_on(
    world, records, network, m1, m2
):
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)

    m1.box_transport(world.transport(T1))

    assert records.writes == [("start", T1)]
    assert network.calls == [("ReserveWorkflow", M2), ("ReceiveWorkflow", M2)]


def test_the_box_transport_workflow_carries_a_running_transport_on(
    world, records, network, m1, m2
):
    m1.box_transport(running_on_module_1(world))

    assert records.writes == []
    assert network.calls == [("ReserveWorkflow", M2), ("ReceiveWorkflow", M2)]


def test_a_transport_assigned_to_another_module_is_left_alone(
    caplog, world, records, network, m1, m2
):
    world.place(B1, M2, origin=M1, destination=M2)

    m1.box_transport(world.transport(T1))

    assert (records.writes, network.calls) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        "Module1: Box1_transport is assigned to Module2, so it is left alone"
    ]


def test_a_transport_whose_box_the_module_does_not_hold_fails_at_the_handover(
    world, tasks, notifications, m1, m2
):
    """The module acts on the transport it is handed: the assignment says it carries the box on.
    The handover checks the possession before it commits, so a box held elsewhere is refused
    there, before anything moves, and the transport fails."""
    transport = running_on_module_1(world)
    world.held[M1].remove(B1)
    world.held[WMS].append(B1)

    m1.box_transport(transport)
    tasks.run()

    assert world.possessor_of(B1) == WMS
    assert world.jobs[T1].assigned == M1
    assert world.jobs[T1].status == OperationStatus.FAILED
    # The middleware's precondition says so, in its own words.
    assert world.jobs[T1].result == f"{M1} does not currently possess {B1}"
    assert notifications.sent == [(T1, WMS)]


def test_a_queued_transport_whose_box_the_module_does_not_hold_stays_queued(
    caplog, world, records, notifications, m1, m2
):
    """The store refuses `running` while no module holds the box. The WMS creates each box on
    its origin, so this happens only to a box that is elsewhere, here the WMS's: the module
    writes nothing, notifies no one, and says why."""
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)
    world.held[M1].remove(B1)
    world.held[WMS].append(B1)
    records.start_fails_with = store_refusal(SECOND_REPORT)

    m1.box_transport(world.transport(T1))

    assert records.writes == []
    assert world.jobs[T1].status == OperationStatus.QUEUED
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: the triple store refused running Box1_transport, by "
            "RunningTransportBoxPossessedShape at Box1, so it stays queued: Box1 is not on this "
            f"module:\n{SECOND_REPORT}"
        )
    ]


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("the store is down"),
        TripleStoreResponseError(
            f"Error while querying GraphDB (500) - {SECOND_REPORT}"
        ),
    ],
    ids=["no store error", "a report in the text only"],
)
def test_a_refused_start_that_is_no_refusal_by_the_store_raises(
    world, records, m1, m2, error
):
    """Only a `ShaclValidationError` is the store's refusal, whatever another error's text says."""
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)
    records.start_fails_with = error

    with pytest.raises(type(error)) as raised:
        m1.box_transport(world.transport(T1))

    assert raised.value is error


@pytest.mark.parametrize("status", [OperationStatus.DONE, OperationStatus.FAILED])
def test_a_transport_that_ended_is_left_alone(
    caplog, world, records, network, m1, m2, status
):
    """A failed transport stays failed: trying again is not built."""
    world.place(B1, M1, destination=M2, status=status)

    m1.box_transport(world.transport(T1))

    assert (records.writes, network.calls) == ([], [])
    assert world.jobs[T1].status == status
    assert logged(caplog, logging.WARNING) == [
        f"Module1: Box1_transport is {status}, so it is left alone"
    ]


# -- One hop ----------------------------------------------------------------------------------- #


def test_a_hop_is_two_calls_and_one_commit_and_no_notification_between_the_modules(
    world, network, notifications, tasks, m1, m2
):
    """Module 1 asks Module 2's Reserve, commits the handover, conveys the box, its own move, and
    calls Module 2's Receive, the light barrier. Module 2 calls nothing of Module 1, and Module 1
    notifies Module 2 of nothing: Module 2, the destination here, carries the box on from its
    Receive, and gives it back."""
    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert network.calls == [("ReserveWorkflow", M2), ("ReceiveWorkflow", M2)]
    assert [counterpart for counterpart, _, _ in m1.handover.calls] == [M2]
    assert notifications.sent == [(T1, WMS)]
    assert world.possessor_of(B1) == WMS
    assert world.jobs[T1].status == OperationStatus.DONE


def test_no_module_answers_a_convey(m2):
    """Convey is the giving module's own move, a local action: no module answers it."""
    assert not hasattr(m2, "convey")


def test_the_handover_passes_the_box_and_the_transport_in_one_entry_commit(
    world, tasks, m1, m2
):
    """The possession and the assignment change together: executedOn and implementsCapability
    name the next module in the handover's entry."""
    m1.box_transport(running_on_module_1(world))
    tasks.run()

    (counterpart, completes, entry) = m1.handover.calls[0]
    assert (counterpart, completes) == (M2, None)
    assert [(change.instance_iri, change.data) for change in entry] == [
        (
            T1,
            {
                str(CFC.executedOn): [{"id": str(M2)}],
                str(CFC.implementsCapability): [{"id": str(capability_of(M2))}],
            },
        )
    ]


def test_after_its_receive_the_next_module_reads_the_box_with_its_transport_and_goes_on(
    world, tasks, m1, m2
):
    """Module 2 answers Receive first, and then, as its own work, reads the box with its
    transport, through the box's link to it: the box is not named after the transport."""
    transport = IRI("Transport7", base=FCI_NS)
    world.place(B1, M1, destination=M2, operation=transport)
    m1.box_transport(world.transport(transport))
    reads_before_receive = list(world.reads)

    tasks.run()

    assert (M2, "transport_of", (B1,)) not in reads_before_receive
    assert (M2, "transport_of", (B1,)) in world.reads
    assert world.jobs[transport].status == OperationStatus.DONE


def test_the_possession_changes_before_the_box_moves(world, tasks, moves, m1, m2):
    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert moves.moved[0] == (B1, M2, M2)


def test_the_receiving_module_logs_each_step_it_accepts(caplog, world, tasks, m1, m2):
    caplog.set_level(logging.INFO, logger="jms_kapps")

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    accepted = [
        line for line in logged(caplog, logging.INFO) if line.startswith("Module2: ")
    ]
    assert accepted[:2] == [
        "Module2: ReserveWorkflow accepted Box1 from Module1",
        "Module2: ReceiveWorkflow accepted Box1 from Module1",
    ]


def test_the_giving_module_logs_its_side_of_each_step(caplog, world, tasks, m1, m2):
    """The Reserve it asked and its answer, and its convey once the box has gone and Module 2's
    Receive was called: so the giving module's own log shows the hop too, as the receiving
    module's does."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert [
        line for line in logged(caplog, logging.INFO) if line.startswith("Module1: ")
    ] == [
        "Module1: Module2's ReserveWorkflow accepted Box1",
        "Module1: conveyed Box1 to Module2",
    ]


def test_a_module_that_hands_over_without_asking_logs_it(caplog, world, m1, m2):
    caplog.set_level(logging.INFO, logger="jms_kapps")
    m1.faults["skip-reserve"] = True

    m1.box_transport(running_on_module_1(world))

    assert [
        line for line in logged(caplog, logging.INFO) if line.startswith("Module1: ")
    ] == [
        (
            "Module1: handed Box1 to Module2 without asking its ReserveWorkflow, as "
            "skip-reserve is armed"
        ),
    ]


def test_an_occupied_module_refuses_to_reserve(world, m2):
    world.place(B2, M2)

    result = m2.reserve(ReserveRequest(box=B1, source_module=M1))

    assert not result.accepted
    assert result.reason == "occupied by Box2"
    assert result.refusal == "occupied"


def test_a_reserved_module_refuses_a_second_box(m2):
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted

    result = m2.reserve(ReserveRequest(box=B2, source_module=M1))

    assert not result.accepted
    assert result.reason == "reserved for Box1"
    assert result.refusal == "reserved"


def test_a_reserve_accepted_carries_no_refusal(m2):
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).refusal is None


class HeldReadThatWaits(Reads):
    """Module 2's reads, whose first read of the boxes it holds waits until the test lets it go:
    the window between a Reserve's read of the store and its answer."""

    def __init__(self, world: World, reader: IRI) -> None:
        super().__init__(world, reader)
        self.reading, self.go_on = threading.Event(), threading.Event()

    def boxes_held_by(self, resource: IRI) -> list[IRI]:
        held = super().boxes_held_by(resource)
        if not self.reading.is_set():
            self.reading.set()
            assert self.go_on.wait(5)
        return held


def in_thread(work) -> tuple[threading.Thread, list]:
    answers: list = []
    thread = threading.Thread(target=lambda: answers.append(work()), daemon=True)
    thread.start()
    return thread, answers


def test_two_reserves_that_arrive_together_reserve_the_module_for_one_box_only(
    world, m2
):
    m2.graph = slow = HeldReadThatWaits(world, M2)
    first, first_answer = in_thread(
        lambda: m2.reserve(ReserveRequest(box=B1, source_module=M1))
    )
    assert slow.reading.wait(5)
    second, second_answer = in_thread(
        lambda: m2.reserve(ReserveRequest(box=B2, source_module=M3))
    )

    slow.go_on.set()
    first.join(5)
    second.join(5)

    assert [a.accepted for a in first_answer + second_answer] == [True, False]
    assert second_answer[0].refusal == "reserved"


def test_a_reserve_is_not_accepted_because_an_arrival_was_confirmed_while_it_read(
    world, m2
):
    """Box 1 arrives on Module 2 after a Reserve for Box 2 has read that Module 2 holds nothing.
    The Receive that confirms the arrival waits for that Reserve, which still sees the
    reservation for Box 1, and refuses."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    m2.graph = slow = HeldReadThatWaits(world, M2)
    reserving, answer = in_thread(
        lambda: m2.reserve(ReserveRequest(box=B2, source_module=M3))
    )
    assert slow.reading.wait(5)
    world.held[M2].append(B1)
    receiving, _ = in_thread(
        lambda: m2.receive(ReceiveRequest(box=B1, source_module=M1))
    )

    slow.go_on.set()
    reserving.join(5)
    receiving.join(5)

    assert not answer[0].accepted
    assert answer[0].refusal == "reserved"


def test_a_step_the_module_refuses_is_not_logged_as_accepted(caplog, world, m2):
    caplog.set_level(logging.INFO, logger="jms_kapps")
    world.place(B2, M2)

    m2.reserve(ReserveRequest(box=B1, source_module=M1))

    assert logged(caplog, logging.INFO) == []


def test_a_receive_from_a_module_answers_first_and_then_carries_the_box_on(
    world, tasks, records, m2
):
    """It never refuses, because the possession has changed already. Carrying the box on is
    the module's own work, after it has answered."""
    world.place(B1, M2, origin=M1, destination=M2)

    result = m2.receive(ReceiveRequest(box=B1, source_module=M1))

    assert result.accepted
    assert world.jobs[T1].status == OperationStatus.RUNNING
    tasks.run()
    assert world.jobs[T1].status == OperationStatus.DONE


# -- A reservation for the WMS's box ------------------------------------------------------------ #

# The bound of a reservation for the WMS's box: no move comes between the answer to Reserve and the
# notification, so no transit time is added.
SPAWN_BOUND_SECONDS = 30.0


def test_a_reservation_for_the_wms_lapses_30_s_after_its_answer(timers, tasks, m2):
    """The WMS's Reserve sets a bound as a module's does, without the transit time, and starts
    nothing: the origin calls nothing of the WMS."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted

    assert timers.seconds == [SPAWN_BOUND_SECONDS]
    assert tasks.pending == []


def test_the_wms_s_notification_uses_the_reservation_for_its_box_up(
    caplog, world, records, network, notifications, timers, tasks, m1, m2
):
    """The WMS created Box 1 on Module 1, the origin, and notified it: Module 1 carries the box on
    as after a Receive, and its reservation is gone, so its bound reads and writes nothing."""
    assert m1.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)

    m1.box_transport(world.transport(T1))
    tasks.run()
    reads, writes = list(world.reads), list(records.writes)
    timers.fire()

    assert world.jobs[T1].status == OperationStatus.DONE
    assert (world.reads, records.writes) == (reads, writes)
    assert logged(caplog, logging.WARNING) == []


def test_a_reservation_for_the_wms_whose_box_never_comes_is_freed_at_its_bound(
    caplog, world, timers, records, notifications, m2
):
    """The WMS's create was refused, or its answer from Reserve was lost: no box comes and no
    notification. Module 2 frees its reservation, writes nothing, and says so."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted
    assert m2.reserve(ReserveRequest(box=B2, source_module=M3)).refusal == "reserved"

    timers.fire()

    assert m2.reserve(ReserveRequest(box=B2, source_module=M3)).accepted
    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: Box1 did not come from WarehouseManagementSystem1 within 30 s, so it "
            "frees its reservation"
        )
    ]


@pytest.mark.parametrize("status", [OperationStatus.DONE, OperationStatus.FAILED])
def test_at_its_bound_the_origin_leaves_alone_a_transport_the_wms_ended(
    caplog, world, timers, records, notifications, m2, status
):
    """The WMS created Box 1 on Module 2 and could not notify it, so it ended the transport
    itself: Module 2 frees its reservation and leaves the transport alone."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted
    world.place(B1, M2, destination=M1, status=status)

    timers.fire()

    assert world.jobs[T1].status == status
    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        f"Module2: Box1_transport is {status}, so it is left alone"
    ]
    assert m2.memory.held() == B1


def test_at_its_bound_the_origin_leaves_a_transport_it_was_not_notified_of_to_the_wms(
    caplog, world, timers, records, notifications, m2
):
    """The WMS created Box 1 on Module 2 and was lost before it notified Module 2: the WMS owns
    the station's entry, so Module 2 frees its reservation, writes nothing, and says why."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted
    world.place(B1, M2, destination=M1, status=OperationStatus.QUEUED)

    timers.fire()

    assert world.jobs[T1].status == OperationStatus.QUEUED
    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: WarehouseManagementSystem1 did not notify it of Box1_transport within "
            "30 s, so it frees its reservation and leaves the transport to "
            "WarehouseManagementSystem1"
        )
    ]
    assert m2.memory.held() == B1


# -- The bound of a reservation ----------------------------------------------------------------- #


def test_a_reservation_s_bound_is_set_when_reserve_answers(timers, m2):
    """30 s and the run's transit time, counted from the answer to Reserve."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted

    assert timers.seconds == [ARRIVAL_BOUND_SECONDS]


def committed_onto_module_2(world, box=B1, operation=None):
    """What Module 1's handover commits: `box` and its transport are Module 2's."""
    operation = operation or transport_iri(box)
    world.held[M1].remove(box)
    world.held[M2].append(box)
    world.jobs[operation].assigned = M2


def test_a_giver_lost_after_its_commit_leaves_the_transport_to_the_receiver_at_the_bound(
    caplog, world, timers, notifications, m2
):
    """Module 1 committed the handover, so Box 1 and its transport are Module 2's, and then its
    process ended during the move, as a crash does: no Receive comes. At the bound, Module 2,
    now the assigned module, finds the transport through the box, ends it failed with a reason
    that names Module 1, and tells the WMS. The transport's IRI does not follow the box's name,
    so no naming rule finds it."""
    transport = IRI("Transport7", base=FCI_NS)
    world.place(B1, M1, destination=M2, operation=transport)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world, operation=transport)

    timers.fire()

    reason = "Module1 is lost: it did not call ReceiveWorkflow for Box1 within 33 s"
    assert world.jobs[transport].status == OperationStatus.FAILED
    assert world.jobs[transport].result == reason
    assert notifications.sent == [(transport, WMS)]
    assert (M2, "transport_of", (B1,)) in world.reads
    assert logged(caplog, logging.WARNING) == [f"Module2: Box1 is here: {reason}"]
    assert m2.memory.held() == B1


def test_a_reservation_whose_box_never_comes_is_freed_at_the_bound(
    caplog, world, timers, records, notifications, m2
):
    """Module 1 went away before its commit: the box and its transport are still Module 1's,
    and not Module 2's to end. Module 2 frees its reservation, and says so."""
    running_on_module_1(world)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    assert m2.reserve(ReserveRequest(box=B2, source_module=M3)).refusal == "reserved"

    timers.fire()

    assert m2.reserve(ReserveRequest(box=B2, source_module=M3)).accepted
    assert (records.writes, notifications.sent) == ([], [])
    assert world.jobs[T1].status == OperationStatus.RUNNING
    assert logged(caplog, logging.WARNING) == [
        "Module2: Box1 did not come from Module1 within 33 s, so it frees its reservation"
    ]


@pytest.mark.parametrize("status", [OperationStatus.DONE, OperationStatus.FAILED])
def test_a_transport_that_ended_is_left_alone_at_the_bound(
    caplog, world, timers, records, notifications, m2, status
):
    world.place(B1, M1, destination=M2, status=status)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world)

    timers.fire()

    assert world.jobs[T1].status == status
    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        f"Module2: Box1_transport is {status}, so it is left alone"
    ]


def test_a_box_that_arrived_leaves_nothing_to_its_bound(
    caplog, world, timers, tasks, records, m1, m2
):
    """Module 2's Receive released the reservation, and Module 2 has carried the box on: at the
    bound it reads nothing, writes nothing, and says nothing."""
    m1.box_transport(running_on_module_1(world))
    tasks.run()
    reads, writes = list(world.reads), list(records.writes)

    timers.fire()

    assert (world.reads, records.writes) == (reads, writes)
    assert logged(caplog, logging.WARNING) == []


def test_the_bound_of_an_older_reservation_leaves_a_newer_one_alone(world, timers, m2):
    """Box 1's Receive came, and Module 2 has reserved itself for Box 2 since: the bound of the
    reservation for Box 1 frees nothing."""
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    world.held[M2].append(B1)
    m2.receive(ReceiveRequest(box=B1, source_module=M1))
    world.held[M2].remove(B1)
    assert m2.reserve(ReserveRequest(box=B2, source_module=M3)).accepted
    first_bound = timers.pending.pop(0)[1]

    first_bound()

    assert m2.reserve(ReserveRequest(box=B3, source_module=M1)).refusal == "reserved"


def test_a_receive_that_comes_after_the_bound_ended_the_transport_leaves_it_alone(
    caplog, world, timers, tasks, network, m2
):
    """The box came, and its giver's Receive later than the bound: the transport has failed,
    and Module 2 does not carry the box on."""
    world.place(B1, M1, destination=M3)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world)
    timers.fire()

    assert m2.receive(ReceiveRequest(box=B1, source_module=M1)).accepted
    tasks.run()

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert network.calls == []
    assert logged(caplog, logging.WARNING)[-1] == (
        "Module2: Box1_transport is failed, so it is left alone"
    )


def test_a_lost_giver_s_box_that_links_to_no_transport_is_only_logged(
    caplog, world, timers, notifications, records, m2
):
    """The graph's rules let no box sit on a module without a transport, but a module does not
    rely on them: if the box it holds links to no transport, it writes nothing, notifies no one,
    and says why in one warning."""
    running_on_module_1(world)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world)
    del world.jobs[T1]

    timers.fire()

    assert records.writes == []
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: Box1 is here, and the graph names no transport of it: Module1 is lost: "
            "it did not call ReceiveWorkflow for Box1 within 33 s"
        )
    ]


def test_a_box_that_arrives_with_no_transport_is_only_logged(
    caplog, world, tasks, records, notifications, m2
):
    world.held[M2].append(B1)

    m2.receive(ReceiveRequest(box=B1, source_module=M1))
    tasks.run()

    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        "Module2: Box1 arrived from Module1, and the graph names no transport of it"
    ]


# -- A receiver lost after the commit ------------------------------------------------------------ #


def receive_not_reachable(world, moves, m1):
    """Module 1 carries Box 1 on, and Module 2 has reserved itself; then, during the move, Module
    2's workflows leave the graph, so Module 1 finds no Receive to call after the move."""
    moves.meanwhile.append(lambda: world.unregistered.add(M2))
    m1.box_transport(running_on_module_1(world))


def test_a_giver_whose_receive_is_not_reachable_after_its_commit_writes_nothing(
    caplog, world, records, moves, notifications, timers, m1, m2
):
    """The possession has changed and the box has moved when Module 2's Receive cannot be
    reached. Nothing is undone: the box is on Module 2, and the transport is assigned to it. So
    Module 1 writes nothing and notifies no one, and says why in one warning: only the assigned
    module writes the transport. Module 2, the box's
    possessor, ends it at the bound of its reservation."""
    receive_not_reachable(world, moves, m1)

    assert world.possessor_of(B1) == M2
    assert world.jobs[T1].assigned == M2
    assert world.jobs[T1].status == OperationStatus.RUNNING
    assert ("fail", T1) not in [write[:2] for write in records.writes]
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: Box1 is on Module2, so Box1_transport is Module2's to end: Module2 is "
            "not reachable: the knowledge graph names no live ReceiveWorkflow of it"
        )
    ]
    world.unregistered.discard(M2)
    timers.fire()
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == (
        "Module1 is lost: it did not call ReceiveWorkflow for Box1 within 33 s"
    )
    assert notifications.sent == [(T1, WMS)]


def test_a_receive_that_does_not_confirm_the_arrival_is_logged_and_undoes_nothing(
    caplog, world, tasks, timers, m1, m2
):
    """Module 2's Receive answers without confirming the arrival, so it has carried nothing on.
    Module 1 goes on: the possession has changed. Module 2's bound then finds the box its own
    with the transport running, and ends it."""
    m2.receive = lambda request: ReceiveResult(accepted=False, reason="not confirmed")

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert (
        "Module1: Module2 did not confirm the arrival of Box1: not confirmed"
        in logged(caplog, logging.WARNING)
    )
    assert world.possessor_of(B1) == M2
    timers.fire()
    assert world.jobs[T1].status == OperationStatus.FAILED


def test_a_module_whose_next_module_has_no_live_service_cannot_go_on_and_does_not_wait(
    world, notifications, waits, m1, m2
):
    """Module 2 deregistered, so the graph names no live Reserve of it: it is not reachable, and
    Module 1 has no other way to it. A next module that cannot be reached is left out, and the
    transport fails only with no live route left."""
    world.unregistered.add(M2)

    m1.box_transport(running_on_module_1(world))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "no live route to Module2: Module2 is not reachable"
    assert world.possessor_of(B1) == M1
    assert notifications.sent == [(T1, WMS)]
    assert waits.waited == []


def test_a_box_over_a_free_path_makes_the_module_wait_for_no_positive_time(
    world, waits, m1, m2
):
    """With a transit time of 0 the module conveys at once: a free path costs no wait (the real simulated move at 0 s calls no sleep either, in `test_uc2_transit_time`)."""
    m1.box_transport(running_on_module_1(world))

    assert world.possessor_of(B1) == M2
    assert [seconds for seconds in waits.waited if seconds > 0] == []


# -- The stop ----------------------------------------------------------------------------------- #

# The reason of a transport that a stop ends.
STOPPING = "the system is stopping"


def test_a_stopping_receiver_ends_the_transport_of_a_box_committed_to_it_without_a_receive(
    caplog, world, records, notifications, stops, m2
):
    """Module 1 committed the handover of Box 1 onto Module 2, and no Receive came before the
    bound of Module 2's stop: the box is Module 2's and its transport is assigned to it, so
    Module 2 ends the transport failed, "the system is stopping", and tells the WMS."""
    world.place(B1, M1, destination=M3)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world)
    stops[M2].begun = True

    m2.stop(within=0.0)

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == STOPPING
    assert [w for w in records.writes if w[0] == "fail"] == [
        ("fail", T1, STOPPING, None)
    ]
    assert notifications.sent == [(T1, WMS)]
    assert logged(caplog, logging.WARNING) == [f"Module2: Box1 is here: {STOPPING}"]
    assert world.possessor_of(B1) == M2


def test_a_stopping_receiver_whose_box_was_not_committed_to_it_frees_itself_and_writes_nothing(
    caplog, world, records, notifications, stops, m2
):
    """Module 1 has not committed the handover of Box 1: the box and its transport are still
    Module 1's, and not Module 2's to end."""
    running_on_module_1(world)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    stops[M2].begun = True

    m2.stop(within=0.0)

    assert (records.writes, notifications.sent) == ([], [])
    assert world.jobs[T1].status == OperationStatus.RUNNING
    assert logged(caplog, logging.WARNING) == [
        "Module2: Box1 did not come from Module1 before the stop, so it frees its reservation"
    ]


@pytest.mark.parametrize("status", [OperationStatus.QUEUED, OperationStatus.FAILED])
def test_a_stopping_origin_leaves_the_transport_of_the_wms_s_box_to_the_wms(
    caplog, world, records, notifications, stops, m1, status
):
    """The WMS created Box 1 on Module 1, the origin, and its notification had not come when
    Module 1's stop began: the WMS owns the station's entry and ends the transport itself when it
    cannot notify the origin, so Module 1 writes nothing, whether the WMS has ended it yet or
    not."""
    assert m1.reserve(ReserveRequest(box=B1, source_module=WMS)).accepted
    world.place(B1, M1, destination=M2, status=status)
    stops[M1].begun = True

    m1.stop(within=0.0)

    assert world.jobs[T1].status == status
    assert (records.writes, notifications.sent) == ([], [])
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: WarehouseManagementSystem1 did not notify it of Box1_transport before "
            "the stop, so it frees its reservation and leaves the transport to "
            "WarehouseManagementSystem1"
        )
        if status == OperationStatus.QUEUED
        else f"Module1: Box1_transport is {status}, so it is left alone"
    ]


def test_a_stopping_module_with_nothing_reserved_and_no_work_reads_and_writes_nothing(
    world, records, stops, m2
):
    stops[M2].begun = True

    m2.stop(within=5.0)

    assert (world.reads, records.writes) == ([], [])


def test_a_receive_under_way_at_the_stop_uses_the_reservation_up_and_the_box_goes_on(
    world, records, tasks, stops, m2
):
    """Module 2's stop waits for the Receive of the box it reserved itself for, and returns once
    the box it carries on has gone: it writes nothing of its own. Module 2 is the destination,
    so it gives the box back."""
    world.place(B1, M1, destination=M2)
    assert m2.reserve(ReserveRequest(box=B1, source_module=M1)).accepted
    committed_onto_module_2(world)
    stops[M2].begun = True
    stopping, _ = in_thread(lambda: m2.stop(within=30.0))

    m2.receive(ReceiveRequest(box=B1, source_module=M1))
    stopping.join(0.3)
    assert stopping.is_alive(), "the stop did not wait for the box's carrying on"
    tasks.run()

    stopping.join(5)
    assert not stopping.is_alive()
    assert world.jobs[T1].status == OperationStatus.DONE
    assert [w[0] for w in records.writes] == []


def test_a_receive_whose_carrying_on_cannot_start_leaves_no_work_to_its_stop(
    world, stops, m2
):
    """Module 2 answered a Receive and could not start the work that carries the box on: its
    stop has no work to wait for."""

    def no_thread(work) -> None:
        raise RuntimeError("can't start new thread")

    m2.start = no_thread
    world.place(B1, M2, origin=M1, destination=M2)
    with pytest.raises(RuntimeError):
        m2.receive(ReceiveRequest(box=B1, source_module=M1))
    stops[M2].begun = True
    stopping, _ = in_thread(lambda: m2.stop(within=30.0))

    stopping.join(5)
    assert not stopping.is_alive(), "the stop waits for work that never started"


def test_ctrl_c_during_a_move_ends_the_transport_once_by_the_module_it_is_assigned_to(
    caplog, world, network, records, moves, notifications, stops, m1, m2
):
    """Ctrl+C reaches both modules while Module 1 moves Box 1 onto Module 2, after its commit.
    Module 1's stop waits for its move, and Module 1 then calls Module 2's Receive, which is
    refused: Module 2's server takes no new connection once its stop has begun. Module 1 writes
    nothing. At its stop, Module 2 finds the box its own, and ends the transport failed, "the
    system is stopping", once."""
    world.connections.add((M2, M3))
    giver_stopped: list[threading.Thread] = []

    def ctrl_c() -> None:
        stops[M1].begun = stops[M2].begun = True
        network.fails_with[M2] = REFUSED
        stopping, _ = in_thread(lambda: m1.stop(within=30.0))
        stopping.join(0.3)
        assert stopping.is_alive(), "Module 1's stop did not wait for its move"
        giver_stopped.append(stopping)

    moves.meanwhile.append(ctrl_c)
    m1.box_transport(running_on_module_1(world))
    giver_stopped[0].join(5)
    assert not giver_stopped[0].is_alive()
    m2.stop(within=0.0)

    assert ("ReceiveWorkflow", M2) in network.calls
    assert world.possessor_of(B1) == M2
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == STOPPING
    assert [w for w in records.writes if w[0] == "fail"] == [
        ("fail", T1, STOPPING, None)
    ]
    assert notifications.sent == [(T1, WMS)]
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: Box1 is on Module2, so Box1_transport is Module2's to end: Module2 is "
            "not reachable: [Errno 111] Connection refused"
        ),
        f"Module2: Box1 is here: {STOPPING}",
    ]


def test_a_stopping_module_asks_no_reserve_and_ends_the_transport_it_holds(
    caplog, world, network, notifications, stops, m1, m2
):
    """Module 1 holds Box 1, and its stop has begun: it starts no hop. It keeps the box, and,
    as the assigned module, ends the transport failed, "the system is stopping"."""
    stops[M1].begun = True

    m1.box_transport(running_on_module_1(world))

    assert network.calls == []
    assert world.possessor_of(B1) == M1
    assert recorded(world, T1) == (STOPPING, None)
    assert notifications.sent == [(T1, WMS)]
    assert logged(caplog, logging.WARNING) == [f"Module1: Box1 stays here: {STOPPING}"]


def test_a_module_that_waits_its_turn_asks_no_more_once_its_stop_has_begun(
    world, network, waits, stops, m1, m2
):
    world.held[M2].append(B2)
    waits.meanwhile.append(lambda: setattr(stops[M1], "begun", True))

    m1.box_transport(running_on_module_1(world))

    assert network.calls == [("ReserveWorkflow", M2)]
    assert waits.waited == [RETRY_SECONDS]
    assert world.possessor_of(B1) == M1
    assert recorded(world, T1) == (STOPPING, None)


@pytest.mark.parametrize(
    "skip_reserve", [False, True], ids=["reserved", "skip-reserve"]
)
def test_a_stop_that_begins_before_the_commit_commits_no_handover(
    world, network, stops, m1, m2, skip_reserve
):
    """The stop of Module 1 begins once Module 2 has answered its Reserve, or, with
    `skip-reserve` armed, before it hands the box over without asking: it commits nothing,
    keeps the box, and ends the transport. Module 2's reservation, if it made one, lapses."""
    m1.faults["skip-reserve"] = skip_reserve
    reserve = m2.reserve

    def answered_then_stop(request: ReserveRequest):
        answer = reserve(request)
        stops[M1].begun = True
        return answer

    m2.reserve = answered_then_stop
    if skip_reserve:
        stops[M1].begun = True

    m1.box_transport(running_on_module_1(world))

    assert m1.handover.calls == []
    assert world.possessor_of(B1) == M1
    assert recorded(world, T1) == (STOPPING, None)
    assert network.calls == ([] if skip_reserve else [("ReserveWorkflow", M2)])


def test_a_stopping_module_refuses_to_reserve_itself_and_sets_no_bound(
    timers, stops, m2
):
    stops[M2].begun = True

    answer = m2.reserve(ReserveRequest(box=B1, source_module=M1))

    assert (answer.accepted, answer.reason, answer.refusal) == (False, STOPPING, None)
    assert timers.seconds == []


def test_a_giver_whose_receiver_is_stopping_ends_its_transport_with_the_receiver_s_answer(
    world, network, stops, m1, m2
):
    """Module 2's stop has begun, and a Reserve already under way reaches it: it refuses, and
    Module 1 keeps the box and ends the transport, as for any refusal that is not `occupied` or
    `reserved`."""
    stops[M2].begun = True

    m1.box_transport(running_on_module_1(world))

    assert world.possessor_of(B1) == M1
    assert recorded(world, T1) == (f"Module2 refused to reserve: {STOPPING}", None)


# -- A next module that does not answer -------------------------------------------------------- #


def test_a_module_whose_next_module_refuses_the_call_fails_the_transport_with_why(
    caplog, world, network, notifications, waits, m1
):
    """Module 2's process is gone, so the call of its Reserve is refused, and Module 1 has no
    other way on. Module 1 leaves Module 2 out, keeps the box, ends the transport failed with
    no live route left, and tells the WMS: one warning that says why Module 2 is left out, one
    that the box stays, nothing raised. A next module that cannot be reached is left out, and the
    transport fails only with no live route left."""
    network.fails_with[M2] = REFUSED

    m1.box_transport(running_on_module_1(world))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "no live route to Module2: Module2 is not reachable"
    assert world.possessor_of(B1) == M1
    assert notifications.sent == [(T1, WMS)]
    assert waits.waited == []
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: leaves Module2 out for Box1: Module2 is not reachable: [Errno 111] Connection "
            "refused"
        ),
        "Module1: Box1 stays here: no live route to Module2: Module2 is not reachable",
    ]


def test_a_module_waiting_for_a_next_module_that_goes_away_stops_waiting_and_fails(
    world, network, notifications, waits, m1, m2
):
    """Module 2 is occupied, so Module 1 waits its retry time, and meanwhile Module 2's process
    goes, as at a stop. The next ask is refused: Module 1 leaves Module 2 out, has no other way
    on, and the transport fails. A next module that cannot be reached is left out, and the transport
    fails only with no live route left."""
    world.held[M2].append(B2)
    waits.meanwhile.append(lambda: network.fails_with.update({M2: REFUSED}))

    m1.box_transport(running_on_module_1(world))

    assert waits.waited == [RETRY_SECONDS]
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "no live route to Module2: Module2 is not reachable"
    assert notifications.sent == [(T1, WMS)]


def test_when_the_wms_cannot_be_told_either_the_module_logs_one_warning_that_says_both(
    caplog, world, network, notifications, m1
):
    """At a stop the WMS has gone too. The module ends the transport, and says why and that the
    WMS was not told in one line, the use case's IRIs by their names. The module first leaves Module 2 out, in a warning of its
    own, and the reason is that no live route is left."""
    network.fails_with[M2] = REFUSED
    notifications.undelivered_to[WMS] = NotificationUndelivered(
        f"{WMS} has no live Service to notify of {T1}"
    )

    m1.box_transport(running_on_module_1(world))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: leaves Module2 out for Box1: Module2 is not reachable: [Errno 111] Connection "
            "refused"
        ),
        (
            "Module1: Box1 stays here: no live route to Module2: Module2 is not reachable; "
            "WarehouseManagementSystem1 was not told: WarehouseManagementSystem1 has no live "
            "Service to notify of Box1_transport"
        ),
    ]


def server_error() -> httpx.HTTPStatusError:
    """What the real call raises when the next module answers its Reserve with status 500."""
    request = httpx.Request("POST", "http://127.0.0.1:40000/reserve")
    try:
        httpx.Response(500, request=request).raise_for_status()
    except httpx.HTTPStatusError as error:
        return error
    raise AssertionError("status 500 raised nothing")


@pytest.mark.parametrize(
    ("error", "why"),
    [
        (httpx.ReadTimeout("timed out"), "timed out"),
        (httpx.ReadTimeout(""), "ReadTimeout"),
        (
            server_error(),
            (
                "Server error '500 Internal Server Error' for url "
                "'http://127.0.0.1:40000/reserve'"
            ),
        ),
    ],
)
def test_a_call_that_times_out_or_fails_is_left_out_with_why_on_one_line(
    caplog, world, network, notifications, error, why, m1
):
    """The warning that leaves Module 2 out says why on one line, the error's first line: an
    error status's message has a second line with a link. The transport's reason, whose record
    keeps the store's report on the lines after it, names the module left out. A next module that cannot be reached is left out, and the transport
    fails only with no live route left."""
    network.fails_with[M2] = error

    m1.box_transport(running_on_module_1(world))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert recorded(world, T1) == (
        "no live route to Module2: Module2 is not reachable",
        None,
    )
    assert logged(caplog, logging.WARNING)[0] == (
        f"Module1: leaves Module2 out for Box1: Module2 is not reachable: {why}"
    )
    assert notifications.sent == [(T1, WMS)]


def test_a_commit_the_store_refuses_after_reserve_fails_the_transport_and_the_bound_frees(
    caplog, world, network, timers, notifications, moves, m1, m2
):
    """Module 2 reserved itself, and the store still refused Module 1's commit: nothing moves,
    Module 1 holds the box and ends the transport failed with the store's reason. It tells Module
    2 nothing, and Module 2's reservation lapses at its bound."""
    transport = running_on_module_1(world)
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]

    m1.box_transport(transport)

    assert network.calls == [("ReserveWorkflow", M2)]
    assert moves.moved == []
    assert world.possessor_of(B1) == M1
    assert world.jobs[T1].assigned == M1
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert recorded(world, T1) == (
        "the triple store refused the handover",
        FIRST_REPORT,
    )
    assert notifications.sent == [(T1, WMS)]
    assert m2.reserve(ReserveRequest(box=B2, source_module=M1)).refusal == "reserved"
    timers.fire()
    assert m2.reserve(ReserveRequest(box=B2, source_module=M1)).accepted
    assert logged(caplog, logging.WARNING)[0] == (
        "Module1: the triple store refused handing Box1 to Module2, by "
        "FlexConveyorModulePossessionShape at Module2; it asked first, so it does not try "
        f"again:\n{FIRST_REPORT}"
    )


def test_a_next_module_without_a_box_transport_capability_is_no_way_on(
    world, tasks, notifications, m1, m2
):
    world.unregistered_capabilities.add(M2)

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert world.possessor_of(B1) == M1
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == (
        "Module2 offers no BoxTransportCapability: it has never registered its "
        "BoxTransportWorkflow"
    )


def test_a_module_reads_the_capability_of_the_next_module_once(world, tasks, m1, m2):
    for box in (B1, B2):
        m1.box_transport(running_on_module_1(world, box))
        tasks.run()

    asked = [read for read in world.reads if read[:2] == (M1, "capability")]
    assert asked == [(M1, "capability", (M2, FC.BoxTransportCapability))]


def test_a_module_reads_the_wms_once_and_the_connections_at_every_hop(
    world, tasks, m1, m2
):
    """The WMS is seeded before any module starts, so a module keeps it. The connections are read
    at every decision, in one read: a table of connections filled once is a race against the start
    of the modules. The destination decides without them."""
    for box in (B1, B2):
        m1.box_transport(running_on_module_1(world, box))
        tasks.run()

    def reads(module, name):
        return [read for read in world.reads if read[:2] == (module, name)]

    assert len(reads(M2, "wms")) == 1
    assert reads(M1, "connections") == [(M1, "connections", ())] * 2
    assert reads(M2, "connections") == []


def test_no_module_polls_the_graph_for_a_box_it_is_about_to_receive(
    world, tasks, m1, m2
):
    """Module 2 asks who holds what once, when it answers Reserve. Its Receive tells it that the
    box has come, so it never asks again."""
    m1.box_transport(running_on_module_1(world))
    tasks.run()

    asked = [read for read in world.reads if read[:2] == (M2, "boxes_held_by")]
    assert asked == [(M2, "boxes_held_by", (M2,))]


# -- The next module ------------------------------------------------------------------------------ #

# The connections of the 5-module cross: each entry and each exit is connected to Module 3 both
# ways, as the shipped topology seeds them.
CROSS_CONNECTIONS = {(m, M3) for m in (M1, M2, M4, M5)} | {
    (M3, m) for m in (M1, M2, M4, M5)
}


def moved(moves: Moves, box: IRI) -> list[IRI]:
    """Where `box` was moved, in order."""
    return [receiver for moved_box, receiver, _ in moves.moved if moved_box == box]


def test_on_the_cross_a_box_goes_from_an_entry_over_module_3_to_its_exit(
    world, tasks, moves, station
):
    world.connections = set(CROSS_CONNECTIONS)
    m1, *_ = station(M1, M3, M4)
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))
    tasks.run()

    assert moved(moves, B1) == [M3, M4, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_of_two_shortest_paths_the_box_takes_the_one_through_the_lowest_iri(
    world, network, station
):
    """Module 2 looks ahead to Module 4 before it accepts."""
    world.connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}
    m1, *_ = station(M1, M2, M3, M4)
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))

    assert network.calls == [
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M4),
        ("ReceiveWorkflow", M2),
    ]


def test_a_connection_added_later_is_used_at_the_next_decision(
    world, tasks, moves, station
):
    world.connections = {(M1, M2), (M2, M3)}
    m1, *_ = station(M1, M2, M3)
    world.place(B1, M1, destination=M3)
    m1.box_transport(world.transport(T1))
    tasks.run()

    world.connections.add((M1, M3))
    world.place(B2, M1, destination=M3)
    m1.box_transport(world.transport(T2))
    tasks.run()

    assert moved(moves, B1) == [M2, M3, WMS]
    assert moved(moves, B2) == [M3, WMS]


# -- An occupied or reserved neighbour ---------------------------------------------------------- #


def test_a_reserve_refused_as_occupied_is_asked_again_after_the_retry_time(
    caplog, world, tasks, network, moves, waits, m1, m2
):
    caplog.set_level(logging.INFO, logger=module_logger.name)
    world.place(B2, M2)
    # Box 2 leaves Module 2 while Module 1 waits.
    waits.meanwhile = [lambda: world.held[M2].remove(B2)]

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert waits.waited == [RETRY_SECONDS]
    assert network.calls[:2] == [("ReserveWorkflow", M2)] * 2
    assert moved(moves, B1) == [M2, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE
    assert (
        "Module1: Module2 refused to reserve: occupied by Box2; asking again in 0.1 s"
        in logged(caplog, logging.INFO)
    )


def test_a_reserve_refused_as_reserved_is_asked_again_until_it_is_accepted(
    world, tasks, moves, waits, m1, m2
):
    assert m2.reserve(ReserveRequest(box=B3, source_module=M3)).accepted
    # Module 2 is still reserved after the first wait, and Box 3's Receive comes during the
    # second; Box 3's transport is not in this station, so Module 2 carries nothing on for it.
    waits.meanwhile = [
        lambda: None,
        lambda: m2.receive(ReceiveRequest(box=B3, source_module=M3)),
    ]

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert waits.waited == [RETRY_SECONDS] * 2
    assert moved(moves, B1) == [M2, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_a_module_that_waits_keeps_the_box_and_writes_nothing_meanwhile(
    world, records, notifications, waits, m1, m2
):
    world.place(B2, M2)
    held_while_waiting: list[IRI | None] = []
    waits.meanwhile = [
        lambda: held_while_waiting.append(world.possessor_of(B1)),
        lambda: world.held[M2].remove(B2),
    ]

    m1.box_transport(running_on_module_1(world))

    assert held_while_waiting == [M1]
    assert records.writes == []
    assert notifications.sent == []


# -- The fault ----------------------------------------------------------------------------------- #


def test_skip_reserve_onto_a_free_module_conveys_and_the_receiver_carries_on(
    world, network, tasks, moves, notifications, m1, m2
):
    """Module 1 commits the handover without asking: Module 2 holds nothing, so the store
    admits it. Module 1 conveys the box and calls Receive, and Module 2 carries the box on."""
    m1.faults["skip-reserve"] = True

    m1.box_transport(running_on_module_1(world))

    assert network.calls == [("ReceiveWorkflow", M2)]
    assert world.possessor_of(B1) == M2
    assert world.jobs[T1].assigned == M2
    assert notifications.sent == []
    tasks.run()
    assert moved(moves, B1) == [M2, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE
    assert notifications.sent == [(T1, WMS)]


def test_both_faults_are_off_when_a_module_starts(m1):
    assert m1.faults == {"skip-reserve": False, "complete-without-releasing": False}


def test_a_handover_without_asking_that_the_store_refuses_waits_its_turn_and_arrives(
    caplog, world, network, tasks, moves, waits, m1, m2
):
    """Fault scenario 1: refused at the handover's entry, before any move. The module then asks
    first, as it should have, and waits while the receiver is occupied, so the box arrives."""
    caplog.set_level(logging.INFO, logger=module_logger.name)
    world.place(B2, M2)
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]
    # Box 2 leaves Module 2 while Module 1 waits its turn.
    waits.meanwhile = [lambda: world.held[M2].remove(B2)]

    m1.box_transport(running_on_module_1(world))

    # Refused before any move; then asked, refused as occupied, asked again, and conveyed.
    assert network.calls == [
        ("ReserveWorkflow", M2),
        ("ReserveWorkflow", M2),
        ("ReceiveWorkflow", M2),
    ]
    assert moved(moves, B1) == [M2]
    assert waits.waited == [RETRY_SECONDS]
    assert logged(caplog, logging.WARNING) == [
        (
            "Module1: the triple store refused handing Box1 to Module2, by "
            "FlexConveyorModulePossessionShape at Module2; asking before trying again:\n"
            f"{FIRST_REPORT}"
        )
    ]
    assert (
        "Module1: Module2 refused to reserve: occupied by Box2; asking again in 0.1 s"
        in logged(caplog, logging.INFO)
    )

    tasks.run()

    assert moved(moves, B1) == [M2, WMS]
    assert world.jobs[T1].status == OperationStatus.DONE


def test_a_handover_without_asking_refused_then_a_reserve_refused_otherwise_fails(
    world, notifications, m1, m2
):
    """Only a receiver that is occupied or reserved is asked again. Any other refusal of the
    Reserve that follows a refused handover ends the transport, with both reasons and the
    store's report."""
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]
    world.unregistered = {M2}

    m1.box_transport(running_on_module_1(world))

    assert world.jobs[T1].status == OperationStatus.FAILED
    reason, report_recorded = recorded(world, T1)
    assert reason == (
        "the triple store refused the handover; asked again: Module2 is not reachable: "
        "the knowledge graph names no live ReserveWorkflow of it"
    )
    assert report_recorded == FIRST_REPORT
    assert notifications.sent == [(T1, WMS)]


DONE_REPORT = report(
    B1,
    FC.DoneTransportBoxReleasedShape,
    "The box of a done transport is possessed by no FlexConveyorModule.",
)


def test_complete_without_releasing_is_refused_emitted_and_the_box_given_back_properly(
    caplog, world, records, notifications, moves, m2
):
    """Fault scenario 2: the destination commits the transport `done` while it still holds the
    box. The store refuses it, the module emits the refusal with no peer, and then gives the box
    back with the last handover, as it should have."""
    world.place(B1, M2, origin=M1, destination=M2)
    m2.faults["complete-without-releasing"] = True
    records.complete_fails_with = store_refusal(DONE_REPORT)

    m2.box_transport(world.transport(T1))

    assert emitted(m2) == [
        ("commit_refused", str(M2), str(B1), None, str(T1), DONE_REPORT)
    ]
    (event,) = m2.events.after(None)
    assert event.reason == (
        "the triple store refused the transport done while this module holds the box"
    )
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: the triple store refused completing Box1_transport without releasing "
            "Box1, by DoneTransportBoxReleasedShape at Box1; it gives the box back properly:\n"
            f"{DONE_REPORT}"
        )
    ]
    assert m2.handover.calls == [(WMS, T1, [])]
    assert moves.moved == [(B1, WMS, WMS)]
    assert world.possessor_of(B1) == WMS
    assert world.jobs[T1].status == OperationStatus.DONE
    assert notifications.sent == [(T1, WMS)]


def test_complete_without_releasing_that_the_store_admits_is_logged_and_the_box_given_back(
    caplog, world, records, m2
):
    """A store without the rules admits it. The module says so, and gives the box back all the
    same."""
    world.place(B1, M2, origin=M1, destination=M2)
    m2.faults["complete-without-releasing"] = True

    m2.box_transport(world.transport(T1))

    assert records.writes == [("complete", T1)]
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: the triple store admitted Box1_transport done while this module holds "
            "Box1: its rules are not loaded"
        )
    ]
    assert emitted(m2) == []
    assert world.possessor_of(B1) == WMS


def test_complete_without_releasing_changes_nothing_on_a_module_that_is_no_destination(
    world, records, network, m1, m2
):
    m1.faults["complete-without-releasing"] = True

    m1.box_transport(running_on_module_1(world))

    assert records.writes == []
    assert network.calls == [("ReserveWorkflow", M2), ("ReceiveWorkflow", M2)]


def test_a_retry_that_is_reserved_hands_the_box_over_the_proper_way(
    world, network, tasks, m1, m2
):
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert network.calls == [("ReserveWorkflow", M2), ("ReceiveWorkflow", M2)]
    assert world.jobs[T1].status == OperationStatus.DONE


@pytest.mark.parametrize(
    "error",
    [
        TripleStoreResponseError(UNKNOWN_REPOSITORY),
        TripleStoreResponseError(MALFORMED_UPDATE),
        TripleStoreResponseError(
            f"Error while querying GraphDB (500) - {FIRST_REPORT}"
        ),
    ],
    ids=["unknown dataset", "malformed update", "a report in the text only"],
)
def test_a_store_error_that_is_no_shacl_refusal_raises(caplog, world, m1, m2, error):
    """Only a `ShaclValidationError` is the store's refusal: no other error is read for a report,
    not even one whose text quotes one."""
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [error]

    with pytest.raises(TripleStoreResponseError) as raised:
        m1.box_transport(running_on_module_1(world))

    assert raised.value is error
    assert logged(caplog, logging.WARNING) == []


def test_an_error_that_is_not_a_refusal_is_not_swallowed(world, m1, m2):
    m1.handover.fails_with = [ConnectionError("the store is down")]

    with pytest.raises(ConnectionError, match="the store is down"):
        m1.box_transport(running_on_module_1(world))


# -- The handover event stream -------------------------------------------------------------- #


def emitted(module: FlexConveyorModule) -> list[tuple]:
    """What `module`'s handover event stream holds: kind, box, peer, transport and report."""
    return [
        (
            e.kind,
            e.resource,
            e.box,
            e.peer,
            e.transport,
            e.refusal.report if e.refusal else None,
        )
        for e in module.events.after(None)
    ]


def test_a_handover_the_store_refuses_is_emitted_with_its_report(world, tasks, m1, m2):
    """The giver emits `commit_refused` with what the store refused it with."""
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert emitted(m1) == [
        ("commit_refused", str(M1), str(B1), str(M2), str(T1), FIRST_REPORT)
    ]
    (event,) = m1.events.after(None)
    assert event.refusal is not None
    assert [(r.shape, r.focus_node) for r in event.refusal.results] == [
        (str(FC.FlexConveyorModulePossessionShape), str(M2))
    ]
    assert emitted(m2) == []


def test_a_handover_without_asking_that_the_store_refuses_is_emitted_once(
    world, tasks, waits, m1, m2
):
    world.place(B2, M2)
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [store_refusal(FIRST_REPORT)]
    waits.meanwhile = [lambda: world.held[M2].remove(B2)]

    m1.box_transport(running_on_module_1(world))
    tasks.run()

    assert world.jobs[T1].status == OperationStatus.DONE
    assert emitted(m1) == [
        ("commit_refused", str(M1), str(B1), str(M2), str(T1), FIRST_REPORT)
    ]


def test_a_last_handover_the_store_refuses_is_emitted_with_the_wms_as_peer(world, m2):
    world.place(B1, M2, origin=M1, destination=M2)
    m2.handover.fails_with = [store_refusal(SECOND_REPORT)]

    m2.box_transport(world.transport(T1))

    assert emitted(m2) == [
        ("commit_refused", str(M2), str(B1), str(WMS), str(T1), SECOND_REPORT)
    ]


def test_a_handover_the_store_admits_or_that_fails_its_precondition_emits_nothing(
    world, tasks, m1, m2
):
    m1.box_transport(running_on_module_1(world))
    tasks.run()
    transport = running_on_module_1(world, box=B2)
    world.held[M1].remove(B2)
    world.held[WMS].append(B2)
    m1.box_transport(transport)
    tasks.run()

    assert world.jobs[T1].status == OperationStatus.DONE
    assert world.jobs[T2].status == OperationStatus.FAILED
    assert emitted(m1) == emitted(m2) == []


# -- Giving the box back, and not going on ------------------------------------------------------ #


def test_the_destination_gives_the_box_back_its_transport_done_and_notifies_the_wms(
    caplog, world, network, notifications, moves, m2
):
    caplog.set_level(logging.INFO, logger="jms_kapps")
    world.place(B1, M2, origin=M1, destination=M2)

    m2.box_transport(world.transport(T1))

    assert m2.handover.calls == [(WMS, T1, [])]
    assert world.possessor_of(B1) == WMS
    assert world.jobs[T1].status == OperationStatus.DONE
    assert moves.moved == [(B1, WMS, WMS)]
    assert notifications.sent == [(T1, WMS)]
    assert network.calls == []  # The WMS has no workflow a module calls.
    assert "Module2: Box1_transport is done" in logged(caplog, logging.INFO)


def test_a_last_handover_the_store_refuses_keeps_the_box_and_fails_the_transport(
    world, notifications, m2
):
    world.place(B1, M2, origin=M1, destination=M2)
    m2.handover.fails_with = [store_refusal(SECOND_REPORT)]

    m2.box_transport(world.transport(T1))

    assert world.possessor_of(B1) == M2
    assert world.jobs[T1].status == OperationStatus.FAILED
    assert recorded(world, T1)[1] == SECOND_REPORT
    assert notifications.sent == [(T1, WMS)]


def test_a_module_with_no_route_to_the_destination_fails_the_transport(
    caplog, world, notifications, m2
):
    world.place(B1, M2, origin=M2, destination=M1)

    m2.box_transport(world.transport(T1))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert world.jobs[T1].result == "Module2 has no route to Module1"
    assert world.possessor_of(B1) == M2
    assert notifications.sent == [(T1, WMS)]
    assert logged(caplog, logging.WARNING) == [
        "Module2: Box1 stays here: Module2 has no route to Module1"
    ]


def test_a_failed_record_the_store_refuses_is_logged_and_the_wms_is_not_told(
    caplog, world, records, notifications, m2
):
    """The transport has not ended in the graph, so the WMS is not told that it has."""
    world.place(B1, M2, origin=M2, destination=M1)
    records.fail_fails_with = store_refusal(SECOND_REPORT)

    m2.box_transport(world.transport(T1))

    assert world.jobs[T1].status == OperationStatus.RUNNING
    assert notifications.sent == []
    assert logged(caplog, logging.WARNING)[-1] == (
        "Module2: the triple store refused recording Box1_transport failed, by "
        "RunningTransportBoxPossessedShape at Box1:\n" + SECOND_REPORT
    )


def test_a_notification_that_is_not_delivered_is_logged(
    caplog, world, notifications, m2
):
    world.place(B1, M2, origin=M1, destination=M2)
    notifications.undelivered = "WarehouseManagementSystem1 has no live Service"

    m2.box_transport(world.transport(T1))

    assert world.jobs[T1].status == OperationStatus.DONE
    assert logged(caplog, logging.WARNING) == [
        (
            "Module2: WarehouseManagementSystem1 was not notified of Box1_transport: "
            "WarehouseManagementSystem1 has no live Service"
        )
    ]


# -- The transport as the middleware's intake hands it ------------------------------------------ #


def test_the_transport_a_module_is_handed_is_read_from_the_node_the_intake_gives():
    """The intake reads the transport once under the module's scope: a linked value is a node,
    a literal is the value itself."""
    node = Node(
        id=T1,
        data={
            FC.hasBox: [Node(id=B1)],
            FC.hasOrigin: [Node(id=M1)],
            FC.hasDestination: [Node(id=M2)],
            SVC.hasOperationStatus: [OperationStatus.RUNNING],
            CFC.executedOn: [Node(id=M1)],
            SVC.hasExecutionResult: ["Module2 has no route to Module1"],
        },
    )

    assert transport_from(node) == Transport(
        T1,
        B1,
        M1,
        M2,
        OperationStatus.RUNNING,
        assigned=M1,
        result="Module2 has no route to Module1",
    )


def test_a_transport_with_no_assignment_and_no_result_reads_as_none():
    node = Node(
        id=T1,
        data={
            FC.hasBox: [Node(id=B1)],
            FC.hasOrigin: [Node(id=M1)],
            FC.hasDestination: [Node(id=M2)],
            SVC.hasOperationStatus: [OperationStatus.QUEUED],
        },
    )

    transport = transport_from(node)

    assert (transport.assigned, transport.result) == (None, None)
