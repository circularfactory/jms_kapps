"""A FlexConveyor station without a network or a triple store, for use case 2's handshake tests.

`World` is the graph: who possesses which box, the transports and the module each is assigned
to, and the connections. A box links to its transport, so `Reads` finds a box's transport
through the box. `Reads` answers one party's reads from it and keeps each, and `Records`
makes a party's writes of a transport into it. `MiddlewareHandover` switches possession in it at
its entry, as the middleware's handover commits the change before its body runs, together with the
changes of its `entry` and the transport it completes `done`. `Network` delivers a call to a peer's
workflow straight to the peer's method, as JSON would. `Tasks` holds the work a party starts as
its own, after it answered, until the test runs it, and `Starts` runs it at once unless told to
hold it there. `Timers` holds the work a party sets to run after some seconds, such as the bound
on a reservation, until the test lets the time pass. `Waits` keeps each wait before a Reserve
is asked again, and runs what the test says happens meanwhile. `Notifications` keeps every
notification, and hands it as a task to the receiver's BoxTransportWorkflow or handler, with the
transport as the graph holds it: the least of the middleware's intake. `Moves` keeps the boxes moved.
"""

from __future__ import annotations

import contextlib
import itertools
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from kapps_semantic_middleware.errors import (
    HandoverPreconditionError,
    NotificationUndelivered,
)
from kapps_semantic_middleware.vocabulary import CFC, OperationStatus
from kapps_triplestore_interface import IRI, ShaclValidationError

from jms_kapps.uc2.discovery import DirectedConnection, ModulesAndConnections
from jms_kapps.uc2.topology import MODULE_TYPES, Direction, ModuleType
from jms_kapps.uc2.transport import Transport, failure
from jms_kapps.uc2.vocabulary import FC, FC_NS, transport_iri, uuid_box_iri


def spawned_box(n: int) -> IRI:
    """The `n`-th box that a test's WarehouseManagementSystem spawns with `BoxNames`: named by a
    UUID, as the WarehouseManagementSystem names every box it spawns, but one made from `n`, so
    that the test knows it beforehand. (Added on 2026-10-12, jms_kapps 92: until then a test's
    WarehouseManagementSystem numbered its boxes Box 1, Box 2 and on.)"""
    return uuid_box_iri(UUID(int=n))


class BoxNames:
    """Names the boxes a test's WarehouseManagementSystem spawns, in the order it spawns them:
    `spawned_box(1)`, `spawned_box(2)` and on."""

    def __init__(self) -> None:
        self._numbers = itertools.count(1)

    def __call__(self) -> IRI:
        return spawned_box(next(self._numbers))


def capability_of(module: IRI) -> IRI:
    """The BoxTransportCapability that `World` gives `module` unless a test takes it away."""
    return IRI(f"{module}_capability_box_transport")


@dataclass
class Job:
    """One transport as `World` holds it."""

    box: IRI
    origin: IRI
    destination: IRI
    status: str = OperationStatus.RUNNING
    result: str | None = None
    assigned: IRI | None = None
    capability: IRI | None = None


class World:
    def __init__(self) -> None:
        self.held: dict[IRI, list[IRI]] = defaultdict(list)
        self.jobs: dict[IRI, Job] = {}
        self.connections: set[tuple[IRI, IRI]] = set()
        # The direction of a connection, by (the module that has it, the module it leads to).
        self.directions: dict[tuple[IRI, IRI], Direction] = {}
        self.wms_iri: IRI | None = None
        self.classes: dict[IRI, list[IRI]] = defaultdict(list)
        # Modules whose BoxTransportCapability is missing: they never registered.
        self.unregistered_capabilities: set[IRI] = set()
        # Resources whose workflows the graph does not name.
        self.unregistered: set[IRI] = set()
        # Every read a party made: (party, read, its arguments).
        self.reads: list[tuple[IRI, str, tuple]] = []

    def possessor_of(self, box: IRI) -> IRI | None:
        return next((r for r, boxes in self.held.items() if box in boxes), None)

    def place(
        self,
        box: IRI,
        on: IRI,
        *,
        origin: IRI | None = None,
        destination: IRI | None = None,
        status: str = OperationStatus.RUNNING,
        operation: IRI | None = None,
    ) -> IRI:
        """Put `box` on `on`, with its transport from `origin` to `destination`, assigned to
        `on`. The transport is `operation` if given, and otherwise has the IRI that the
        WarehouseManagementSystem's opening gives it."""
        self.held[on].append(box)
        operation = operation or transport_iri(box)
        self.jobs[operation] = Job(
            box, origin or on, destination or on, status, assigned=on
        )
        return operation

    def transport(self, operation: IRI) -> Transport | None:
        job = self.jobs.get(operation)
        if job is None:
            return None
        return Transport(
            operation,
            job.box,
            job.origin,
            job.destination,
            job.status,
            assigned=job.assigned,
            result=job.result,
        )


class Reads:
    """The reads of the party `reader` from `world`. Each is kept in `world.reads`."""

    def __init__(self, world: World, reader: IRI) -> None:
        self.world, self.reader = world, reader

    def _read(self, name: str, *args) -> None:
        self.world.reads.append((self.reader, name, args))

    def boxes_held_by(self, resource: IRI) -> list[IRI]:
        self._read("boxes_held_by", resource)
        return list(self.world.held[resource])

    def holds(self, resource: IRI, box: IRI) -> bool:
        """Whether `resource` possesses `box`: the store's `ASK` about that one box."""
        self._read("holds", resource, box)
        return box in self.world.held[resource]

    def connections(self) -> list[DirectedConnection]:
        self._read("connections")
        return [
            DirectedConnection(
                source, target, self.world.directions.get((source, target))
            )
            for source, target in sorted(self.world.connections)
        ]

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None:
        if resource in self.world.unregistered:
            return None
        return f"{resource}|{workflow_class}"

    def wms(self) -> IRI | None:
        self._read("wms")
        return self.world.wms_iri

    def modules_and_connections(self) -> ModulesAndConnections:
        """Every module `world.classes` puts in one of the three module classes, with those of
        its classes, in the order of their IRIs, and every connection with its direction, where
        `world.directions` names one, as the graph's one read gives them."""
        self._read("modules_and_connections")
        types: dict[IRI, list[ModuleType]] = {}
        for module_type in MODULE_TYPES:
            for module in self.world.classes[IRI(module_type, base=FC_NS)]:
                types.setdefault(module, []).append(module_type)
        return ModulesAndConnections(
            dict(sorted(types.items(), key=lambda item: str(item[0]))),
            sorted(self.world.connections),
            dict(self.world.directions),
        )

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None:
        self._read("capability", resource, capability_class)
        if capability_class != FC.BoxTransportCapability:
            return None
        if resource in self.world.unregistered_capabilities:
            return None
        return capability_of(resource)

    def transport_of(self, box: IRI) -> Transport | None:
        """The transport that names `box`, found through the box, as the box's link to its
        transport finds it: never by its IRI."""
        self._read("transport_of", box)
        return next(
            (
                self.world.transport(operation)
                for operation, job in self.world.jobs.items()
                if job.box == box
            ),
            None,
        )


def store_refusal(report: str) -> ShaclValidationError:
    """What the triplestore interface raises when the store's shapes refuse a write: the report,
    verbatim, beside the message that quotes it."""
    return ShaclValidationError(
        f"Error while querying GraphDB (500) - {report}", report=report
    )


class Records:
    """A party's writes of a box's transport, into `world`. Each is also kept in `writes`.

    `fails_with` holds the error the next open raises, `start_fails_with` the error the next
    start raises, `complete_fails_with` the error the next complete raises, and `fail_fails_with`
    the error the next fail raises, as a refusal by the store would: nothing is written then.
    """

    def __init__(self, world: World) -> None:
        self.world = world
        self.writes: list[tuple] = []
        self.fails_with: Exception | None = None
        self.start_fails_with: Exception | None = None
        self.complete_fails_with: Exception | None = None
        self.fail_fails_with: Exception | None = None

    def open(
        self,
        box: IRI,
        *,
        origin: IRI,
        destination: IRI,
        capability: IRI,
        possessor: IRI,
    ) -> IRI:
        if self.fails_with is not None:
            raise self.fails_with
        operation = transport_iri(box)
        self.writes.append(("open", box, origin, destination, capability, possessor))
        self.world.held[possessor].append(box)
        self.world.jobs[operation] = Job(
            box,
            origin,
            destination,
            OperationStatus.QUEUED,
            assigned=origin,
            capability=capability,
        )
        return operation

    def start(self, operation: IRI) -> None:
        if self.start_fails_with is not None:
            raise self.start_fails_with
        self.writes.append(("start", operation))
        self.world.jobs[operation].status = OperationStatus.RUNNING

    def complete(self, operation: IRI) -> None:
        if self.complete_fails_with is not None:
            raise self.complete_fails_with
        self.writes.append(("complete", operation))
        self.world.jobs[operation].status = OperationStatus.DONE

    def fail(self, operation: IRI, reason: str, report: str | None = None) -> None:
        if self.fail_fails_with is not None:
            raise self.fail_fails_with
        self.writes.append(("fail", operation, reason, report))
        self.world.jobs[operation].status = OperationStatus.FAILED
        self.world.jobs[operation].result = failure(reason, report)


class MiddlewareHandover:
    """The middleware's handover for `possessor`: the box moves in `world` at its entry.

    `fails_with` holds the errors the next entries raise, one per handover, as the store's
    refusal of the entry commit raises: nothing changes then, and the body does not run. `calls`
    keeps each handover's counterpart, the operation it completes, and its entry.
    """

    def __init__(self, world: World, possessor: IRI) -> None:
        self.world, self.possessor = world, possessor
        self.fails_with: list[Exception] = []
        self.calls: list[tuple[IRI, IRI | None, list]] = []

    @contextlib.contextmanager
    def __call__(
        self,
        *,
        mode,
        workpiece,
        counterpart,
        workpiece_update=None,
        operation=None,
        entry=None,
    ):
        self.calls.append((IRI(counterpart), operation, list(entry or [])))
        if workpiece not in self.world.held[self.possessor]:
            raise HandoverPreconditionError(
                f"{self.possessor} does not currently possess {workpiece}"
            )
        if self.fails_with:
            raise self.fails_with.pop(0)
        self.world.held[self.possessor].remove(workpiece)
        self.world.held[IRI(counterpart)].append(workpiece)
        if operation is not None:
            self.world.jobs[IRI(operation)].status = OperationStatus.DONE
        for change in entry or []:
            job = self.world.jobs[IRI(change.instance_iri)]
            for prop, values in change.data.items():
                value = IRI(values[0]["id"])
                if IRI(prop) == CFC.executedOn:
                    job.assigned = value
                elif IRI(prop) == CFC.implementsCapability:
                    job.capability = value
        yield


# The method of a peer that answers each workflow: a module's. A module's convey is its own move,
# which no peer calls, and the WarehouseManagementSystem answers no workflow of the station.
STEPS = {
    str(FC.ReserveWorkflow): "reserve",
    str(FC.ReceiveWorkflow): "receive",
}


class Network:
    """Peers by resource. A call goes straight to the peer's step method, as JSON would.

    `fails_with` holds, by peer, the error that every call to it raises, as the real call raises
    one when the peer's process is gone, does not answer in time, or answers with an error status.
    """

    def __init__(self) -> None:
        self.peers: dict[IRI, object] = {}
        self.calls: list[tuple[str, IRI]] = []
        self.fails_with: dict[IRI, Exception] = {}

    def call(self, endpoint: str, payload) -> dict:
        resource, workflow_class = endpoint.split("|")
        self.calls.append((IRI(workflow_class).fragment, IRI(resource)))
        if IRI(resource) in self.fails_with:
            raise self.fails_with[IRI(resource)]
        step = getattr(self.peers[IRI(resource)], STEPS[workflow_class])
        return step(type(payload).model_validate(payload.model_dump())).model_dump()


class Tasks:
    """The work the parties started, held until `run` runs it, and whatever that starts."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], object]] = []

    def start(self, work: Callable[[], object]) -> None:
        self.pending.append(work)

    def run(self) -> None:
        while self.pending:
            self.pending.pop(0)()


class Starts:
    """How a module starts its own work once it has answered, such as carrying a box on after
    its Receive: at once, as the module's thread would run it, or into `tasks` while `holding` is
    set, until the test runs it."""

    def __init__(self, tasks: Tasks) -> None:
        self.tasks = tasks
        self.holding = False

    def __call__(self, work: Callable[[], object]) -> None:
        if self.holding:
            self.tasks.start(work)
        else:
            work()


class Notifications:
    """Every notification sent, as (operation, resource), and what receives it.

    A resource in `receivers` gets the transport, as `world` holds it when the task runs, as a
    task of `tasks`: the middleware's intake answers at once and runs the workflow afterwards.
    `undelivered` makes every notification fail, as one to a resource with no live service does,
    and `undelivered_to` every notification of one resource, by resource: with why, or with the
    error the middleware raises.
    """

    def __init__(self, world: World, tasks: Tasks) -> None:
        self.world, self.tasks = world, tasks
        self.sent: list[tuple[IRI, IRI]] = []
        self.receivers: dict[IRI, Callable[[Transport], object]] = {}
        self.undelivered: str | None = None
        self.undelivered_to: dict[IRI, str | NotificationUndelivered] = {}

    def notify(self, operation: IRI, resource: IRI) -> None:
        if self.undelivered is not None:
            raise NotificationUndelivered(self.undelivered)
        operation, resource = IRI(operation), IRI(resource)
        if resource in self.undelivered_to:
            why = self.undelivered_to[resource]
            raise why if isinstance(why, Exception) else NotificationUndelivered(why)
        self.sent.append((operation, resource))
        receiver = self.receivers.get(resource)
        if receiver is not None:

            def receive() -> None:
                transport = self.world.transport(operation)
                assert transport is not None
                receiver(transport)

            self.tasks.start(receive)


class Timers:
    """The work a party set to run after some seconds, held until the test lets the time pass:
    `fire` runs each, in the order they were set, and whatever they set meanwhile. `seconds`
    says how long each was set for."""

    def __init__(self) -> None:
        self.pending: list[tuple[float, Callable[[], object]]] = []
        self.seconds: list[float] = []

    def later(self, seconds: float, work: Callable[[], object]) -> None:
        self.seconds.append(seconds)
        self.pending.append((seconds, work))

    def fire(self) -> None:
        while self.pending:
            self.pending.pop(0)[1]()


class Stop:
    """Whether the stop of a party's process has begun, as a test says: what the party is given
    to ask, in place of the stop signal."""

    def __init__(self) -> None:
        self.begun = False

    def has_begun(self) -> bool:
        return self.begun


class Moves:
    """Every move made, as (box, receiver), with who possessed the box while it moved.

    `meanwhile` holds what happens during each move, one per move, in order, such as a receiver
    whose process goes away.
    """

    def __init__(self, world: World) -> None:
        self.world = world
        self.moved: list[tuple[IRI, IRI, IRI | None]] = []
        self.meanwhile: list[Callable[[], object]] = []

    def move(self, box: IRI, receiver: IRI) -> None:
        self.moved.append((box, receiver, self.world.possessor_of(box)))
        if self.meanwhile:
            self.meanwhile.pop(0)()


class Waits:
    """Every wait a party makes before it asks again, in seconds, without waiting.

    `meanwhile` holds what happens during each wait, one per wait, in order, such as a module
    that frees itself. A party that waits more often than `limit` times fails the test, instead
    of asking for ever.
    """

    def __init__(self, limit: int = 20) -> None:
        self.waited: list[float] = []
        self.meanwhile: list[Callable[[], object]] = []
        self.limit = limit

    def wait(self, seconds: float) -> None:
        self.waited.append(seconds)
        assert len(self.waited) <= self.limit, f"waited {len(self.waited)} times"
        if self.meanwhile:
            self.meanwhile.pop(0)()
