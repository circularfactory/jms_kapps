"""A stop, or a lost peer, fails the transport with why: with the WarehouseManagementSystem and
two modules as middleware instances.

Each test seeds Module 1, the only entry module, connected east to Module 2, the only exit
module, and serves the three parties in this process (`_participants`), with a transit time of 0.
Every call between them goes over REST, every notification through the middleware, and every
write through the middleware into the store. A test whose giver's process ends during its move
runs that module as a process of its own, and kills it.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time

import httpx
import pytest
from _participants import M1, M2, RETRY_SECONDS, TWO_MODULES, participants
from _wait import eventually
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_semantic_middleware.registration import deregister_service
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.process import begin_stop
from jms_kapps.common.services import live_resources
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import bindings
from jms_kapps.uc2.discovery import boxes_held_by, capability, workflow_endpoint
from jms_kapps.uc2.handshake import ReceiveRequest, ReserveRequest, acceptance_record
from jms_kapps.uc2.module import ARRIVAL_BOUND_SECONDS
from jms_kapps.uc2.process import module_command
from jms_kapps.uc2.seed import place_box
from jms_kapps.uc2.transport import boxes, execution_result, operation_status
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, box_iri, transport_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

B1 = box_iri(1)

# A journey of one hop takes well under a second on a local store; the shared KIT store is slower.
JOURNEY_SECONDS = 30.0

# How long a module of its own takes to start and register its workflows.
STARTED_WITHIN_SECONDS = 60.0

# The move of a giver whose process the test ends during it: long enough that it ends there. It is
# the giver's transit time in the graph, and so T, the longest of the station, which the receiver
# adds to the bound of its reservation.
GIVER_MOVE_SECONDS = 10.0
GIVER_BOUND_SECONDS = ARRIVAL_BOUND_SECONDS + GIVER_MOVE_SECONDS

# How long the WMS, once told of a transport's end, hears nothing more of it.
HEARD_ONCE_SECONDS = 2.0

# The time of one commit on the shared KIT store, with the reads before it: what a receiving
# module may take beyond the bound of its reservation to end a transport.
ONE_COMMIT_SECONDS = 10.0

# The bound of a reservation of a module served here, whose transit time is 0.
BOUND_SECONDS = ARRIVAL_BOUND_SECONDS

# The bound of a reservation for the WMS's box: no move comes before the notification.
SPAWN_BOUND_SECONDS = ARRIVAL_BOUND_SECONDS


def accepted_at(caplog, record: str) -> float:
    """When the record `record` was logged, as `time.time()` tells it."""
    (created,) = [r.created for r in caplog.records if r.getMessage() == record]
    return created


def test_once_the_stop_has_begun_the_wms_refuses_a_spawn_and_opens_no_transport(
    dataset,
):
    """What the stop signal does to the WMS's process, done here without a signal: the WMS's
    spawn workflow refuses, and no box and no transport come into being."""
    with participants(dataset) as party:
        begin_stop(party.served[WMS].middleware.app)

        spawned = party.spawn()

        assert not spawned["accepted"]
        assert spawned["reason"] == "the system is stopping"
        assert boxes(party.db) == []


def box_on_module_1(db: TripleStore) -> None:
    """Setup before any party starts: Box 1 on Module 1, its transport running to Module 2."""
    place_box(OGM(db=db), B1, on=M1, origin=M1, destination=M2)


def test_a_module_whose_next_module_crashed_fails_the_transport_with_why(
    dataset, caplog
):
    """Module 2's process ends without deregistering, as a crash does: the graph still names
    its Reserve, and the call of it is refused. Module 1 leaves Module 2 out, has no other way
    on, keeps the box, ends the transport failed with no live route left, tells the WMS, and logs
    two warnings: why it left Module 2 out, and that the box stays; no error, no traceback.
    A crash is routed around, and a transport fails only with no live route left. The origin of a
    spawn looks ahead, so the WMS
    spawns no box onto Module 1 while Module 2 is gone. Box 1 is on Module 1 from the seed, and
    the WMS notifies Module 1 of its transport, as after a spawn."""
    caplog.set_level(logging.INFO)
    with participants(dataset, before_start=box_on_module_1) as party:
        crashed = party.served[M2]
        crashed.server.force_exit = True  # no lifespan shutdown, so no deregistration
        crashed.server.should_exit = True
        crashed.thread.join(timeout=30)
        try:
            assert M2 in live_resources(party.db)
            transport = transport_iri(B1)
            # The seed names no capability: Module 1's comes with its registration.
            m1_capability = capability(party.db, M1, FC.BoxTransportCapability)
            assert m1_capability is not None
            assert party.db.triple_add(
                (transport, CFC.implementsCapability, m1_capability)
            )

            party.served[WMS].middleware.notify(operation=transport, resource=M1)

            eventually(
                lambda: operation_status(party.db, transport) == OperationStatus.FAILED,
                timeout=JOURNEY_SECONDS,
            )
            result = execution_result(party.db, transport)
            assert result == "no live route to Module2: Module2 is not reachable", (
                result
            )
            assert boxes_held_by(party.db, M1) == [B1]
            told = f"WarehouseManagementSystem1: Box1_transport failed: {result}"
            eventually(
                lambda: told in [r.getMessage() for r in caplog.records],
                timeout=JOURNEY_SECONDS,
            )

            # The module says why once it has told the WMS, so its line may come second.
            def module_warnings() -> list[str]:
                return [
                    r.getMessage()
                    for r in caplog.records
                    if r.name == "jms_kapps.uc2.module" and r.levelno == logging.WARNING
                ]

            eventually(lambda: len(module_warnings()) >= 2, timeout=JOURNEY_SECONDS)
            left_out, stays = module_warnings()
            assert left_out.startswith(
                "Module1: leaves Module2 out for Box1: Module2 is not reachable: "
            ), left_out
            assert "\n" not in left_out
            assert stays == f"Module1: Box1 stays here: {result}"
            assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        finally:
            # What its shutdown would have done, so that nothing stays live in the dataset.
            deregister_service(crashed.middleware.ogm, crashed.middleware.service_iri)


def deregister(db: TripleStore, resource: IRI) -> None:
    """What the shutdown of `resource`'s killed process would have done: deregister each of its
    services, so that nothing stays live in the dataset."""
    rows = bindings(
        db, f"SELECT ?service WHERE {{ ?service <{SVC.isServiceOf}> <{resource}> }}"
    )
    for row in rows:
        deregister_service(OGM(db=db), IRI(str(row["service"])))


def test_a_receiver_ends_the_transport_of_a_giver_lost_after_its_entry_commit(
    dataset, caplog, tmp_path
):
    """Module 1 runs as a process of its own, and the test kills it during its move of Box 1:
    after its entry commit made Module 2 the possessor of the box and the assigned module of its
    transport, and before its call of Module 2's Receive. At the bound of its reservation,
    counted from its answer to Reserve, Module 2 reads the box with its transport through the
    box's link to it, ends the transport failed with a reason that names Module 1, and tells the
    WMS, which hears of the box once: within the bound and the time of one commit. The seed gives
    the transport an IRI that does not follow the box's name, so no naming rule finds it."""
    caplog.set_level(logging.INFO)
    transport = IRI("Transport7", base=FCI_NS)

    def box_on_module_1(db: TripleStore) -> None:
        place_box(OGM(db=db), B1, on=M1, origin=M1, destination=M2, transport=transport)

    giver_is_slow = TWO_MODULES.model_copy(
        update={
            "modules": [
                m.model_copy(update={"transit_seconds": GIVER_MOVE_SECONDS})
                if m.resource == M1
                else m
                for m in TWO_MODULES.modules
            ]
        }
    )
    with participants(
        dataset, giver_is_slow, before_start=box_on_module_1, elsewhere={M1}
    ) as party:
        with (tmp_path / "Module1.log").open("w") as output:
            giver = subprocess.Popen(
                module_command(M1, dataset, retry_seconds=RETRY_SECONDS),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        try:
            eventually(
                lambda: (
                    M1 in live_resources(party.db)
                    and capability(party.db, M1, FC.BoxTransportCapability) is not None
                ),
                timeout=STARTED_WITHIN_SECONDS,
            )
            # Setup: the seed names no capability, since a module's comes with its
            # registration. The transport implements Module 1's, as the WMS's opening writes it.
            m1_capability = capability(party.db, M1, FC.BoxTransportCapability)
            assert party.db.triple_add(
                (transport, CFC.implementsCapability, m1_capability)
            )
            # As the WMS notifies the origin of a transport.
            party.served[WMS].middleware.notify(operation=transport, resource=M1)

            # Module 1's entry commit: the box and its transport are Module 2's now.
            eventually(
                lambda: boxes_held_by(party.db, M2) == [B1], timeout=JOURNEY_SECONDS
            )
            giver.kill()
            giver.wait(timeout=10)

            eventually(
                lambda: operation_status(party.db, transport) == OperationStatus.FAILED,
                timeout=GIVER_BOUND_SECONDS + ONE_COMMIT_SECONDS,
            )
            ended = time.time()
            answered = accepted_at(
                caplog, acceptance_record(M2, FC.ReserveWorkflow, B1, M1)
            )
            assert (
                GIVER_BOUND_SECONDS
                <= ended - answered
                <= GIVER_BOUND_SECONDS + ONE_COMMIT_SECONDS
            )
            result = execution_result(party.db, transport)
            assert result == (
                "Module1 is lost: it did not call ReceiveWorkflow for Box1 within "
                f"{GIVER_BOUND_SECONDS:g} s"
            )
            assert boxes_held_by(party.db, M2) == [B1]

            def heard_by_the_wms() -> list[str]:
                return [
                    r.getMessage()
                    for r in caplog.records
                    if r.name == "jms_kapps.uc2.wms"
                    and transport.fragment in r.getMessage()
                ]

            told = f"WarehouseManagementSystem1: Transport7 failed: {result}"
            eventually(lambda: heard_by_the_wms() != [], timeout=JOURNEY_SECONDS)
            assert heard_by_the_wms() == [told]
            # And still once a while later: no second notification follows.
            time.sleep(HEARD_ONCE_SECONDS)
            assert heard_by_the_wms() == [told]
            module_warnings = [
                r.getMessage()
                for r in caplog.records
                if r.name == "jms_kapps.uc2.module" and r.levelno == logging.WARNING
            ]
            assert module_warnings == [f"Module2: Box1 is here: {result}"]
            assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        finally:
            giver.kill()
            giver.wait(timeout=10)
            deregister(party.db, M1)


def test_a_reservation_whose_box_never_comes_is_freed_at_the_bound(dataset, caplog):
    """The test plays a Module 1 that asked Module 2's Reserve for Box 1 and went away before its
    commit: the box and its transport stay Module 1's. Module 2 refuses every other box as
    reserved until the bound of its reservation has passed, counted from its answer to Reserve,
    then frees the reservation, so that it reserves itself again, and writes nothing."""
    caplog.set_level(logging.INFO)

    def box_on_module_1(db: TripleStore) -> None:
        place_box(OGM(db=db), B1, on=M1, origin=M1, destination=M2)

    with participants(
        dataset, before_start=box_on_module_1, elsewhere={M1}
    ) as party:
        endpoint = workflow_endpoint(party.db, M2, FC.ReserveWorkflow)
        assert endpoint is not None

        def reserve(box: IRI, giver: IRI) -> dict:
            request = ReserveRequest(box=str(box), source_module=str(giver))
            response = httpx.post(endpoint, json=request.model_dump(), timeout=30)
            response.raise_for_status()
            return response.json()

        assert reserve(B1, M1)["accepted"]
        answered = accepted_at(
            caplog, acceptance_record(M2, FC.ReserveWorkflow, B1, M1)
        )
        other = box_iri(2)
        refused = reserve(other, M1)
        assert (refused["accepted"], refused["refusal"]) == (False, "reserved")
        assert time.time() - answered < BOUND_SECONDS

        freed = (
            f"Module2: Box1 did not come from Module1 within {BOUND_SECONDS:g} s, so it "
            "frees its reservation"
        )
        eventually(
            lambda: freed in [r.getMessage() for r in caplog.records],
            timeout=BOUND_SECONDS + ONE_COMMIT_SECONDS,
        )
        assert time.time() - answered >= BOUND_SECONDS
        # Free again. A Receive uses the new reservation up, so that no bound outlives the test
        # and fires in another one: Box 1's transport is still Module 1's, so Module 2 leaves it
        # alone and writes nothing.
        assert reserve(B1, M1)["accepted"]
        receive = workflow_endpoint(party.db, M2, FC.ReceiveWorkflow)
        assert receive is not None
        request = ReceiveRequest(box=str(B1), source_module=str(M1))
        httpx.post(receive, json=request.model_dump(), timeout=30).raise_for_status()
        left_alone = (
            "Module2: Box1_transport is assigned to Module1, so it is left alone"
        )
        eventually(
            lambda: left_alone in [r.getMessage() for r in caplog.records],
            timeout=JOURNEY_SECONDS,
        )
        assert boxes_held_by(party.db, M1) == [B1]
        assert operation_status(party.db, transport_iri(B1)) == OperationStatus.RUNNING


def test_an_origin_the_wms_cannot_notify_leaves_alone_the_transport_the_wms_ended(
    dataset, caplog
):
    """The WMS creates Box 1 on Module 1 and its notification does not reach Module 1: the
    WMS ends the transport failed with why, and the spawn says so. At the bound of its
    reservation, 30 s from its answer to Reserve, Module 1 finds the box on it and the transport
    ended: it frees its reservation and leaves the transport alone. Module 1 stays live, so its bound runs: the WMS's notification fails as the
    middleware's does when the post to the origin's service fails. Module 2, which Module 1's
    look-ahead reserved ahead for Box 1, frees its reservation at its own bound, since Box 1 never
    comes."""
    caplog.set_level(logging.INFO)
    with participants(dataset) as party:

        def not_taken(*, operation, resource, **kwargs) -> None:
            raise NotificationUndelivered(
                f"{IRI(str(resource)).fragment} did not take the notification"
            )

        # The WMS's notification is the middleware's, looked up at each call.
        party.served[WMS].middleware.notify = not_taken

        spawned = party.spawn(origin=str(M1), destination=str(M2))

        reason = "Module1 is not reachable: Module1 did not take the notification"
        assert (spawned["accepted"], spawned["reason"]) == (False, reason)
        transport = IRI(spawned["operation"])
        box = IRI(spawned["box"])
        assert operation_status(party.db, transport) == OperationStatus.FAILED
        assert execution_result(party.db, transport) == reason
        assert boxes_held_by(party.db, M1) == [box]
        answered = accepted_at(
            caplog, acceptance_record(M1, FC.ReserveWorkflow, box, WMS)
        )
        left_alone = f"Module1: {transport.fragment} is failed, so it is left alone"
        freed_ahead = (
            f"Module2: {box.fragment} did not come from Module1 within 30 s, so it frees its "
            "reservation"
        )
        eventually(
            lambda: (
                {left_alone, freed_ahead} <= {r.getMessage() for r in caplog.records}
            ),
            timeout=SPAWN_BOUND_SECONDS + ONE_COMMIT_SECONDS,
        )
        assert time.time() - answered >= SPAWN_BOUND_SECONDS
        assert execution_result(party.db, transport) == reason
        module_warnings = [
            r.getMessage()
            for r in caplog.records
            if r.name == "jms_kapps.uc2.module" and r.levelno == logging.WARNING
        ]
        assert sorted(module_warnings) == sorted([left_alone, freed_ahead])
        wms_warnings = [
            r.getMessage()
            for r in caplog.records
            if r.name == "jms_kapps.uc2.wms" and r.levelno == logging.WARNING
        ]
        assert wms_warnings == [
            f"WarehouseManagementSystem1: {box.fragment} is on Module1: {reason}"
        ]


# -- The stop: only the module a transport is assigned to writes it ------------- #

# The reason of a transport that a stop ends.
STOPPING = "the system is stopping"

# The move of Box 1 off Module 1 in the stop tests: long enough that the Ctrl+C comes during it.
MOVE_SECONDS = 2.0


def ctrl_c(party, *, join: bool = True) -> None:
    """What a terminal's Ctrl+C does to every party at once, without a signal: each marks its
    stop as begun at the same moment, as the stop signal does, and then its server shuts down,
    with the lifespan shutdown where each deregisters and a module waits at its stop."""
    for served in party.served.values():
        begin_stop(served.middleware.app)
    for served in party.served.values():
        served.server.should_exit = True
    if join:
        for served in party.served.values():
            served.thread.join(timeout=30)


def failed_by(caplog, transport: IRI) -> list[str]:
    """Each party that recorded `transport` failed, by the record it logs once the store has
    admitted the write."""
    return [
        r.getMessage().split(":")[0]
        for r in caplog.records
        if r.getMessage().endswith(f": {transport.fragment} is failed")
    ]


def assigned_to(db: TripleStore, transport: IRI) -> list[IRI]:
    rows = bindings(
        db, f"SELECT ?module WHERE {{ <{transport}> <{CFC.executedOn}> ?module }}"
    )
    return [IRI(str(row["module"])) for row in rows]


def warnings_of(caplog, logger: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == logger and r.levelno == logging.WARNING
    ]


def test_ctrl_c_while_a_box_moves_between_two_modules_ends_it_by_the_receiver(
    dataset, caplog
):
    """Ctrl+C reaches every party while Module 1 moves Box 1 onto Module 2, after its commit made
    Module 2 the box's possessor and the transport's assignee. Module 1 finishes its move and
    calls Module 2's Receive, which is refused, since Module 2's server takes no new connection:
    Module 1 writes nothing. Module 2 waits for the Receive, the graceful-shutdown bound from the
    stop at most, then ends the transport failed, "the system is stopping": written once, by the
    module it is assigned to, well before the Launcher's kill."""
    caplog.set_level(logging.INFO)
    moving = threading.Event()

    def move(box: IRI, receiver: IRI) -> None:
        moving.set()
        time.sleep(MOVE_SECONDS)

    with participants(dataset, moves={M1: move}) as party:
        spawned = party.spawn(origin=str(M1), destination=str(M2))
        assert spawned["accepted"], spawned
        transport = IRI(spawned["operation"])
        box = IRI(spawned["box"])
        assert moving.wait(JOURNEY_SECONDS), "Module 1 never moved Box 1"
        assert boxes_held_by(party.db, M2) == [box]

        asked = time.monotonic()
        ctrl_c(party)
        stopped = time.monotonic() - asked

        assert stopped < STOP_TIMEOUT_SECONDS, stopped
        assert operation_status(party.db, transport) == OperationStatus.FAILED
        assert execution_result(party.db, transport) == STOPPING
        assert failed_by(caplog, transport) == ["Module2"]
        assert assigned_to(party.db, transport) == [M2]
        assert boxes_held_by(party.db, M2) == [box]
        module_warnings = warnings_of(caplog, "jms_kapps.uc2.module")
        assert module_warnings[0].startswith(
            f"Module1: {box.fragment} is on Module2, so {box.fragment}_transport is Module2's "
            "to end: Module2 is not reachable: "
        ), module_warnings
        assert module_warnings[1].startswith(f"Module2: {box.fragment} is here: {STOPPING}")
        assert len(module_warnings) == 2, module_warnings
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert live_resources(party.db) == set()


def refuses_connections(address: str) -> bool:
    """Whether the server at `address` takes no new connection: its stop has begun."""
    host, port = address.rstrip("/").rsplit("//", 1)[1].rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=1).close()
    except OSError:
        return True
    return False


def test_ctrl_c_between_the_wms_s_create_and_its_notification_ends_it_once_by_the_wms(
    dataset, caplog
):
    """Ctrl+C reaches every party once the WMS has created Box 1 on Module 1, and before it
    notifies Module 1, whose server takes no new connection by then. The WMS ends the transport
    failed, once, "the system is stopping", and the spawn says so. Module 1, the origin, waits for
    the WMS's notification, not for a Receive, and leaves the transport to the WMS: it writes
    nothing."""
    caplog.set_level(logging.INFO)
    with participants(dataset) as party:
        wms = party.served[WMS].middleware
        notify = wms.notify
        origin = party.served[M1].middleware.address

        def ctrl_c_then_notify(*, operation, resource, **kwargs) -> None:
            ctrl_c(party, join=False)
            eventually(lambda: refuses_connections(origin), timeout=10)
            notify(operation=operation, resource=resource, **kwargs)

        # The WMS's notification is the middleware's, looked up at each call.
        wms.notify = ctrl_c_then_notify

        spawned = party.spawn(origin=str(M1), destination=str(M2))
        for served in party.served.values():
            served.thread.join(timeout=30)

        assert (spawned["accepted"], spawned["reason"]) == (False, STOPPING)
        transport = IRI(spawned["operation"])
        box = IRI(spawned["box"])
        assert operation_status(party.db, transport) == OperationStatus.FAILED
        assert execution_result(party.db, transport) == STOPPING
        assert failed_by(caplog, transport) == ["WarehouseManagementSystem1"]
        assert boxes_held_by(party.db, M1) == [box]
        (wms_warning,) = warnings_of(caplog, "jms_kapps.uc2.wms")
        assert wms_warning.startswith(
            f"WarehouseManagementSystem1: {box.fragment} is on Module1: {STOPPING}; Module1 is not "
            "reachable: "
        ), wms_warning
        # Module 1's stop leaves the transport to the WMS, whether the WMS ended it yet or not.
        left_to_the_wms = {
            (
                "Module1: WarehouseManagementSystem1 did not notify it of "
                f"{transport.fragment} before the stop, so it frees its reservation and "
                "leaves the transport to WarehouseManagementSystem1"
            ),
            f"Module1: {transport.fragment} is failed, so it is left alone",
        }
        module_warnings = warnings_of(caplog, "jms_kapps.uc2.module")
        # Module 2, which Module 1's look-ahead reserved ahead for Box 1, frees its reservation
        # at its own stop, and writes nothing.
        freed_ahead = (
            f"Module2: {box.fragment} did not come from Module1 before the stop, so it frees its "
            "reservation"
        )
        assert freed_ahead in module_warnings, module_warnings
        origin_warnings = [w for w in module_warnings if w != freed_ahead]
        assert len(origin_warnings) == 1, module_warnings
        assert set(origin_warnings) <= left_to_the_wms, module_warnings
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert live_resources(party.db) == set()


def test_a_giver_whose_receive_fails_after_its_commit_writes_nothing(
    dataset, caplog, tmp_path
):
    """Module 2 runs as a process of its own, and the test kills it while Module 1 moves Box 1
    onto it, after Module 1's commit. Module 1's Receive is refused: Module 1 writes nothing, and
    says so. The transport stays running, assigned to Module 2, with the box on it: the known
    limit of a receiver that crashes after the commit, which KSM's crash recovery ends at
    Module 2's next start."""
    caplog.set_level(logging.INFO)
    moving, go_on = threading.Event(), threading.Event()

    def held_move(box: IRI, receiver: IRI) -> None:
        moving.set()
        assert go_on.wait(JOURNEY_SECONDS)

    def receiver_process(log: str) -> subprocess.Popen:
        with (tmp_path / log).open("w") as output:
            return subprocess.Popen(
                module_command(M2, dataset, retry_seconds=RETRY_SECONDS),
                stdout=output,
                stderr=subprocess.STDOUT,
            )

    def receiver_is_live() -> bool:
        return M2 in live_resources(party.db) and (
            workflow_endpoint(party.db, M2, FC.ReceiveWorkflow) is not None
        )

    with participants(dataset, moves={M1: held_move}, elsewhere={M2}) as party:
        receiver = receiver_process("Module2.log")
        restarted: subprocess.Popen | None = None
        try:
            eventually(receiver_is_live, timeout=STARTED_WITHIN_SECONDS)
            spawned = party.spawn(origin=str(M1), destination=str(M2))
            assert spawned["accepted"], spawned
            transport = IRI(spawned["operation"])
            box = IRI(spawned["box"])
            assert moving.wait(JOURNEY_SECONDS), "Module 1 never moved Box 1"
            receiver.kill()
            receiver.wait(timeout=10)

            go_on.set()

            not_reached = (
                f"Module1: {box.fragment} is on Module2, so {box.fragment}_transport is "
                "Module2's to end: Module2 is not reachable: "
            )
            eventually(
                lambda: any(
                    w.startswith(not_reached)
                    for w in warnings_of(caplog, "jms_kapps.uc2.module")
                ),
                timeout=JOURNEY_SECONDS,
            )
            time.sleep(HEARD_ONCE_SECONDS)
            assert failed_by(caplog, transport) == []
            assert operation_status(party.db, transport) == OperationStatus.RUNNING
            assert assigned_to(party.db, transport) == [M2]
            assert boxes_held_by(party.db, M2) == [box]
            assert warnings_of(caplog, "jms_kapps.uc2.module") == [
                w
                for w in warnings_of(caplog, "jms_kapps.uc2.module")
                if w.startswith(not_reached)
            ]

            # The known limit ends at the receiver's next start.
            restarted = receiver_process("Module2-restarted.log")
            eventually(
                lambda: operation_status(party.db, transport) == OperationStatus.FAILED,
                timeout=STARTED_WITHIN_SECONDS,
            )
            assert execution_result(party.db, transport) == (
                "orphaned running operation reclaimed at startup, not auto-rerun"
            )
            assert failed_by(caplog, transport) == []
        finally:
            go_on.set()
            for process in (receiver, restarted):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=STOP_TIMEOUT_SECONDS + 5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            deregister(party.db, M2)
