"""What one FlexConveyorModule knows of its own work, kept in its memory.

Its dashboard renders this memory and the module's fault switches, and nothing else, so an open tab
causes no load: no read of the graph and no call to a peer. The module writes it as it works, from
what it reads and answers for its work anyway: its decisions, with the connections each one read
and the Reserve refusals that followed, and the box it holds. Its process adds one read of its
own when the module starts: the connections, so that the dashboard draws them before the first
decision, and the boxes the module possesses, so that a restarted module shows the box it holds.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection, StartRead
from jms_kapps.uc2.handshake import Refusal
from jms_kapps.uc2.transport import Transport

# How many decisions it keeps: the newest.
KEPT_DECISIONS = 20


@dataclass(frozen=True)
class Decision:
    """Where the module decided to send a box, and when.

    `next_module` is the module it chose, or None where it chose none: the box is on its
    destination, or no path leads there. `ties` are the modules it could also have chosen, on a
    shortest path as short, which lost to the lowest IRI, or to the module reserved ahead.
    `refusals` are the Reserve refusals of the next module that followed, in order. `detour` is
    the one other module it would try when the next module declines, `detour_refusals` that
    module's refusals, and `detoured` whether the box went there. `retries` counts the times it
    waited and asked again.
    """

    at: datetime
    box: IRI
    transport: IRI
    destination: IRI
    next_module: IRI | None
    ties: tuple[IRI, ...] = ()
    refusals: tuple[Refusal, ...] = ()
    detour: IRI | None = None
    detour_refusals: tuple[Refusal, ...] = ()
    detoured: bool = False
    retries: int = 0


@dataclass(frozen=True)
class ConnectionsRead:
    """The station's connections as the module read them, and when: for its decision on `box`, or
    when it started if `box` is None."""

    at: datetime
    box: IRI | None
    connections: tuple[DirectedConnection, ...]


class ModuleMemory:
    """What a module knows of its own work: its newest decisions, the connections it read last,
    and the box it holds.

    It holds a box from the moment it knows it possesses the box: its start read finds the box in
    its possession; its Receive confirms the box's arrival, after the giving module's handover
    committed the change of possession; the bound of its reservation finds the box in its
    possession, though no Receive came; or, at the origin, the store admits the box's transport
    `running`, which it does only while a FlexConveyorModule possesses the box, and the
    WarehouseManagementSystem created the box on the origin before it notified it. A transport
    merely assigned to the module is no such knowledge. It holds the box until the move of the
    box off it has ended, so during a move the box is still the giving module's here, where the
    graph already names the receiving module its possessor.
    """

    def __init__(self) -> None:
        # A decision is kept on the thread of the module's BoxTransportWorkflow, and a render reads
        # on one of its own.
        self._lock = threading.Lock()
        self._decisions: deque[Decision] = deque(maxlen=KEPT_DECISIONS)
        # How many decisions it made, the ones it no longer keeps included.
        self._decided = 0
        self._connections: ConnectionsRead | None = None
        self._held: IRI | None = None

    def decided(
        self,
        transport: Transport,
        choices: Sequence[IRI],
        connections: Sequence[DirectedConnection] | None = None,
        *,
        detour: IRI | None = None,
    ) -> None:
        """Keep a decision on the box of `transport`, made now: `choices` are the modules first on
        a shortest path to its destination, the one chosen first, `connections` what it read to
        decide, if it read them, and `detour` the module it would try when the first declines."""
        at = datetime.now().astimezone()
        decision = Decision(
            at=at,
            box=transport.box,
            transport=transport.operation,
            destination=transport.destination,
            next_module=choices[0] if choices else None,
            ties=tuple(choices[1:]),
            detour=detour,
        )
        with self._lock:
            self._decisions.appendleft(decision)
            self._decided += 1
            if connections is not None:
                self._connections = ConnectionsRead(
                    at, transport.box, tuple(connections)
                )

    def refused(self, box: IRI, refusal: Refusal) -> None:
        """Keep a refusal of the next module's Reserve for `box`, which the module answers by its
        detour or by asking again, on its newest decision on `box`. A refusal with no decision on
        `box` is not kept."""
        self._change(box, lambda d: replace(d, refusals=(*d.refusals, refusal)))

    def detour_refused(self, box: IRI, refusal: Refusal) -> None:
        """Keep a refusal of the detour's Reserve for `box` on its newest decision on `box`."""
        self._change(
            box, lambda d: replace(d, detour_refusals=(*d.detour_refusals, refusal))
        )

    def detoured(self, box: IRI) -> None:
        """`box` takes the detour of its newest decision."""
        self._change(box, lambda d: replace(d, detoured=True))

    def waited(self, box: IRI) -> None:
        """The module waits the retry time, and then asks again for `box`."""
        self._change(box, lambda d: replace(d, retries=d.retries + 1))

    def _change(self, box: IRI, change: Callable[[Decision], Decision]) -> None:
        """Change the newest decision on `box` with `change`; nothing if there is none."""
        with self._lock:
            for index, decision in enumerate(self._decisions):
                if decision.box == box:
                    self._decisions[index] = change(decision)
                    return

    def read_at_start(self, read: StartRead) -> None:
        """Keep what the module read when it started: the connections, until it reads them to
        decide, and the box it possesses, if the graph names one. The connections are for its
        dashboard alone: every decision reads them again."""
        with self._lock:
            self._connections = ConnectionsRead(
                datetime.now().astimezone(), None, tuple(read.connections)
            )
            if read.held:
                self._held = read.held[0]

    def holding(self, box: IRI) -> None:
        """It holds `box`: the box is on it."""
        with self._lock:
            self._held = box

    def gone(self, box: IRI) -> None:
        """`box` has moved off it."""
        with self._lock:
            if self._held == box:
                self._held = None

    def held(self) -> IRI | None:
        """The box it holds, if it holds one."""
        with self._lock:
            return self._held

    def decisions(self) -> list[Decision]:
        """The decisions it keeps, newest first."""
        with self._lock:
            return list(self._decisions)

    def decision_count(self) -> int:
        """How many decisions it made, the ones it no longer keeps included."""
        with self._lock:
            return self._decided

    def connections(self) -> ConnectionsRead | None:
        """The connections it read last, or None if it has read none."""
        with self._lock:
            return self._connections
