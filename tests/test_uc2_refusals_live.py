"""Both of §5.2's faults in the running FlexConveyor station, and the monitor showing each
refusal, on a live GraphDB.

The WarehouseManagementSystem and the five modules of the cross are served in this process
(`_participants`), each move taking 1 s, and the monitor's background task reads the graph and
their handover event streams as the monitor's process does, with the real reads and the real
stream reader. Its page's JSON is read the way the page reads it. Each fault is armed on its
module through the module's own route, `POST /faults/{name}`, at the address its service
registered.

- **Fault scenario 1, `skip-reserve`.** Box A travels from Module 2 over Module 3 onto Module 4.
  While Module 4 holds it, box B is spawned onto Module 1 toward Module 4 too, with the fault armed
  on Module 3: Module 3 takes B without looking ahead, and hands B to Module 4 without asking. The
  store refuses the handover's entry before any move; Module 3 emits `commit_refused`, asks
  Module 4's Reserve, and waits its turn; B still reaches its exit. Module 4 holds A
  deterministically: Module 3's move of A takes 1 s and then waits until the test lets it end, and
  Module 4 possesses A from the handover's entry, for as long as the move lasts. The origin of a spawn looks ahead, so the WMS
  spawns no box onto Module 1 while Module 3 holds one, and the fault skips the look-ahead of the
  module it is armed on.
- **Fault scenario 2, `complete-without-releasing`.** A box goes to Module 4 with the fault armed
  there. Module 4 commits its transport `done` before it gives the box back; the store refuses
  that, Module 4 emits `commit_refused`, then gives the box back with the last handover.

- **A spawn onto an origin that a neighbour filled.** On a station of three modules, where an
  entry module hands boxes to another entry module, the neighbour with `skip-reserve` armed fills
  the origin after its answer to the WMS's Reserve. The store refuses the WMS's create, and the
  WMS emits `commit_refused` with the store's report.

A participant emits `commit_refused` only where it catches the store's refusal as the interface's
`ShaclValidationError`, so each event here is that type reaching the participant.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from _html import rows
from _participants import HOST, M1, M2, Participants, participants
from _uc2_run import POSSESSION_PATH, set_fault
from _wait import eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import (
    ValidationResult,
    validation_results,
)
from kapps_semantic_middleware.vocabulary import CFC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import connect
from jms_kapps.uc2 import wms_process
from jms_kapps.uc2.discovery import boxes_held_by, capability, service_address
from jms_kapps.uc2.handover_events import (
    STREAM_ROUTE,
    BrokenRule,
    HandoverEvent,
    HandoverEvents,
)
from jms_kapps.uc2.handshake import ReserveRequest, acceptance_record
from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import MonitorMemory
from jms_kapps.uc2.monitor.reads import GraphReads
from jms_kapps.uc2.process import STEP_TIMEOUT_SECONDS
from jms_kapps.uc2.seed import place_box
from jms_kapps.uc2.topology import Connection, Module, Topology, shipped_topology
from jms_kapps.uc2.transport import boxes, operation_status
from jms_kapps.uc2.vocabulary import FC, WMS, box_iri, module_iri, transport_iri
from jms_kapps.uc2.wms import taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

M3, M4, M5 = (module_iri(n) for n in (3, 4, 5))
CROSS = shipped_topology("cross")

# The transit time of these tests: each move takes 1 s.
TRANSIT_SECONDS = 1.0

# The monitor shows a refusal within this long.
SHOWN_WITHIN_SECONDS = 3.0

# The monitor finds the streams every 5 s, after it has seen the participants live.
FOLLOWED_WITHIN_SECONDS = 30.0

# A box's journey over two hops of 1 s each, on the shared remote store.
ARRIVES_WITHIN_SECONDS = 90.0

# How long Module 3's move of A may wait for the test. Module 4's reservation for A has a bound of
# 31 s, the transit time included, from its answer to Reserve: A arrives well within it, after
# Module 3's commit, its move of 1 s and this wait.
HELD_AT_MOST_SECONDS = 22.0


@pytest.fixture
def records(caplog):
    """Every record the parties log, as its message, in the order they logged them."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    def records() -> list[str]:
        return [
            r.getMessage() for r in caplog.records if r.name.startswith("jms_kapps")
        ]

    return records


@contextmanager
def monitoring(dataset: str) -> Iterator[tuple[MonitorMemory, TestClient]]:
    """The monitor's background task on `dataset`, and a client of its page's JSON."""
    memory = MonitorMemory(GraphReads(connect(dataset)))
    app = FastAPI()
    mount_onto(app, memory=memory)
    memory.start()
    try:
        yield memory, TestClient(app)
    finally:
        memory.stop()


def stream_of(party, resource: IRI) -> HandoverEvents:
    return party.served[resource].middleware.app.state.handover_events


def refused(events: HandoverEvents, box: IRI) -> HandoverEvent:
    """The one refusal `events` holds for `box`, once it holds one."""
    eventually(
        lambda: any(e.box == str(box) for e in events.after(None)),
        timeout=ARRIVES_WITHIN_SECONDS,
    )
    (event,) = [e for e in events.after(None) if e.box == str(box)]
    return event


def shown_in_time(client: TestClient, event: HandoverEvent) -> dict:
    """The refusal `event` as the Refusals view shows it, once it does, within 3 s."""
    deadline = time.monotonic() + SHOWN_WITHIN_SECONDS
    while True:
        shown = {r["id"]: r for r in client.get("/api/state").json()["refusals"]}
        if event.id in shown:
            return shown[event.id]
        assert time.monotonic() < deadline, f"{event.id} not shown within 3 s"
        time.sleep(0.05)


# The message of the rule that refuses a transport done while a module possesses its box.
RELEASED_MESSAGE = "The Box of a done BoxTransportOperation must not be possessed by a FlexConveyorModule."


# The logger on which the middleware records each refused write of an instance, once, at INFO.
REFUSED_WRITES_LOGGER = "kapps_semantic_middleware.validation_report"


def refused_writes_shown(party, resource: IRI) -> tuple[str, list[str]]:
    """What `resource`'s own pages, which the middleware draws, show of its refused writes: the
    box "Refused writes" of its dashboard, as the page fetches it, and the first line of each
    INFO record of a refused write on its activity page."""
    address = service_address(party.db, resource)
    assert address is not None
    box = httpx.get(f"{address.rstrip('/')}/dashboard/box/refused-writes", timeout=10)
    box.raise_for_status()
    feed = party.served[resource].middleware.activity_feed
    return box.text, [
        record.message.splitlines()[0]
        for record in feed.snapshot()
        if (record.logger, record.level) == (REFUSED_WRITES_LOGGER, "INFO")
    ]


def readable(shown: dict) -> set[tuple[str, str | None, str | None]]:
    """What a person reads of each result of the refusal `shown`: its message, its focus node
    by short name, and its source shape whole."""
    return {
        (
            result["message"],
            result["focus"] and result["focus"]["short"],
            result["shape"] and result["shape"]["full"],
        )
        for result in shown["readable_results"]
    }


def arrives(records, box: IRI, transport: IRI) -> None:
    """Wait until the WarehouseManagementSystem has taken `box` back, its transport done."""
    eventually(
        lambda: taken_back_record(WMS, box, transport) in records(),
        timeout=ARRIVES_WITHIN_SECONDS,
    )


def test_each_participant_commits_where_its_stream_lives(dataset):
    with participants(dataset) as party:
        located = GraphReads(party.db).streams()

        assert set(located) == {M1, M2, WMS}
        for resource, url in located.items():
            address = service_address(party.db, resource)
            assert address is not None
            assert url == f"{address.rstrip('/')}{STREAM_ROUTE}"
            assert url.startswith(f"http://{HOST}:")


def test_skip_reserve_onto_an_occupied_module_is_refused_before_any_move_and_arrives(
    dataset, records
):
    """Fault scenario 1, in the running FlexConveyor station.

    The store refuses the handover onto the occupied module before anything is admitted (§5.2): the
    box stays the giver's, and the receiver keeps its own box. Enforcement at the moment of
    execution (R9): the refused handover never runs its
    body, the move, so the giver never moves the box until a later handover is admitted."""
    a_may_arrive = threading.Event()
    moved_by_module_3: list[str] = []
    held: list[str] = []

    def module_3_moves(box: object, receiver: object) -> None:
        moved_by_module_3.append(str(box))
        time.sleep(TRANSIT_SECONDS)
        if not held:
            held.append(str(box))
            a_may_arrive.wait(HELD_AT_MOST_SECONDS)

    with (
        participants(
            dataset,
            CROSS,
            transit_seconds=TRANSIT_SECONDS,
            moves={M3: module_3_moves},
        ) as party,
        monitoring(dataset) as (memory, client),
    ):
        eventually(
            lambda: {M3, WMS} <= set(memory.following()),
            timeout=FOLLOWED_WITHIN_SECONDS,
        )
        spawned_a = party.spawn(origin=str(M2), destination=str(M4))
        assert spawned_a["accepted"], spawned_a
        box_a = IRI(spawned_a["box"])
        eventually(
            lambda: boxes_held_by(party.db, M4) == [box_a],
            timeout=ARRIVES_WITHIN_SECONDS,
        )
        set_fault(party.db, M3, "skip-reserve", armed=True)

        spawned_b = party.spawn(origin=str(M1), destination=str(M4))

        assert spawned_b["accepted"], spawned_b
        box_b, transport_b = IRI(spawned_b["box"]), IRI(spawned_b["operation"])
        event = refused(stream_of(party, M3), box_b)
        # Refused before any move: B is still Module 3's, and Module 3 never moved it.
        assert moved_by_module_3 == [str(box_a)]
        assert boxes_held_by(party.db, M3) == [box_b]
        assert boxes_held_by(party.db, M4) == [box_a]
        assert (event.peer, event.transport) == (str(M4), str(transport_b))
        assert event.refusal is not None
        rule = ValidationResult(
            focus=str(M4),
            path=POSSESSION_PATH,
            shape=str(FC.FlexConveyorModulePossessionShape),
        )
        assert rule in validation_results(event.refusal.report)
        assert (
            BrokenRule(shape=rule.shape, focus_node=rule.focus, path=rule.path)
            in event.refusal.results
        )
        # Module 3's own pages show it as the middleware shows any refused write: its dashboard's
        # box "Refused writes", and the INFO record on its activity page, whose first line names
        # the verb and the rule.
        first_line = "handover refused by FlexConveyorModulePossessionShape at Module4"
        assert [
            (write.verb, write.rules)
            for write in party.served[M3].middleware.refused_writes
        ] == [("handover", ("FlexConveyorModulePossessionShape at Module4",))]
        box_html, logged = refused_writes_shown(party, M3)
        assert first_line in box_html
        assert first_line in logged
        shown = shown_in_time(client, event)
        assert (shown["giver"], shown["receiver"], shown["box"]) == (
            "Module3",
            "Module4",
            box_b.fragment,
        )
        assert shown["report"] == event.refusal.report
        assert shown["results"] == [r.model_dump() for r in event.refusal.results]
        assert shown["report_note"] is None
        assert (
            "A FlexConveyorModule may possess at most one Box at a time.",
            "Module4",
            str(FC.FlexConveyorModulePossessionShape),
        ) in readable(shown)
        # Module 3 asks first now, and waits its turn while Module 4 holds A: no detour leads
        # from Module 3 to Module 4 but back over Module 3.
        waiting = (
            f"Module3: Module4 refused to reserve: occupied by {box_a.fragment}; "
            "asking again in 0.1 s"
        )
        eventually(lambda: waiting in records(), timeout=10)
        log = records()
        # Module 3 took B from Module 1 without looking ahead, and Module 4, which holds A, has
        # reserved itself for B neither then nor since.
        assert acceptance_record(M3, FC.ReserveWorkflow, box_b, M1) in log
        assert acceptance_record(M4, FC.ReserveWorkflow, box_b, M3) not in log

        a_may_arrive.set()

        arrives(records, box_b, transport_b)
        assert operation_status(party.db, transport_b) == OperationStatus.DONE
        log = records()
        assert acceptance_record(M4, FC.ReceiveWorkflow, box_b, M3) in log
        assert box_b in boxes_held_by(party.db, WMS)
        # Refused once: the hop that asked first was admitted.
        assert [e.box for e in stream_of(party, M3).after(None)] == [str(box_b)]


def test_complete_without_releasing_is_refused_and_the_box_ends_in_the_wms_done(
    dataset, records
):
    """Fault scenario 2, in the running FlexConveyor station."""
    with (
        participants(dataset, CROSS, transit_seconds=TRANSIT_SECONDS) as party,
        monitoring(dataset) as (memory, client),
    ):
        eventually(lambda: M4 in memory.following(), timeout=FOLLOWED_WITHIN_SECONDS)
        set_fault(party.db, M4, "complete-without-releasing", armed=True)

        spawned = party.spawn(origin=str(M1), destination=str(M4))

        assert spawned["accepted"], spawned
        box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
        event = refused(stream_of(party, M4), box)
        assert (event.peer, event.transport) == (None, str(transport))
        assert event.refusal is not None
        assert (
            BrokenRule(
                shape=str(FC.DoneTransportBoxReleasedShape),
                focus_node=str(box),
                path=None,
            )
            in event.refusal.results
        )
        assert str(FC.DoneTransportBoxReleasedShape) in {
            found.shape for found in validation_results(event.refusal.report)
        }
        shown = shown_in_time(client, event)
        assert (shown["giver"], shown["receiver"], shown["box"]) == (
            "Module4",
            None,
            box.fragment,
        )
        assert f"DoneTransportBoxReleasedShape at {box.fragment}" in shown["rules"]
        assert shown["report"] == event.refusal.report
        assert shown["results"] == [r.model_dump() for r in event.refusal.results]
        assert shown["report_note"] is None
        assert (
            RELEASED_MESSAGE,
            box.fragment,
            str(FC.DoneTransportBoxReleasedShape),
        ) in readable(shown)

        arrives(records, box, transport)
        assert operation_status(party.db, transport) == OperationStatus.DONE
        assert box in boxes_held_by(party.db, WMS)
        assert [e.box for e in stream_of(party, M4).after(None)] == [str(box)]


def test_skip_reserve_onto_a_free_module_conveys_and_the_receiver_carries_on(
    dataset, records
):
    """Fault scenario 1 with nothing to collide with: Module 1 commits the handover without
    asking, Module 2 holds nothing, so the store admits it. Module 1 conveys the box and calls
    Module 2's Receive, and Module 2 carries the box on, back to the WMS. Nothing is refused."""
    with participants(dataset) as party:
        set_fault(party.db, M1, "skip-reserve", armed=True)

        spawned = party.spawn(origin=str(M1), destination=str(M2))

        assert spawned["accepted"], spawned
        box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
        arrives(records, box, transport)
        log = records()
        assert (
            f"Module1: handed {box.fragment} to Module2 without asking its ReserveWorkflow, "
            "as skip-reserve is armed"
        ) in log
        # Module 2 accepted one Reserve of Module 1: the plain one of Module 1's look-ahead at
        # the spawn, which the WMS's Reserve always gets. The hop itself asked none.
        assert log.count(acceptance_record(M2, FC.ReserveWorkflow, box, M1)) == 1
        assert acceptance_record(M2, FC.ReceiveWorkflow, box, M1) in log
        assert operation_status(party.db, transport) == OperationStatus.DONE
        assert stream_of(party, M1).after(None) == []


# Module 3, an entry module, hands boxes to Module 1, an entry module too, which hands them to
# Module 2, the exit module: a neighbour that fills an entry module.
FEEDING_AN_ENTRY = Topology(
    modules=[
        Module(
            id="Module3",
            label="Module 3",
            types=["EntryModule"],
            connections=[Connection(direction="East", to="Module1")],
        ),
        Module(
            id="Module1",
            label="Module 1",
            types=["EntryModule"],
            connections=[Connection(direction="East", to="Module2")],
        ),
        Module(id="Module2", label="Module 2", types=["ExitModule"]),
    ]
)

# The bound of a reservation for the WMS's box, counted from the origin's answer to Reserve, and
# the time of one read of the shared remote store after it.
SPAWN_BOUND_SECONDS = 30.0
ONE_READ_SECONDS = 10.0

# The message of the rule that allows a module one box at a time.
POSSESSION_MESSAGE = "A FlexConveyorModule may possess at most one Box at a time."


def test_a_spawn_onto_an_origin_a_skip_reserve_neighbour_filled_is_refused_by_the_store(
    dataset, records, monkeypatch
):
    """Module 1 accepts the WMS's Reserve for Box 2. Before the WMS's create, Module 3, with
    `skip-reserve` armed, hands Box 1 onto Module 1 without asking, which the store admits, since
    Module 1 holds nothing; Module 3's move of Box 1 waits until the test lets it end, so Module 1
    holds Box 1 meanwhile. The WMS's create of Box 2 on Module 1 is refused by the store: nothing
    is written, and "Last spawns" on the WMS's dashboard shows the store's reason, which the
    monitor shows too. Box 1 then goes on, and Module 1's reservation for Box 2 lapses at its
    bound. Deterministic: the WMS's call of Reserve waits,
    after the answer, until Module 1 holds Box 1. "Box 2" is the box the WMS spawns, named by a
    UUID as it names every box: the test learns its name from the WMS's Reserve.
    """
    box_1, transport_1 = box_iri(1), transport_iri(box_iri(1))
    box_1_may_go_on = threading.Event()
    served: list[Participants] = []
    # The box of the WMS's Reserve: Box 2.
    asked_for: list[IRI] = []

    def module_3_moves(box: object, receiver: object) -> None:
        box_1_may_go_on.wait(HELD_AT_MOST_SECONDS)

    def box_1_on_module_3(db: TripleStore) -> None:
        place_box(OGM(db=db), box_1, on=M3, origin=M3, destination=M2)

    call_peer = wms_process.call_peer

    def the_wms_calls(endpoint: str, request, timeout: float = STEP_TIMEOUT_SECONDS):
        answer = call_peer(endpoint, request, timeout)
        if isinstance(request, ReserveRequest) and answer["accepted"]:
            asked_for.append(IRI(request.box))
            (party,) = served
            # As the WMS notifies the origin of a transport: Module 3 carries Box 1 on.
            party.served[WMS].middleware.notify(operation=transport_1, resource=M3)
            eventually(
                lambda: boxes_held_by(party.db, M1) == [box_1],
                timeout=ARRIVES_WITHIN_SECONDS,
            )
        return answer

    # Before the WMS is built: it takes its call when it is built.
    monkeypatch.setattr(wms_process, "call_peer", the_wms_calls)
    with (
        participants(
            dataset,
            FEEDING_AN_ENTRY,
            moves={M3: module_3_moves},
            before_start=box_1_on_module_3,
        ) as party,
        monitoring(dataset) as (memory, client),
    ):
        served.append(party)
        eventually(
            lambda: (
                WMS in memory.following()
                and capability(party.db, M3, FC.BoxTransportCapability) is not None
            ),
            timeout=FOLLOWED_WITHIN_SECONDS,
        )
        # Setup: the seed names no capability, since a module's comes with its registration.
        # The transport implements Module 3's, as the WMS's create writes it.
        m3_capability = capability(party.db, M3, FC.BoxTransportCapability)
        assert party.db.triple_add(
            (transport_1, CFC.implementsCapability, m3_capability)
        )
        set_fault(party.db, M3, "skip-reserve", armed=True)

        spawned = party.spawn(origin=str(M1), destination=str(M2))

        [box_2] = asked_for
        reason = f"the triple store refused creating {box_2.fragment} on Module1"
        assert (spawned["accepted"], spawned["reason"]) == (False, reason)
        assert (spawned["box"], spawned["operation"]) == (None, None)
        rule = ValidationResult(
            focus=str(M1),
            path=POSSESSION_PATH,
            shape=str(FC.FlexConveyorModulePossessionShape),
        )
        assert rule in validation_results(spawned["report"])
        # Nothing is written: no Box 2 and no transport of it.
        assert boxes(party.db) == [box_1]
        assert operation_status(party.db, transport_iri(box_2)) is None
        assert boxes_held_by(party.db, M1) == [box_1]
        # "Last spawns" reads why: the rule the store names, and what it says.
        address = service_address(party.db, WMS)
        assert address is not None
        last_spawns = httpx.get(
            f"{address.rstrip('/')}/dashboard/box/last-spawns", timeout=10
        )
        last_spawns.raise_for_status()
        [(_, box, way, outcome, why)] = rows(last_spawns.text)
        assert (box, way, outcome) == ("—", "Module1 → Module2", "refused")
        assert why == (
            f"{reason}, by FlexConveyorModulePossessionShape at Module1: {POSSESSION_MESSAGE}"
        )
        # The WMS's own pages, and the monitor, show it as any refused write.
        assert [
            (write.verb, write.rules)
            for write in party.served[WMS].middleware.refused_writes
        ] == [("create", ("FlexConveyorModulePossessionShape at Module1",))]
        event = refused(stream_of(party, WMS), box_2)
        assert (event.peer, event.transport) == (str(M1), None)
        shown = shown_in_time(client, event)
        assert (shown["giver"], shown["receiver"]) == (
            "WarehouseManagementSystem1",
            "Module1",
        )
        assert (
            POSSESSION_MESSAGE,
            "Module1",
            str(FC.FlexConveyorModulePossessionShape),
        ) in readable(shown)

        box_1_may_go_on.set()

        arrives(records, box_1, transport_1)
        freed = (
            f"Module1: {box_2.fragment} did not come from WarehouseManagementSystem1 within "
            "30 s, so it frees its reservation"
        )
        eventually(
            lambda: freed in records(), timeout=SPAWN_BOUND_SECONDS + ONE_READ_SECONDS
        )
        assert boxes(party.db) == [box_1]
