"""The WarehouseManagementSystem: where a box's journey starts, and where it ends.

It stands for the production system level around the FlexConveyor station. It is an active
party, with control logic and no conveyor of its own: its own middleware instance with two
workflows, `:SpawnBoxWorkflow` and `:SetSpawnRateWorkflow`, a handler for the notifications it
gets, and a dashboard of its own. It owns the station's entry.

**Spawn.** **Roles are optional:** entry and exit are roles of a module, which a topology may
name, and one module may have both. Unless the request names them, the WarehouseManagementSystem
picks a random free entry module, read from the graph, as the box's origin, or a random free module
where the topology names no entry module; and a random exit module as its destination, or a random
module where it names no exit module. It never picks the origin as the destination. A named
origin must be an entry module, and a named destination an exit module, only where the topology
names that role; any two different modules otherwise. A spawn that names a module that may not
take its place is refused with why, such as "Module4 is not an entry module" or, where the
topology names no such role, "Module9 is not a module". If no such module is free, it spawns
nothing and answers why: "no entry module is free", or "no module is free". A box is named by a
new UUID (`vocabulary.new_box_iri`), so that more than one middleware can create boxes without a
shared number, and the spawn reads no box in the graph to name it.

**Only a reachable destination.** A destination is reachable from an origin where the connections
in the graph lead from the origin to it, each one way, as listed; the WarehouseManagementSystem
reads them with the modules, in the same read. Where it picks the destination, it picks only among
the destinations the origin reaches, and refuses the spawn with "no exit module can be reached from
Module1" where none is. Where it picks the origin, it picks only among the free origins that reach
the named destination, or else some destination, and refuses with "no free entry module can reach
Module4", or "... can reach an exit module", where free origins exist but none does. A named
destination that the named origin cannot reach is refused with "Module4 cannot be reached from
Module1". The reasons name the roles as the others do: "module" where the topology names no such
role. Where the origin has no other destination at all, the reason stays "the graph has no exit
module other than Module1".

**A strategy picks.** The picks above are those of `real`, the default WMS strategy
(`strategies`): where the request leaves an end to pick, the WarehouseManagementSystem's strategy
picks one pair among the candidates, the free origins with the destinations each reaches, and a
pair that is not a candidate refuses the spawn. The strategy is chosen at start in the Launcher,
for the WMS and for the modules, and `real` is the default.

**Spawns at once.** Spawns run at once, each on the thread of its request or of the spawn timer,
and the timer has no wait of its own (`spawn_rate`). At most as many spawns are under way at once,
of the spawn workflow and of the timer alike, as the topology has modules that may be an origin,
by the latest read of the modules that a spawn made; before the first read there is no cap. A
spawn asked for at the cap does not start, and is refused with "every origin has a spawn under
way". Concurrent spawns pick apart: the origin of a spawn under way is not free for another
spawn's pick, as a module that holds a box is not. The WMS spawns as fast as the hardware allows,
with no wait of its own.

**The picking lock covers only the slot bookkeeping.** A spawn reads the graph outside it: the
modules with the connections, and what each origin it may pick holds. Under it, it chooses its
origin and its destination in memory, from what it read and from the spawns in flight, and enters
itself in flight; it takes the lock again only to leave the flight. So a spawn that has ended frees
its slot without waiting for another spawn's reads, and concurrent spawns still pick apart. A read
can be stale: when another spawn left the flight after this spawn's reads began, its origin may
hold that spawn's box by now, so this spawn reads once more before it chooses. A Reserve that the
origin refuses still refuses the spawn with the origin's reason.

**Every spawn accounted for.** Each spawn asked for ends in exactly one record at INFO: the record
of the box spawned, or "spawn refused: <reason>", with its origin and destination where they are
known (`spawn_record`). A spawn counts at its answer, created once the store admitted its create:
one whose origin then cannot be notified is recorded as spawned, beside its warning. A spawn that
raises is recorded as refused with "the spawn failed: <error>", and its traceback is logged where
it was asked: by the spawn timer, or by the middleware for the workflow. Every answer, a refusal
at the cap included, goes into its memory. No box is lost silently: each spawn asked for is
either created or logged with its reason.

**The box is spawned directly on the origin.** The WarehouseManagementSystem asks the origin's
Reserve for the box, and writes nothing before the origin accepts. The Reserve names the box's
destination, and the origin accepts only once its own next module has accepted a plain Reserve for
the box at once, so no origin fills with a box that cannot move on; a box that waits for the
origin comes before the spawn. An origin
that holds a box, or has reserved itself for another, refuses, and the spawn is refused with the
origin's reason, whether the request named the origin or the WarehouseManagementSystem picked it:
no other module is tried, and the origin is not asked again. An origin that cannot be reached refuses the spawn
too, with why ("Module1 is not reachable: ..."). So a module that passes a box onto the origin at
the same moment, and asks the origin's Reserve too, is refused as reserved when the
WarehouseManagementSystem asked first, and waits its turn. Once the origin has accepted, one
`mw.create()` writes the box, its `:BoxTransportOperation` as `queued` and assigned to the origin,
the links between the two, and the box's first possession, by the origin. The store admits or
refuses it as one: if it refuses, as it does when a neighbour with `skip-reserve` armed has filled
the origin since its answer, nothing is written, the refusal is logged with its report and emitted
on the WarehouseManagementSystem's handover event stream, and the answer carries the report. Then
it notifies the origin of the transport: the origin's intake reads the transport and carries the
box on, and the notification uses the origin's reservation up. It logs its side, at INFO: the
origin's acceptance of its Reserve, the transport `queued`, and the spawn's one record: every
spawn ends in one record.

**An origin that cannot be notified.** The box is on the origin and the transport is assigned to
it, but nothing would start its BoxTransportWorkflow: the WarehouseManagementSystem ends the
transport `failed` with why ("Module1 is not reachable: ..."), as a module does that cannot reach
its next module, logs one warning, and the answer of the spawn says so. The origin leaves that
transport alone. So the WarehouseManagementSystem writes a transport only in the create that opens
it and when it cannot notify the origin.

**A stop.** Once the stop of its process has begun, it refuses every spawn, with the reason "the
system is stopping", and creates no box, neither for the spawn workflow nor for the spawn rate's
timer: it asks once more just before the create. A spawn already under way ends. If it cannot
notify the origin then, since every process of the run stops at once and the origin's server
takes no new connection, it ends the transport `failed` with the stop as its reason, "the system
is stopping", and its warning says why the origin was not told; the origin, which waits for the
notification at its own stop, writes nothing.

**The notification back.** The destination module notifies the WarehouseManagementSystem once it
has given the box back, and a module that cannot go on notifies it once it has recorded the
transport `failed`: the transport has ended. The WarehouseManagementSystem checks the transport it
is handed: it accepts a box it holds whose transport is done, and logs the acceptance as its
evidence. Whether it holds the box it asks the store in one `ASK` about that one box.

**What it knows.** It keeps in memory the answers to its newest spawns, the transports it opened
with their last known status, and the boxes it holds, from the notifications back (`wms_memory`).
Its dashboard renders that memory.
"""

from __future__ import annotations

import logging
import random
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI, ShaclValidationError
from pydantic import BaseModel, field_validator

from jms_kapps.uc2.discovery import ModulesAndConnections
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import (
    STOPPING,
    BoxTransportCapabilities,
    Call,
    Notify,
    PeerUnreachable,
    ReserveRequest,
    ReserveResult,
    StepResult,
    ask,
    no_box_transport_capability,
    not_reachable,
    reserved_record,
)
from jms_kapps.uc2.routing import reachable
from jms_kapps.uc2.strategies import REAL, REAL_WMS_STRATEGY, Choose, WmsStrategy
from jms_kapps.uc2.topology import Choice, choices, entries_and_exits
from jms_kapps.uc2.transport import Transport, status_record
from jms_kapps.uc2.validation_report import rules_clause
from jms_kapps.uc2.vocabulary import FC, local_name, new_box_iri
from jms_kapps.uc2.wms_memory import WmsMemory

logger = logging.getLogger(__name__)


class SpawnBoxRequest(BaseModel):
    """Where a new box enters the FlexConveyor station, and the module it is carried to. A module
    left out, or left empty, is picked: a free entry module as the origin, an exit module as the
    destination, and, for a role the topology does not name, any free module as the origin and
    any module but the origin as the destination. Empty is how a form says it: a select whose
    choice is the WarehouseManagementSystem's posts an empty value."""

    origin: str | None = None
    destination: str | None = None

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def _empty_names_none(cls, value: object) -> object:
        return None if value == "" else value


class SpawnBoxResult(StepResult):
    """The answer to a spawn. `origin` and `destination` name the modules it was given or picked,
    once there are both, and the ones the request named before that. `box` and `operation` name
    the box and its transport, once made."""

    origin: str | None = None
    destination: str | None = None
    box: str | None = None
    operation: str | None = None


class WmsReads(Protocol):
    """What the WarehouseManagementSystem reads. `discovery.GraphView` is the real one. None of it
    reads every box in the graph."""

    def boxes_held_by(self, resource: IRI) -> list[IRI]: ...

    def holds(self, resource: IRI, box: IRI) -> bool: ...

    def modules_and_connections(self) -> ModulesAndConnections: ...

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None: ...

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None: ...


class WmsWrites(Protocol):
    """What it writes. `transport.TransportRecords` is the real one."""

    def open(
        self,
        box: IRI,
        *,
        origin: IRI,
        destination: IRI,
        capability: IRI,
        possessor: IRI,
    ) -> IRI: ...

    def fail(self, operation: IRI, reason: str, report: str | None = None) -> None: ...


def seeded_choice(seed: int | None) -> Choose:
    """The chooser a WarehouseManagementSystem picks its modules with: `random.choice`'s shared
    generator where `seed` is None, else the choice of a generator of its own that `seed` seeds, so
    that the same seed and the same candidates give the same sequence of picks. The seed is set per
    repetition. Every pick is made under the picking lock, one
    at a time; concurrent spawns that reach it in another order draw the same numbers in that
    other order."""
    return random.choice if seed is None else random.Random(seed).choice


def create_refused(box: IRI, origin: IRI) -> str:
    """Why a spawn is refused when the store refused the create of `box` on `origin`: prose for a
    person, since a caller tells the store's refusal by the step result's report."""
    return f"the triple store refused creating {box.fragment} on {origin.fragment}"


class _NothingToSpawn(Exception):
    """No origin or no destination for a spawn, or one the request names that may not take the
    place it is named for: a module without the role, where the topology names that role, or no
    module. The message says why. Roles are optional."""


@dataclass(frozen=True)
class _PlannedSpawn:
    """A spawn of `box` onto `origin`, for `destination`, whose BoxTransportCapability is
    `capability`: what the spawn picked before it asks the origin's Reserve."""

    box: IRI
    origin: IRI
    destination: IRI
    capability: IRI

    def refused(
        self, reason: str, report: str | None = None, **made: str
    ) -> SpawnBoxResult:
        """The answer that the spawn is refused for `reason`, naming its modules and what it
        `made` (its box and its transport)."""
        return SpawnBoxResult(
            accepted=False,
            reason=reason,
            report=report,
            origin=str(self.origin),
            destination=str(self.destination),
            **made,
        )


@dataclass(frozen=True)
class _StationRead:
    """What a spawn read of the graph, outside the picking lock, before it chooses: the modules
    with their types and the connections (`station`), the modules that may be its origin and its
    destination, and `empty`, each origin that it read as holding no box. It reads what an origin
    holds only where it picks the origin, and not for an origin that was in flight when the read
    began."""

    station: ModulesAndConnections
    origins: Choice[IRI]
    destinations: Choice[IRI]
    empty: frozenset[IRI]


def _refused(request: SpawnBoxRequest, reason: str) -> SpawnBoxResult:
    """The answer that a spawn of `request` is refused for `reason`, before it has picked its
    modules: it names the modules the request names."""
    return SpawnBoxResult(
        accepted=False,
        reason=reason,
        origin=request.origin,
        destination=request.destination,
    )


def spawn_candidates(
    connections: Iterable[tuple[IRI, IRI]],
    origins: Sequence[IRI],
    destinations: Sequence[IRI],
) -> dict[IRI, list[IRI]]:
    """The candidates of a spawn: each of `origins` from which a path over `connections` leads to
    one of `destinations` other than itself, with those destinations, both in their order. An
    origin that reaches none is no candidate.

    The WarehouseManagementSystem picks its origin among these, and its destination among those of
    the origin, so that it picks only an exit it can reach from the origin, for every topology.
    Its WMS strategy picks the pair, and `real` picks so.
    """
    connections = list(connections)
    candidates: dict[IRI, list[IRI]] = {}
    for origin in origins:
        reached = reachable(connections, origin)
        ends = [d for d in destinations if d != origin and d in reached]
        if ends:
            candidates[origin] = ends
    return candidates


def _unreachable(
    request: SpawnBoxRequest, origins: Choice[IRI], destinations: Choice[IRI]
) -> str:
    """Why a spawn has no candidate (`spawn_candidates`), in the terms of the roles: the named
    origin reaches no destination, or no free origin reaches the named destination, or any.
    """
    if request.origin is not None and request.destination is not None:
        return (
            f"{local_name(request.destination)} cannot be reached from "
            f"{local_name(request.origin)}"
        )
    if request.origin is not None:
        return (
            f"no {destinations.name} can be reached from {local_name(request.origin)}"
        )
    if request.destination is not None:
        return f"no free {origins.name} can reach {local_name(request.destination)}"
    return f"no free {origins.name} can reach {destinations.with_article}"


def taken_back_record(resource: IRI, box: IRI, operation: IRI) -> str:
    """What the WarehouseManagementSystem `resource` logs when it takes `box` back: its evidence
    that the box is back in its possession and its transport `operation` is done."""
    return (
        f"{resource.fragment}: accepted {box.fragment} back, and {operation.fragment} "
        "is done"
    )


TAKEN_BACK = re.compile(
    r"(?:^|[\s:])(?P<resource>[^\s:]+): accepted (?P<box>\S+) back, and "
    r"(?P<operation>\S+) is done$"
)
"""A line of a log that holds a `taken_back_record`, whatever the log's format puts in front of it:
the WarehouseManagementSystem, the box and its transport by their fragments."""


def spawned_record(resource: IRI, box: IRI, origin: IRI, destination: IRI) -> str:
    """What the WarehouseManagementSystem `resource` logs once the store has admitted its create
    of `box` on `origin`, for `destination`: the record of the box spawned (`spawn_record`).
    A spawn counts at its answer: created when the store admitted its create, so the record does
    not wait for the notification."""
    return (
        f"{resource.fragment}: spawned {box.fragment} onto {origin.fragment}, "
        f"for {destination.fragment}"
    )


SPAWNED = re.compile(
    r"(?:^|[\s:])(?P<resource>[^\s:]+): spawned (?P<box>\S+) onto (?P<origin>\S+), "
    r"for (?P<destination>\S+)$"
)
"""A line of a log that holds a `spawned_record`, whatever the log's format puts in front of it:
the WarehouseManagementSystem, the box, its origin and its destination by their fragments."""


def refused_record(resource: IRI, refused: SpawnBoxResult) -> str:
    """What the WarehouseManagementSystem `resource` logs when it answers a spawn with the refusal
    `refused`: why, and the spawn's origin and destination where they are known.
    """
    known = [
        f"{end} {local_name(module)}"
        for end, module in (
            ("origin", refused.origin),
            ("destination", refused.destination),
        )
        if module is not None
    ]
    where = f" ({', '.join(known)})" if known else ""
    return f"{resource.fragment}: spawn refused: {refused.reason}{where}"


def spawn_record(resource: IRI, answer: SpawnBoxResult) -> str:
    """The one record the WarehouseManagementSystem `resource` logs for each spawn, at its
    `answer`: the box spawned, or the spawn refused with why.

    A spawn counts as created once the store admitted its create, which the answer shows by
    naming the box's transport: so a spawn whose origin then cannot be notified is recorded as
    spawned, while its answer says why it failed and a warning says that the origin was not
    told.
    """
    if answer.operation is None:
        return refused_record(resource, answer)
    # A spawn whose create the store admitted names its box, its origin and its destination.
    return spawned_record(
        resource,
        IRI(str(answer.box)),
        IRI(str(answer.origin)),
        IRI(str(answer.destination)),
    )


EVERY_ORIGIN_BUSY = "every origin has a spawn under way"
"""Why a spawn does not start while as many spawns are under way as there are origins."""


class _SpawnsUnderWay:
    """How many spawns are under way, of the spawn workflow and of the spawn timer alike, and the
    cap on them: as many as the modules that may be an origin, as the latest read of the modules
    that a spawn made found them. It reads nothing of its own, so before the first read there is
    no cap."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._cap: int | None = None

    def start(self) -> bool:
        """Count one more spawn under way, or answer False, and count none, at the cap."""
        with self._lock:
            if self._cap is not None and self._count >= self._cap:
                return False
            self._count += 1
            return True

    def end(self) -> None:
        """A spawn under way has ended."""
        with self._lock:
            self._count -= 1

    def cap_at(self, origins: int) -> None:
        """Cap the spawns under way at `origins`: the modules that may be an origin, as a spawn's
        read of the modules found them."""
        with self._lock:
            self._cap = origins


class WarehouseManagementSystem:
    """The WarehouseManagementSystem `resource`.

    Args:
        resource: The WarehouseManagementSystem.
        graph: The graph's answers.
        records: Its writes of a box and its transport.
        call: A blocking call to a module's workflow: the origin's Reserve.
        notify: The middleware's notification of a resource.
        choose: Picks a module out of several.
        events: Its handover event stream, where a create the store refused is emitted.
        stopping: Whether the stop of its process has begun.
        strategy: The WMS strategy that picks the origin and the destination of a spawn among
            its candidates, `real` unless a run chooses another (`strategies`).
        strategy_name: Its name, which the refusal of a pair it chose outside the candidates
            names.
        new_box: Names each box it spawns: a new UUID, unless a test names them otherwise.
    """

    def __init__(
        self,
        resource: IRI,
        *,
        graph: WmsReads,
        records: WmsWrites,
        call: Call,
        notify: Notify,
        choose: Choose = random.choice,
        events: HandoverEvents | None = None,
        stopping: Callable[[], bool] = lambda: False,
        strategy: WmsStrategy = REAL_WMS_STRATEGY,
        strategy_name: str = REAL,
        new_box: Callable[[], IRI] = new_box_iri,
    ) -> None:
        self.resource = IRI(resource)
        self.graph = graph
        self.records = records
        self.strategy = strategy
        self.strategy_name = strategy_name
        self.call = call
        self.notify = notify
        self._capabilities = BoxTransportCapabilities(graph)
        self.events = events
        self.choose = choose
        self._stopping = stopping
        # What it knows of its own work, which it keeps as it works.
        self.memory = WmsMemory()
        self._new_box = new_box
        # The spawns under way, and the cap on them.
        self._under_way = _SpawnsUnderWay()
        # The picking lock, which covers only the slot bookkeeping: `_in_flight`, the origin of
        # each spawn in flight, by its box, and `_left_flight`, how many spawns have left the
        # flight. A spawn chooses its modules under it, in memory, and enters itself in flight;
        # it never reads the store under it. The origin of a spawn in flight is not free for
        # another spawn's pick.
        self._picking = threading.Lock()
        self._in_flight: dict[IRI, IRI] = {}
        self._left_flight = 0

    def spawn(self, request: SpawnBoxRequest) -> SpawnBoxResult:
        """Ask the origin's Reserve for a new box; once the origin has accepted, create the box
        on it with its transport, and notify it.

        The answer is kept in its memory. So is a spawn that raised, as refused with the error as
        its reason, before the error goes on. Each answer is logged as one record at INFO
        (`spawn_record`): the box spawned, or the spawn refused with why, so that no spawn asked
        for, of the spawn workflow or of the spawn timer, is lost silently.

        **Spawns under way at once.** At most as many spawns are under way at once as the
        topology has modules that may be an origin, of the spawn workflow and of the spawn timer
        alike. A spawn asked for at that cap does not start, and is refused with "every origin
        has a spawn under way". The WMS spawns as fast as the hardware allows, with no wait of its
        own.
        """
        try:
            answer = self._spawn(request)
        except Exception as error:
            self._answered(
                SpawnBoxResult(
                    accepted=False,
                    reason=f"the spawn failed: {error}",
                    origin=request.origin,
                    destination=request.destination,
                )
            )
            raise
        self._answered(answer)
        return answer

    def _answered(self, answer: SpawnBoxResult) -> None:
        """Keep the `answer` to a spawn in memory, and log its one record."""
        self.memory.answered(answer)
        logger.info("%s", spawn_record(self.resource, answer))

    def _spawn(self, request: SpawnBoxRequest) -> SpawnBoxResult:
        # First, so that the stop is the reason, whatever else stands in the way.
        if self._stopping():
            return _refused(request, STOPPING)
        if not self._under_way.start():
            return _refused(request, EVERY_ORIGIN_BUSY)
        try:
            return self._pick_and_spawn(request)
        finally:
            self._under_way.end()

    def _pick_and_spawn(self, request: SpawnBoxRequest) -> SpawnBoxResult:
        picked = self._pick(request)
        if isinstance(picked, SpawnBoxResult):
            return picked
        box, origin, destination = picked
        try:
            # A store read the first time for each origin, outside the picking lock.
            capability = self._capabilities.of(origin)
            if capability is None:
                return SpawnBoxResult(
                    accepted=False,
                    reason=no_box_transport_capability(origin),
                    origin=str(origin),
                    destination=str(destination),
                )
            return self._onto_origin(
                _PlannedSpawn(box, origin, destination, capability)
            )
        finally:
            # Before the spawn counts as ended, so that a spawn under the cap finds its origin
            # free again.
            with self._picking:
                del self._in_flight[box]
                self._left_flight += 1

    def _pick(self, request: SpawnBoxRequest) -> tuple[IRI, IRI, IRI] | SpawnBoxResult:
        """The box, the origin and the destination of a spawn of `request`, entered in flight;
        or the answer that refuses the spawn.

        It reads the graph outside the picking lock, then chooses under it, in memory, from what
        it read and from the spawns in flight. When another spawn left the flight after its reads
        began, its origin may hold that spawn's box by now: so it reads once more, and only
        once, before it chooses.
        """
        read_again = True
        while True:
            with self._picking:
                began = self._left_flight
                in_flight = set(self._in_flight.values())
            read = self._read(request, in_flight)
            with self._picking:
                if read_again and self._left_flight != began:
                    read_again = False
                    continue
                # Again: the stop may have begun while the spawn read the graph.
                if self._stopping():
                    return _refused(request, STOPPING)
                try:
                    origin, destination = self._origin_and_destination(request, read)
                except _NothingToSpawn as nothing:
                    return _refused(request, str(nothing))
                box = self._new_box()
                self._in_flight[box] = origin
                return box, origin, destination

    def _onto_origin(self, spawn: _PlannedSpawn) -> SpawnBoxResult:
        """Ask the origin's Reserve for the box of `spawn`, create the box on the origin with its
        transport, and notify the origin. Nothing is written before the origin has accepted.

        The Reserve names the box's destination, so the origin looks ahead: it accepts only once
        its own next module has reserved itself for the box, and this spawn's Reserve waits
        behind every box that waits for the origin.

        A refusal of Reserve refuses the spawn with the origin's reason; an origin that cannot be
        reached, or whose answer is lost, refuses it with why, and a reservation the origin made
        lapses at its bound. Any other error of the call is raised.
        """
        box, origin, destination = spawn.box, spawn.origin, spawn.destination
        try:
            reserved = ask(
                self.graph,
                self.call,
                origin,
                FC.ReserveWorkflow,
                ReserveRequest(
                    box=str(box),
                    source_module=str(self.resource),
                    destination=str(destination),
                ),
                ReserveResult,
            )
        except PeerUnreachable as lost:
            return spawn.refused(str(lost))
        if not reserved.accepted:
            return spawn.refused(
                f"{origin.fragment} refused to reserve: {reserved.reason}",
                reserved.report,
            )
        logger.info("%s", reserved_record(self.resource, origin, box))
        # Again, as late as it can be asked: the reads and the origin's Reserve take a while on a
        # shared store, and the next step creates the box.
        if self._stopping():
            return spawn.refused(STOPPING)
        try:
            operation = self.records.open(
                box,
                origin=origin,
                destination=destination,
                capability=spawn.capability,
                possessor=origin,
            )
        except ShaclValidationError as refused:
            return self._create_refused(spawn, refused.report)
        logger.info(
            "%s", status_record(self.resource, operation, OperationStatus.QUEUED)
        )
        self.memory.opened(
            Transport(
                operation,
                box,
                origin,
                destination,
                OperationStatus.QUEUED,
                assigned=origin,
            )
        )
        made = {"box": str(box), "operation": str(operation)}
        try:
            self.notify(operation, origin)
        except NotificationUndelivered as error:
            # Nothing else would start the origin's BoxTransportWorkflow. Once the stop of this
            # process has begun, the stop is why: every process of the run stops at once, and
            # the origin's server takes no new connection.
            why = not_reachable(origin, error)
            if self._stopping():
                self._origin_lost(box, origin, operation, STOPPING, also=why)
                return spawn.refused(STOPPING, **made)
            self._origin_lost(box, origin, operation, why)
            return spawn.refused(why, **made)
        return SpawnBoxResult(
            accepted=True, origin=str(origin), destination=str(destination), **made
        )

    def _create_refused(self, spawn: _PlannedSpawn, report: str) -> SpawnBoxResult:
        """The store refused the create of the box of `spawn` on its origin, with `report`, and
        wrote nothing. Log the refusal with its report, emit it, and refuse the spawn with both."""
        reason = create_refused(spawn.box, spawn.origin)
        logger.warning(
            "%s: %s%s; the spawn is refused:\n%s",
            self.resource.fragment,
            reason,
            rules_clause(report),
            report,
        )
        if self.events is not None:
            self.events.commit_refused(
                box=spawn.box,
                peer=spawn.origin,
                transport=None,
                reason=reason,
                report=report,
            )
        return spawn.refused(reason, report)

    def _origin_lost(
        self,
        box: IRI,
        origin: IRI,
        operation: IRI,
        reason: str,
        *,
        also: str | None = None,
    ) -> None:
        """End `operation` failed with `reason`: this party created it and `box` on `origin`, and
        then could not notify `origin`, so nothing would carry the box on. One warning says it,
        and `also`, why the notification failed, when the reason is another."""
        logger.warning(
            "%s: %s is on %s: %s%s",
            self.resource.fragment,
            box.fragment,
            origin.fragment,
            reason,
            "" if also is None else f"; {also}",
        )
        self._fail(operation, reason)

    def transport_ended(self, transport: Transport) -> None:
        """The notification that `transport` has ended: accept a box it holds whose transport is
        done, and log a transport that failed.

        Only a transport that is done, or in a status no notification should bring, makes the
        WarehouseManagementSystem ask the store whether it holds the transport's box, in one
        `ASK` about that one box: a failed one costs nothing beyond the middleware's read of the
        transport.
        """
        operation = transport.operation
        if transport.status == OperationStatus.FAILED:
            # A transport fails on the station with its box on a module.
            self.memory.ended(transport, held=False)
            logger.warning(
                "%s: %s failed: %s",
                self.resource.fragment,
                operation.fragment,
                transport.result,
            )
            return
        held = self.graph.holds(self.resource, transport.box)
        self.memory.ended(transport, held=held)
        if transport.status == OperationStatus.DONE and held:
            logger.info(
                "%s", taken_back_record(self.resource, transport.box, operation)
            )
            return
        logger.warning(
            "%s: notified of %s, which is %s, %s %s",
            self.resource.fragment,
            operation.fragment,
            transport.status,
            "and it holds" if held else "and it does not hold",
            transport.box.fragment,
        )

    def origins_and_destinations(self) -> tuple[list[IRI], list[IRI]]:
        """The modules a spawn may name as its origin, and those it may name as its destination,
        read from the graph: the entry modules and the exit modules where the topology names
        them, and every module for a role it does not name. What the dashboard's form offers.
        Roles are optional."""
        return entries_and_exits(self.graph.modules_and_connections().types)

    def _read(self, request: SpawnBoxRequest, in_flight: set[IRI]) -> _StationRead:
        """What a spawn of `request` reads of the graph before it chooses, outside the picking
        lock: the modules with the connections, in one read, and, where it picks the origin, what
        each module that may be its origin holds, but for those in `in_flight`, the origins of
        the spawns in flight when the read began. Its answers do not grow with the boxes in the
        graph. They are the reads of `_origin_and_destination`, made outside the picking lock."""
        station = self.graph.modules_and_connections()
        origins, destinations = choices(station.types)
        self._under_way.cap_at(len(origins.modules))
        empty: frozenset[IRI] = frozenset()
        if request.origin is None:
            empty = frozenset(
                module
                for module in origins.modules
                if module not in in_flight and not self.graph.boxes_held_by(module)
            )
        return _StationRead(station, origins, destinations, empty)

    def _origin_and_destination(
        self, request: SpawnBoxRequest, read: _StationRead
    ) -> tuple[IRI, IRI]:
        """The modules the request names, or those picked for it, from what the spawn `read`, in
        memory: called under the picking lock. A picked origin is one it read as holding no box,
        and not the origin of a spawn in flight now. It reads nothing itself.

        **Roles are optional** (`topology.choices`). Where the topology names entry modules, a
        named origin must be one, and a picked one is a free one; where it names none, the origin
        is any module, and a picked one any free module. Where it names exit modules, a named
        destination must be one, and a picked one is one; where it names none, the destination
        is any module. A picked destination is never the origin, and a named one may not be. One
        module may have both roles. Roles are optional.

        **Only a reachable destination** (`spawn_candidates`). A picked destination is one the
        origin reaches over the connections, and a picked origin one that reaches the named
        destination, or else some destination; a named destination that the named origin does not
        reach is refused, for every topology.

        **A strategy picks** (`strategies`). Where the request leaves an end to pick, the
        WarehouseManagementSystem's strategy picks one pair among the candidates, given the
        connections as this read found them and the chooser; `real` lets the chooser pick the
        origin, then the destination, as before. A pair that is not a candidate refuses the
        spawn: "the strategy <name> chose Module1 to Module7, which is not a candidate". A
        request that names both ends is checked as before, and no strategy is asked.

        Raises:
            _NothingToSpawn: If there are none, or the request names one that may not take the
                place it is named for, saying why.
        """
        station, origins, destinations = read.station, read.origins, read.destinations
        if request.origin is not None:
            origin = IRI(request.origin)
            if origin not in origins.modules:
                raise _NothingToSpawn(
                    f"{local_name(origin)} is not {origins.with_article}"
                )
            free = [origin]
        else:
            in_flight = set(self._in_flight.values())
            free = [
                module
                for module in origins.modules
                if module in read.empty and module not in in_flight
            ]
            if not free:
                raise _NothingToSpawn(f"no {origins.name} is free")
        if request.destination is not None:
            destination = IRI(request.destination)
            if destination not in destinations.modules:
                raise _NothingToSpawn(
                    f"{local_name(destination)} is not {destinations.with_article}"
                )
            if request.origin is not None and IRI(request.origin) == destination:
                raise _NothingToSpawn("a box's origin and destination must differ")
            allowed = [destination]
        else:
            allowed = destinations.modules
            if request.origin is not None and allowed == [IRI(request.origin)]:
                raise _NothingToSpawn(
                    f"the knowledge graph has no {destinations.name} other than "
                    f"{local_name(request.origin)}"
                )
        candidates = spawn_candidates(station.connections, free, allowed)
        if not candidates:
            raise _NothingToSpawn(_unreachable(request, origins, destinations))
        if request.origin is not None and request.destination is not None:
            return IRI(request.origin), IRI(request.destination)
        origin, destination = self.strategy.origin_and_destination(
            candidates, station.directed(), self._chooser(request)
        )
        if destination not in candidates.get(IRI(origin), ()):
            raise _NothingToSpawn(
                f"the strategy {self.strategy_name} chose {local_name(origin)} to "
                f"{local_name(destination)}, which is not a candidate"
            )
        return IRI(origin), IRI(destination)

    def _chooser(self, request: SpawnBoxRequest) -> Choose:
        """The chooser the strategy picks with for a spawn of `request`: this party's, except
        that a module the request names is no pick. Given it alone, it answers it, and this
        party's chooser is not asked."""
        named = {IRI(m) for m in (request.origin, request.destination) if m is not None}

        def choose(options: Sequence[IRI]) -> IRI:
            if len(options) == 1 and options[0] in named:
                return options[0]
            return self.choose(options)

        return choose

    def _fail(self, operation: IRI, reason: str, report: str | None = None) -> None:
        """Record `operation` failed. A refusal by the store is logged with its report: the
        transport stays as it is, and the answer of the spawn says why it did not start."""
        try:
            self.records.fail(operation, reason, report)
        except ShaclValidationError as refused:
            logger.warning(
                "%s: the triple store refused recording %s failed%s:\n%s",
                self.resource.fragment,
                operation.fragment,
                rules_clause(refused.report),
                refused.report,
            )
            return
        self.memory.failed(operation, reason, report)
        logger.info(
            "%s", status_record(self.resource, operation, OperationStatus.FAILED)
        )
