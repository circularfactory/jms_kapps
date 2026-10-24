"""A participant's handover event stream: what happened in its handovers, served as SSE.

Every module and the WarehouseManagementSystem keeps its own stream. The graph holds only where
the stream lives (`fc:hasHandoverEventStream`, which each participant commits at startup), never
its events: a monitor finds the stream through the graph and reads the events from the participant.

**Two kinds for now.**

- **`commit_refused`.** A participant emits it when the store refuses the entry of one of its
  handovers, with the store's validation report verbatim, and the WarehouseManagementSystem when
  the store refuses the create that puts a box on its origin, its handover onto the station.
- **`deadlock_alarm`.** A module emits it once for a box it holds that has waited past the
  deadlock alarm time, counted from its first ask of a Reserve, with the modules it asks
  (`asked`). The box keeps waiting.

The other kinds of the full stream (a Reserve answered, a reservation dropped, an admitted commit,
an arrival) belong to the monitor on the middleware's dashboard, which comes later.

**The event** is JSON: its `id`, its `time` (UTC, ISO 8601), the `resource` that emitted it, its
`kind`, the `box`, the `peer` and the `transport` it is about, a `reason`, for a refusal the
`refusal`: its `results`, one `{shape, focus_node, path}` for every rule the report names (a
report can name several, and names none when it is no RDF), and the `report` itself, and for an
alarm (`DeadlockAlarm`) the modules `asked`, which no other kind has. Every IRI is full; a local
name is made only where a person reads it.

**The route** `GET /handover-events` keeps the last 500 events. An event's id is
`<stream token>:<sequence>`, and the token is new at every start of the process, so a reader that
resumes with `Last-Event-ID` after a restart gets every event kept instead of none. Each connection
delivers what is pending, waits at most 2 s for more, delivering what comes, and ends; the reader
connects again. So an open stream never holds a stopping participant for longer than that.
`read_sse` reads one such connection, as a reader of the stream does.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from kapps_ogm import Update
from kapps_semantic_middleware.validation_report import validation_results
from kapps_triplestore_interface import IRI
from pydantic import BaseModel
from rdflib import XSD
from rdflib import Literal as RDFLiteral

from jms_kapps.uc2.vocabulary import FC

STREAM_ROUTE = "/handover-events"

# How many events a participant keeps for its readers.
KEPT_EVENTS = 500

# How long one connection waits for more before it ends.
WAIT_SECONDS = 2.0

# How often a waiting connection looks for a new event.
_POLL_SECONDS = 0.05

EventKind = Literal["commit_refused", "deadlock_alarm"]


class BrokenRule(BaseModel):
    """One result of a validation report: the source shape, the focus node, and the path, which
    is None for a result without one, such as that of a shape that is an `sh:or`."""

    shape: str
    focus_node: str
    path: str | None


class StoreRefusal(BaseModel):
    """What the store refused a commit with: every rule its report names, in order, and the
    report itself. `results` is empty when the report names no rule."""

    results: list[BrokenRule]
    report: str


class HandoverEvent(BaseModel):
    id: str
    time: str
    resource: str
    kind: EventKind
    box: str
    peer: str | None
    transport: str | None
    reason: str | None
    refusal: StoreRefusal | None


class DeadlockAlarm(HandoverEvent):
    """An event of the kind `deadlock_alarm`: the module `resource` holds `box`, which has
    waited past the deadlock alarm time for a Reserve of the modules `asked`, each by its full
    IRI."""

    asked: list[str]


def event_from_json(data: str) -> HandoverEvent:
    """The event that `data`, one event of a stream, holds: a `DeadlockAlarm` where its kind is
    `deadlock_alarm`, so that the modules it asks are kept."""
    event = HandoverEvent.model_validate_json(data)
    if event.kind == "deadlock_alarm":
        return DeadlockAlarm.model_validate_json(data)
    return event


def refusal_of(report: str) -> StoreRefusal:
    """The refusal that `report` describes, read with the middleware's reader of a validation
    report."""
    return StoreRefusal(
        results=[
            BrokenRule(
                shape=result.shape, focus_node=result.focus, path=result.path or None
            )
            for result in sorted(validation_results(report))
        ],
        report=report,
    )


class HandoverEvents:
    """The handover event stream of the participant `resource`: the last events, in memory.

    Thread-safe: a participant emits from its workflows' threads while the route reads.

    Args:
        token: The stream token. A new one is drawn unless a test fixes it.
        kept: How many events are kept.
    """

    def __init__(
        self, resource: IRI, *, token: str | None = None, kept: int = KEPT_EVENTS
    ) -> None:
        self.resource = IRI(resource)
        self.token = token if token is not None else uuid.uuid4().hex
        self._events: deque[HandoverEvent] = deque(maxlen=kept)
        self._sequence = 0
        self._lock = threading.Lock()

    def commit_refused(
        self,
        *,
        box: IRI,
        peer: IRI | None,
        transport: IRI | None,
        reason: str | None,
        report: str,
    ) -> HandoverEvent:
        """Emit that the store refused a commit of this participant about `box`, with `report`."""
        # Read before the lock: a reader of the route waits for the lock on the event loop.
        refusal = refusal_of(report)
        with self._lock:
            event = HandoverEvent(
                **self._next(box=box, peer=peer, transport=transport),
                kind="commit_refused",
                reason=reason,
                refusal=refusal,
            )
            self._events.append(event)
        return event

    def deadlock_alarm(
        self,
        *,
        box: IRI,
        transport: IRI | None,
        asked: Sequence[IRI],
        reason: str,
    ) -> DeadlockAlarm:
        """Emit that `box`, which this participant holds, has waited past the deadlock alarm time
        for a Reserve of the modules `asked`, as `reason` says for a person."""
        with self._lock:
            event = DeadlockAlarm(
                **self._next(box=box, peer=None, transport=transport),
                kind="deadlock_alarm",
                reason=reason,
                refusal=None,
                asked=[str(module) for module in asked],
            )
            self._events.append(event)
        return event

    def _next(self, *, box: IRI, peer: IRI | None, transport: IRI | None) -> dict:
        """The fields every event has, numbered after the last: called under the lock."""
        self._sequence += 1
        return {
            "id": f"{self.token}:{self._sequence}",
            "time": datetime.now(UTC).isoformat(),
            "resource": str(self.resource),
            "box": str(box),
            "peer": None if peer is None else str(peer),
            "transport": None if transport is None else str(transport),
        }

    def after(self, last_event_id: str | None) -> list[HandoverEvent]:
        """The kept events after `last_event_id`. All of them, when it is None or comes from
        another start of the process: what the reader read before is gone then."""
        with self._lock:
            events = list(self._events)
        token, sequence = _parts(last_event_id or "")
        if token != self.token or sequence is None:
            return events
        return [event for event in events if (_parts(event.id)[1] or 0) > sequence]


def _parts(event_id: str) -> tuple[str, int | None]:
    """The stream token and the sequence number of an event id, `<token>:<sequence>`. The
    sequence is None when the id has none."""
    token, _, sequence = event_id.rpartition(":")
    return token, int(sequence) if sequence.isdigit() else None


def _sse(event: HandoverEvent) -> str:
    return f"id: {event.id}\ndata: {event.model_dump_json()}\n\n"


def mount_handover_events(
    app: FastAPI, events: HandoverEvents, *, wait_seconds: float = WAIT_SECONDS
) -> None:
    """Serve `events` on `app` at `GET /handover-events`, as server-sent events."""

    async def stream(last_event_id: str | None) -> AsyncIterator[str]:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        last = last_event_id
        while True:
            for event in events.after(last):
                last = event.id
                yield _sse(event)
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(_POLL_SECONDS)

    @app.get(STREAM_ROUTE, include_in_schema=False)
    def handover_events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            stream(request.headers.get("last-event-id")),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )


def read_sse(url: str, last_event_id: str | None) -> Iterator[tuple[str, str]]:
    """The events of one connection to the SSE stream at `url`, after `last_event_id`.

    Raises:
        httpx.HTTPError: If the stream cannot be read.
    """
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    timeout = httpx.Timeout(10.0, connect=2.0)
    with httpx.stream("GET", url, headers=headers, timeout=timeout) as response:
        response.raise_for_status()
        event_id, data = "", list[str]()
        for line in response.iter_lines():
            if not line:
                if data:
                    yield event_id, "\n".join(data)
                event_id, data = "", []
            elif line.startswith("id:"):
                event_id = line[3:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())


def stream_locator(resource: IRI, address: str) -> list[Update]:
    """The change that says where `resource`'s handover event stream lives, the participant
    serving at `address`: for the one `mw.commit()` it makes at startup. It replaces the
    locator an earlier start of the participant left.

    The locator is an `xsd:anyURI` literal, the range that the ontology declares for
    `fc:hasHandoverEventStream`. A plain `str` would be stored as `xsd:string`, and an `IRI` as
    a node."""
    url = RDFLiteral(f"{address.rstrip('/')}{STREAM_ROUTE}", datatype=XSD.anyURI)
    return [Update(instance_iri=resource, data={str(FC.hasHandoverEventStream): [url]})]
