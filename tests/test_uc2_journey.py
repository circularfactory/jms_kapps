"""A box's journey, with the WarehouseManagementSystem and two modules as middleware instances.

Each test seeds Module 1, the only entry module, connected east to Module 2, the only exit
module, and serves the three parties in this process (`_participants`), with a transit time of 0.
Every call between them goes over REST, every notification through the middleware, and every
write through the middleware into the store.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import logging
import threading
from uuid import UUID

import pytest
from _bulk import bulk_validation_report, conforms
from _explicit import explicit_links
from _participants import M1, M2, TWO_MODULES, participants
from _wait import eventually
from kapps_ogm import OGM
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import validation_results
from kapps_semantic_middleware.vocabulary import CFC, OperationStatus
from kapps_triplestore_interface import IRI, ShaclValidationError

from jms_kapps.common.triplestore import bindings
from jms_kapps.uc2 import process, wms_process
from jms_kapps.uc2.discovery import boxes_held_by, capability, workflow_endpoint
from jms_kapps.uc2.handshake import (
    ReceiveRequest,
    ReserveRequest,
    acceptance_record,
    reserved_record,
)
from jms_kapps.uc2.seed import place_box
from jms_kapps.uc2.topology import Connection, Module, Topology
from jms_kapps.uc2.transport import (
    TransportRecords,
    boxes,
    execution_result,
    operation_status,
    status_record,
)
from jms_kapps.uc2.vocabulary import (
    FC,
    FC_NS,
    FCI_NS,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
)
from jms_kapps.uc2.wms import spawned_record, taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

B1 = box_iri(1)

# The term the UC2 ontology once had for a Convey workflow, taken out since.
CONVEY_WORKFLOW = IRI("ConveyWorkflow", base=FC_NS)

# A journey of one hop takes well under a second on a local store; the shared remote store is slower.
JOURNEY_SECONDS = 30.0


@pytest.fixture
def records(caplog):
    """Every record the parties log, as `<logger>:<message>`, in the order they logged them."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    def records() -> list[str]:
        return [
            r.getMessage() for r in caplog.records if r.name.startswith("jms_kapps")
        ]

    return records


def in_order(records: list[str], expected: list[str]) -> bool:
    """Whether `expected` occur in `records` in this order, other records between them."""
    found = iter(records)
    return all(any(record == wanted for record in found) for wanted in expected)


def test_a_box_goes_from_the_wms_over_one_hop_and_back_through_queued_running_and_done(
    dataset, records
):
    """By the parties' own records (§5.2): the hop as the code makes it. Module 2 accepts Module 1's Reserve, then its Receive, and carries the box on to
    the WMS, which takes it back done; the store holds a graph that breaks no rule."""
    with participants(dataset) as party:
        spawned = party.spawn()

        assert spawned["accepted"], spawned
        box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
        # The spawn's opening links the box to its transport and back, both written.
        assert explicit_links(party.db, box, transport) == (
            [str(transport)],
            [str(box)],
        )
        taken_back = taken_back_record(WMS, box, transport)
        eventually(lambda: taken_back in records(), timeout=JOURNEY_SECONDS)

        assert operation_status(party.db, transport) == OperationStatus.DONE
        assert boxes_held_by(party.db, WMS) == [box]
        assert in_order(
            records(),
            [
                # The WMS asks the origin's Reserve, creates the box on it and notifies it.
                acceptance_record(M1, FC.ReserveWorkflow, box, WMS),
                status_record(WMS, transport, OperationStatus.QUEUED),
                status_record(M1, transport, OperationStatus.RUNNING),
                acceptance_record(M2, FC.ReserveWorkflow, box, M1),
                acceptance_record(M2, FC.ReceiveWorkflow, box, M1),
                status_record(M2, transport, OperationStatus.DONE),
                taken_back,
            ],
        ), "\n".join(records())
        report = bulk_validation_report(party.db)
        assert conforms(report), report.serialize()


class _Nested(threading.local):
    """How deep the current thread is inside the bodies of `mw.create()`."""

    depth = 0


class _CountedCreation:
    """A creation of the middleware, which says on its thread how deep its body is."""

    def __init__(self, creation, nested: _Nested) -> None:
        self._creation, self._nested = creation, nested

    def __enter__(self):
        entered = self._creation.__enter__()
        self._nested.depth += 1
        return entered

    def __exit__(self, *raised):
        self._nested.depth -= 1
        return self._creation.__exit__(*raised)


class Exchanges:
    """What the parties served in this process send each other, in the order they send it: each
    call a party makes of a peer's workflow, as ("call", the calling party, its request's class,
    the endpoint), the class of the plain Reserve of a look-ahead as "ReserveRequest (plain)",
    each commit that a party asks of its middleware, a handover's and a create's
    included, as ("commit", its resource), and each notification, as ("notify", the notifying
    resource, the notified one)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: list[tuple] = []
        self._lock = threading.Lock()
        call_peer = process.call_peer

        def call(endpoint: str, request, timeout: float = process.STEP_TIMEOUT_SECONDS):
            # A module calls Reserve and Receive as the giver, and the WMS the origin's Reserve.
            assert isinstance(request, (ReserveRequest, ReceiveRequest)), request
            caller = request.source_module
            kind = type(request).__name__
            if isinstance(request, ReserveRequest) and request.plain:
                kind += " (plain)"
            self._add(("call", IRI(caller), kind, endpoint))
            return call_peer(endpoint, request, timeout)

        commit = SemanticMiddleware.commit
        create, notify = SemanticMiddleware.create, SemanticMiddleware.notify
        nested = _Nested()

        # A commit is counted where a party asks for it, at the middleware's public calls: each
        # `mw.commit()` is one, and so is each `possession.handover(mw, ...)`, whose entry is a
        # `mw.commit(verb="handover")` that commits the change of possession with the changes it is
        # given, and each `mw.create()` that is not inside the body of another, whose write it
        # joins.
        def committing(middleware, *args, **kwargs):
            self._add(("commit", IRI(str(middleware.resource))))
            return commit(middleware, *args, **kwargs)

        def creating(middleware, *args, **kwargs):
            if nested.depth == 0:
                self._add(("commit", IRI(str(middleware.resource))))
            return _CountedCreation(create(middleware, *args, **kwargs), nested)

        def notifying(middleware, *, operation, resource, **kwargs):
            self._add(("notify", IRI(str(middleware.resource)), IRI(str(resource))))
            return notify(middleware, operation=operation, resource=resource, **kwargs)

        # Before the parties are built: a party takes its call when it is built.
        monkeypatch.setattr(process, "call_peer", call)
        monkeypatch.setattr(wms_process, "call_peer", call)
        monkeypatch.setattr(SemanticMiddleware, "commit", committing)
        monkeypatch.setattr(SemanticMiddleware, "create", creating)
        monkeypatch.setattr(SemanticMiddleware, "notify", notifying)

    def _add(self, seen: tuple) -> None:
        with self._lock:
            self.seen.append(seen)

    def between(self, first: tuple, last: tuple) -> list[tuple]:
        """What was sent after `first` and before `last`."""
        with self._lock:
            seen = list(self.seen)
        return seen[seen.index(first) + 1 : seen.index(last)]

    def of(self, kind: str) -> list[tuple]:
        with self._lock:
            return [seen for seen in self.seen if seen[0] == kind]


def test_a_spawn_and_a_hop_each_make_one_commit_between_their_calls(
    dataset, records, monkeypatch
):
    """A party finds a peer's workflow (§5.2) in the graph and
    calls it directly. Every party serves on a port that the system gave it at its start, so no
    address exists anywhere but in the graph, and each call goes to the endpoint that the graph
    names for the peer's workflow of its class. The hop as the code makes it: the giver asks the receiver's Reserve, makes one commit, the handover, conveys
    the box, its own move, and calls the receiver's Receive; no party exposes a Convey.

    The spawn: the WMS asks Module 1's Reserve, makes one commit, which creates the box on
    Module 1 with its transport, and notifies Module 1. The
    hop: Module 1 asks Module 2's Reserve, commits the handover, conveys the box, and calls
    Module 2's Receive; Module 2 carries the box on from there, and gives it back. Module 2 calls
    nothing of Module 1, no party calls a workflow of the WMS, and no notification passes between
    the two modules. The spawn's box starts its journey once. Module 1
    looks ahead before it answers the WMS: it asks Module 2's plain Reserve for the box."""
    exchanges = Exchanges(monkeypatch)
    with participants(dataset) as party:
        # Nobody exposes a Convey.
        for resource in (M1, M2, WMS):
            assert workflow_endpoint(party.db, resource, CONVEY_WORKFLOW) is None
        origin_reserve = workflow_endpoint(party.db, M1, FC.ReserveWorkflow)
        reserve = workflow_endpoint(party.db, M2, FC.ReserveWorkflow)
        receive = workflow_endpoint(party.db, M2, FC.ReceiveWorkflow)
        spawned = party.spawn()

        assert spawned["accepted"], spawned
        box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
        taken_back = taken_back_record(WMS, box, transport)
        eventually(lambda: taken_back in records(), timeout=JOURNEY_SECONDS)

        spawn_asked = ("call", WMS, "ReserveRequest", origin_reserve)
        looked_ahead = ("call", M1, "ReserveRequest (plain)", reserve)
        origin_notified = ("notify", WMS, M1)
        asked = ("call", M1, "ReserveRequest", reserve)
        received = ("call", M1, "ReceiveRequest", receive)
        assert exchanges.of("call") == [spawn_asked, looked_ahead, asked, received]
        assert exchanges.between(spawn_asked, origin_notified) == [
            looked_ahead,
            ("commit", WMS),
        ]
        assert exchanges.between(asked, received) == [("commit", M1)]
        assert exchanges.of("notify") == [origin_notified, ("notify", M2, WMS)]
        assert operation_status(party.db, transport) == OperationStatus.DONE
        log = records()
        assert log.count(status_record(M1, transport, OperationStatus.RUNNING)) == 1
        assert log.count(reserved_record(M1, M2, box)) == 1
        assert log.count(status_record(M2, transport, OperationStatus.DONE)) == 1
        assert log.count(spawned_record(WMS, box, M1, M2)) == 1


def box_on_module_1(db) -> None:
    """Setup before any party starts: Box 1 on Module 1, its transport running to Module 2."""
    place_box(OGM(db=db), B1, on=M1, origin=M1, destination=M2)


def test_a_spawn_onto_an_occupied_origin_is_refused_by_its_reserve_and_writes_nothing(
    dataset,
):
    """The WMS asks the origin's Reserve before it writes anything."""
    with participants(dataset, before_start=box_on_module_1) as party:
        spawned = party.spawn(origin=str(M1), destination=str(M2))

        assert (spawned["accepted"], spawned["reason"], spawned["report"]) == (
            False,
            "Module1 refused to reserve: occupied by Box1",
            None,
        )
        assert (spawned["box"], spawned["operation"]) == (None, None)
        assert boxes(party.db) == [B1]
        assert boxes_held_by(party.db, WMS) == []
        assert boxes_held_by(party.db, M1) == [B1]
        report = bulk_validation_report(party.db)
        assert conforms(report), report.serialize()


def test_with_no_free_entry_module_the_wms_spawns_nothing_and_says_why(dataset):
    with participants(dataset, before_start=box_on_module_1) as party:
        spawned = party.spawn()

        assert not spawned["accepted"]
        assert spawned["reason"] == "no entry module is free"
        assert boxes(party.db) == [B1]


M3 = module_iri(3)

# The two modules, and Module 3, a second entry module, with no connection at all.
STRANDED_ENTRY = Topology(
    modules=[
        *TWO_MODULES.modules,
        Module(id="Module3", label="Module 3", types=["EntryModule"]),
    ]
)

# The two modules, and Module 3, which Module 1 also leads to and which has no connection on: a
# dead end on the way to Module 2. Module 1 reaches Module 2, so a transport from Module 1 to
# Module 2 is one the shapes admit, wherever its box is.
DEAD_END = Topology(
    modules=[
        Module(
            id="Module1",
            label="Module 1",
            types=["EntryModule"],
            connections=[
                Connection(direction="East", to="Module2"),
                Connection(direction="South", to="Module3", port=1),
            ],
        ),
        Module(id="Module2", label="Module 2", types=["ExitModule"]),
        Module(id="Module3", label="Module 3"),
    ]
)


def assigned_to(db, transport: IRI) -> list[IRI]:
    """The modules `transport` is assigned to (`cfc:executedOn`): one, read from the graph."""
    rows = bindings(
        db, f"SELECT ?module WHERE {{ <{transport}> <{CFC.executedOn}> ?module }}"
    )
    return [IRI(str(row["module"])) for row in rows]


def test_a_spawn_onto_an_origin_with_no_route_to_the_destination_is_refused_by_its_look_ahead(
    dataset,
):
    """Module 3 has no connection to Module 2: its look-ahead finds no next module, so it refuses
    the WMS's Reserve, and nothing is written.

    The WMS now refuses such a spawn itself, before it asks the origin, since Module 2 cannot be
    reached from Module 3; the look-ahead still refuses the WMS's Reserve, which is asked here as
    the WMS asks it. The test asks the Reserve itself, so that the look-ahead's refusal stays
    proven."""
    with participants(dataset, STRANDED_ENTRY) as party:
        spawned = party.spawn(origin=str(M3), destination=str(M2))
        reserve = workflow_endpoint(party.db, M3, FC.ReserveWorkflow)
        assert reserve is not None
        asked_as_the_wms_asks = process.call_peer(
            reserve,
            ReserveRequest(box=str(B1), source_module=str(WMS), destination=str(M2)),
        )

        assert (spawned["accepted"], spawned["reason"]) == (
            False,
            "Module2 cannot be reached from Module3",
        )
        assert (
            asked_as_the_wms_asks["accepted"],
            asked_as_the_wms_asks["reason"],
        ) == (False, "Module3 has no route to Module2")
        assert boxes(party.db) == []


def box_stranded_on_module_3(db) -> None:
    """Setup before any party starts: Box 1 on Module 3, its transport running from Module 1 to
    Module 2, as a hop from Module 1 leaves it. Module 3 has no connection on, so it has no route
    to Module 2."""
    place_box(OGM(db=db), B1, on=M3, origin=M1, destination=M2)


def test_a_module_that_cannot_go_on_fails_the_transport_and_notifies_the_wms(
    dataset, records
):
    """Box 1 is on Module 3, a dead end that Module 1 leads to, with no connection to Module 2, its
    destination: once Module 3 carries it on, its transport fails with the reason, and the box
    stays there, its transport assigned to Module 3. Module 3 starts on a Receive from Module 1, as
    at every module after the origin. The WMS spawns no box onto such an origin, so the seed puts
    Box 1 there. The seeded transport starts at Module 1, which reaches Module 2, so the shapes,
    the reachable-destination rule among them, admit it."""
    with participants(
        dataset, DEAD_END, before_start=box_stranded_on_module_3
    ) as party:
        transport = transport_iri(B1)
        receive = workflow_endpoint(party.db, M3, FC.ReceiveWorkflow)
        assert receive is not None
        answer = process.call_peer(
            receive, ReceiveRequest(box=str(B1), source_module=str(M1))
        )
        assert answer["accepted"], answer
        notified = (
            "WarehouseManagementSystem1: Box1_transport failed: "
            "Module3 has no route to Module2"
        )
        eventually(lambda: notified in records(), timeout=JOURNEY_SECONDS)

        assert operation_status(party.db, transport) == OperationStatus.FAILED
        assert (
            execution_result(party.db, transport) == "Module3 has no route to Module2"
        )
        assert boxes_held_by(party.db, M3) == [B1]
        assert assigned_to(party.db, transport) == [M3]
        assert in_order(
            records(),
            [status_record(M3, transport, OperationStatus.FAILED), notified],
        ), "\n".join(records())
        report = bulk_validation_report(party.db)
        assert conforms(report), report.serialize()


def test_a_spawn_toward_a_module_that_is_not_an_exit_module_is_refused_and_writes_nothing(
    dataset,
):
    with participants(dataset) as party:
        spawned = party.spawn(origin=str(M1), destination=str(WMS))

        assert (spawned["accepted"], spawned["reason"], spawned["report"]) == (
            False,
            "WarehouseManagementSystem1 is not an exit module",
            None,
        )
        assert boxes(party.db) == []


def test_a_transport_the_store_refuses_to_open_writes_nothing(dataset):
    """The WMS is no module, so a transport toward it breaks the transport's rule. The WMS
    refuses such a spawn before it writes, so its own records open the transport here."""
    with participants(dataset) as party:
        records = TransportRecords(party.served[WMS].middleware)
        transport_capability = capability(party.db, M1, FC.BoxTransportCapability)
        assert transport_capability is not None

        with pytest.raises(ShaclValidationError) as refused:
            records.open(
                box_iri(1),
                origin=M1,
                destination=WMS,
                capability=transport_capability,
                possessor=M1,
            )

        assert any(
            found.focus == str(transport_iri(box_iri(1)))
            for found in validation_results(refused.value.report)
        ), refused.value.report
        assert boxes(party.db) == []


def test_two_boxes_go_one_after_the_other_through_the_same_modules(dataset, records):
    """The origin runs one notified transport at a time, and its BoxTransportWorkflow waits on
    the next module's Reserve and Receive, which answer at once; the next module carries the box
    on from its Receive, on a thread of its own. So no call waits on the worker it runs beside:
    the second box finds every worker free again. The WMS names each box by a UUID of its own,
    `Box-<UUID>`."""
    spawned_boxes: list[IRI] = []
    with participants(dataset) as party:
        for _ in (1, 2):
            spawned = party.spawn()
            assert spawned["accepted"], spawned
            box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
            taken_back = taken_back_record(WMS, box, transport)
            eventually(
                lambda record=taken_back: record in records(), timeout=JOURNEY_SECONDS
            )
            assert str(box).startswith(f"{FCI_NS}Box-")
            name = str(box).removeprefix(f"{FCI_NS}Box-")
            assert str(UUID(name)) == name, box
            spawned_boxes.append(box)

        assert spawned_boxes[0] != spawned_boxes[1]
        held = boxes_held_by(party.db, WMS)
        assert len(held) == 2 and set(held) == set(spawned_boxes), held
        report = bulk_validation_report(party.db)
        assert conforms(report), report.serialize()
