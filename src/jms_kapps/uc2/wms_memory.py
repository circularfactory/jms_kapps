"""What the WarehouseManagementSystem knows of its own work, kept in its memory.

Its dashboard renders this memory and nothing else, so an open tab causes no load: no read of the
graph and no call to a module. The WarehouseManagementSystem writes it as it works, from its own
spawns and from the notifications it gets when a transport ends, and it reads the graph for none of
it beyond what it reads for its work anyway.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import islice
from typing import TYPE_CHECKING

from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.transport import Transport, failure, reason_and_report
from jms_kapps.uc2.validation_report import messages_clause, rules_clause

if TYPE_CHECKING:
    from jms_kapps.uc2.wms import SpawnBoxResult

# How many answers of spawns it keeps: the newest.
KEPT_ANSWERS = 20


@dataclass(frozen=True)
class SpawnAnswer:
    """The answer to one spawn, and when it was given. `why` is the reason of a refusal, with the
    rules the store's report names and what they say when the store refused, read once when it
    was kept."""

    at: datetime
    result: SpawnBoxResult
    why: str | None


@dataclass(frozen=True)
class KnownTransport:
    """A transport the WarehouseManagementSystem opened, as it last knew it. `why` says why it
    failed, with the rules the store's report names and what they say when the store refused,
    read once when it was kept."""

    transport: Transport
    why: str | None = None


def _explained(reason: str, report: str | None) -> str:
    """`reason`, followed by the rules the store's `report` names when the store refused, and by
    what those rules say."""
    return reason + rules_clause(report) + messages_clause(report) if report else reason


def _why(result: str | None) -> str | None:
    """Why a transport failed, from its result: the reason, and the rules of a report after it."""
    if not result:
        return None
    return _explained(*reason_and_report(result))


class WmsMemory:
    """What the WarehouseManagementSystem knows of its own work: the answers to its newest spawns,
    the transports it opened with their last known status, and the boxes it holds.

    It knows a transport's status from its own commits, and from the notification that the
    transport ended. A transport it opened on its origin stays `queued` here until it ends, since
    only the module it is assigned to writes it meanwhile, unless the origin cannot be told of
    it: then the WarehouseManagementSystem ends it `failed` itself. It holds a box only once a
    notification brings the box back with its transport done: it creates each box on the box's
    origin.

    It keeps every transport and every box it holds for as long as it runs, a few hundred bytes
    each; a reader asks for the newest few.
    """

    def __init__(self) -> None:
        # Spawns answer on the threads of their requests and of the timer, and a render reads on
        # one of its own.
        self._lock = threading.Lock()
        self._answers: deque[SpawnAnswer] = deque(maxlen=KEPT_ANSWERS)
        # How many spawns it answered, the ones it no longer keeps included.
        self._answered = 0
        # In the order they were opened.
        self._transports: dict[IRI, KnownTransport] = {}
        # The transport of each box it holds, by box, in the order it came to hold them.
        self._held: dict[IRI, IRI] = {}

    def answered(self, result: SpawnBoxResult) -> None:
        """Keep the answer to a spawn, at the time of day it was given."""
        why = _explained(result.reason, result.report) if result.reason else None
        answer = SpawnAnswer(datetime.now().astimezone(), result, why)
        with self._lock:
            self._answers.appendleft(answer)
            self._answered += 1

    def answers(self) -> list[SpawnAnswer]:
        """The answers to the newest spawns, newest first."""
        with self._lock:
            return list(self._answers)

    def answer_count(self) -> int:
        """How many spawns it answered, the ones whose answers it no longer keeps included."""
        with self._lock:
            return self._answered

    def opened(self, transport: Transport) -> None:
        """It opened `transport`, with its box on the box's origin."""
        with self._lock:
            self._transports[transport.operation] = KnownTransport(transport)

    def failed(self, operation: IRI, reason: str, report: str | None = None) -> None:
        """It recorded `operation` failed, for `reason`, with the store's `report` if it has one."""
        result = failure(reason, report)
        with self._lock:
            known = self._transports.get(operation)
            if known is None:
                return
            self._transports[operation] = KnownTransport(
                replace(known.transport, status=OperationStatus.FAILED, result=result),
                _why(result),
            )

    def ended(self, transport: Transport, *, held: bool) -> None:
        """A notification brought `transport`, which ended. `held` is whether it holds the box."""
        known = KnownTransport(transport, _why(transport.result))
        with self._lock:
            self._transports[transport.operation] = known
            self._held.pop(transport.box, None)
            if held:
                self._hold(transport)

    def _hold(self, transport: Transport) -> None:
        """It holds the box of `transport`, last of all it holds. Called under the lock."""
        self._held.pop(transport.box, None)
        self._held[transport.box] = transport.operation

    def transports(self, newest: int | None = None) -> list[KnownTransport]:
        """The transports it opened, or that a notification brought, newest first: the `newest`
        of them, or every one if None."""
        with self._lock:
            return list(islice(reversed(self._transports.values()), newest))

    def transport_count(self) -> int:
        """How many transports it knows."""
        with self._lock:
            return len(self._transports)

    def transports_of_held_boxes(
        self, newest: int | None = None
    ) -> list[KnownTransport]:
        """The transport of each box it holds, the box it came to hold last first: of the
        `newest` boxes, or of every one if None."""
        with self._lock:
            operations = islice(reversed(self._held.values()), newest)
            return [self._transports[operation] for operation in operations]

    def held_count(self) -> int:
        """How many boxes it holds."""
        with self._lock:
            return len(self._held)
