"""The monitor's HTTP surface, grafted onto its middleware's app: JSON routes and a static page.

Every GET reads only the monitor's memory: what its background task read last (`memory.py`). The
two PUTs set how the monitor reads, its mode and the interval of its graph read, and change
nothing else: the monitor writes nothing to the graph. The backend renders no HTML: `/api/state`
is JSON, and the page in `static/` draws it. The middleware's own welcome at `/` gives way to the
page, as the transferunits demo's station board replaces it. (Changed on 2026-10-08, jms_kapps 24,
decided by Etienne on 2026-10-05: a switch on the monitor's page, and a setting for the interval
of its graph read. Until then the second sentence read: "Every route here answers GET, and reads
only the monitor's memory: what its background task read last (`memory.py`).")
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import get_args

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from kapps_semantic_middleware.validation_report import ReadableResult, Term
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.uc2.handover_events import DeadlockAlarm
from jms_kapps.uc2.monitor.handovers import SeenHandover
from jms_kapps.uc2.monitor.memory import (
    LONGEST_GRAPH_SECONDS,
    SHORTEST_GRAPH_SECONDS,
    KeptRefusal,
    Mode,
    MonitorMemory,
    Snapshot,
)
from jms_kapps.uc2.monitor.reads import SentQuery
from jms_kapps.uc2.monitor.wires import wires
from jms_kapps.uc2.topology import layout
from jms_kapps.uc2.vocabulary import local_name

STATIC = Path(__file__).parent / "static"

# Why the page shows a refusal's report only as the store sent it, or no report at all.
NO_REPORT_NOTE = "The participant sent no report with this refusal."
UNREADABLE_NOTE = "The monitor could not read this report as RDF, so it shows it as the store sent it."
NO_RESULT_NOTE = (
    "The report names no result, so the monitor shows it as the store sent it."
)


def _local(iri: str | IRI | None) -> str | None:
    return None if iri is None else local_name(iri)


def _result_json(result: ReadableResult) -> dict:
    """`result` for the page: its message as text, empty when it gives none, and each other term
    as `{short, full}`, or None."""
    shown: dict = {
        field: value._asdict() if isinstance(value, Term) else value
        for field, value in result._asdict().items()
    }
    shown["message"] = result.message.short if result.message is not None else ""
    return shown


def _refusal(kept: KeptRefusal) -> dict:
    event = kept.event
    report = event.refusal.report if event.refusal else ""
    if not report:
        note: str | None = NO_REPORT_NOTE
    elif kept.results is None:
        note = UNREADABLE_NOTE
    elif not kept.results:
        note = NO_RESULT_NOTE
    else:
        note = None
    return {
        "id": event.id,
        "time": event.time,
        "giver": _local(event.resource),
        "receiver": _local(event.peer),
        "box": _local(event.box),
        "reason": event.reason,
        "rules": list(kept.rules),
        # As the participant emitted them, every IRI whole: what a program compares.
        "results": (
            [rule.model_dump() for rule in event.refusal.results]
            if event.refusal
            else []
        ),
        # For a person to read: each result of the report in full, every term short with its
        # whole IRI beside it. Empty, with a note that says why, when the report names none.
        "readable_results": [_result_json(result) for result in kept.results or ()],
        "report_note": note,
        "report": report,
    }


def _by_name(box: IRI) -> tuple[str, str]:
    """Where `box` stands in the Boxes table: by its name, as text sorts it, and by its whole IRI
    where two names are the same.

    (Changed on 2026-10-12, jms_kapps 92, the ticket's interim reading of Sören's decision on
    jms_kapps 85: a box is named by a UUID, which has no order a person counts in. Until then the
    table ordered the boxes by number, "Box 2 before Box 10, as a person counts, and a box that the
    WarehouseManagementSystem did not name after every box it named". Interim, jms_kapps 83.)"""
    return (str(_local(box)), str(box))


def possible_deadlock(snapshot: Snapshot) -> dict | None:
    """The one "possible deadlock" notice of `snapshot`'s deadlock alarms, or None where it holds
    none: every module involved, each module with an alarm and the modules it asks, in the order
    of the graph's modules, by its local name; and each alarm, oldest first, with its module, its
    box, the modules it asks and its reason. (Added on 2026-10-11, jms_kapps 77, decided by
    Etienne.)"""
    if not snapshot.alarms:
        return None
    involved = {
        IRI(module)
        for alarm in snapshot.alarms
        for module in (alarm.resource, *alarm.asked)
    }
    order = {module.resource: index for index, module in enumerate(snapshot.modules)}
    modules = sorted(
        involved, key=lambda module: (order.get(module, len(order)), str(module))
    )
    return {
        "modules": [_local(module) for module in modules],
        "alarms": [_alarm(alarm) for alarm in snapshot.alarms],
    }


def _alarm(alarm: DeadlockAlarm) -> dict:
    return {
        "id": alarm.id,
        "time": alarm.time,
        "module": _local(alarm.resource),
        "box": _local(alarm.box),
        "asked": [_local(module) for module in alarm.asked],
        "reason": alarm.reason,
    }


def _handover(handover: SeenHandover) -> dict:
    return {
        "seen": handover.seen,
        "box": _local(handover.box),
        "from": _local(handover.giver),
        "to": _local(handover.receiver),
    }


class ModeChange(BaseModel):
    """What a page sends to switch the monitor's mode."""

    mode: Mode


class IntervalChange(BaseModel):
    """What a page sends to set the interval of the monitor's graph read, in seconds."""

    seconds: float


def _reading(snapshot: Snapshot) -> dict:
    """How the monitor reads: its mode, of the modes it has, and the interval of its graph read,
    of those a page may set."""
    return {
        "mode": snapshot.mode,
        "modes": list(get_args(Mode)),
        "graph_seconds": snapshot.graph_seconds,
        "shortest_graph_seconds": SHORTEST_GRAPH_SECONDS,
        "longest_graph_seconds": LONGEST_GRAPH_SECONDS,
    }


def _query(query: SentQuery, snapshot: Snapshot) -> dict:
    return {
        "read": query.read,
        "text": query.text,
        "every_seconds": snapshot.every(query),
        "sent": query.sent,
        "duration_seconds": query.duration,
        "rows": query.rows,
        "error": query.error,
    }


def monitor_state(snapshot: Snapshot) -> dict:
    """The modules where they stand, live or offline, with the boxes they hold; the wires between
    them, each with the way its connections lead; every box, by its name (`_by_name`; by its
    number until 2026-10-12, jms_kapps 92), with its transport's destination; the newest handovers
    and the newest refusals, each newest first; the one "possible deadlock" notice, or None
    (`possible_deadlock`, added on 2026-10-11, jms_kapps 77); how the monitor reads, and the query
    each of its reads sent last. (Changed on 2026-10-10, jms_kapps 24,
    decided by Etienne on 2026-10-06: "we can map destinations but not routes". Until then: "the
    wires between them, each with the way its connections lead and the way boxes from entry to
    exit modules hop over it; every box, by its number, with its path ahead if its transport is
    running; the newest handovers and the newest refusals, each newest first".)"""
    held: dict[IRI, list[str]] = defaultdict(list)
    boxes = sorted(snapshot.boxes, key=lambda row: _by_name(row.box))
    for row in boxes:
        if row.holder is not None:
            held[row.holder].append(str(_local(row.box)))
    labels = {m.resource: m.label or str(_local(m.resource)) for m in snapshot.modules}
    places = layout(
        {
            m.resource: [(c.direction, c.to) for c in m.connections]
            for m in snapshot.modules
        }
    )
    return {
        "error": snapshot.error,
        "modules": [
            {
                "id": str(module.resource),
                "label": labels[module.resource],
                "type": module.type,
                "place": list(places[module.resource]),
                "live": module.resource in snapshot.live,
                "boxes": held.get(module.resource, []),
                "holds_box": module.resource in held,
                "connections": [
                    {
                        "direction": c.direction,
                        "to": str(c.to),
                        "to_label": labels.get(c.to, _local(c.to)),
                        "port": c.port,
                    }
                    for c in module.connections
                ],
            }
            for module in snapshot.modules
        ],
        # Each module by its whole IRI, as the modules' "id" gives it.
        "wires": [
            {
                "between": [str(end) for end in wire.between],
                "leads_to": [str(end) for end in wire.leads_to],
            }
            for wire in wires(snapshot.modules)
        ],
        "boxes": [
            {
                "id": _local(row.box),
                "status": row.status,
                "origin": _local(row.origin),
                "destination": _local(row.destination),
                "holder": _local(row.holder),
                # The destination by its whole IRI, as the modules' "id" gives it, so that the
                # drawing marks it on its module; None when the box has no transport. The monitor
                # computes no path to it: only the module that holds the box knows its next one.
                "destination_id": None
                if row.destination is None
                else str(row.destination),
            }
            for row in boxes
        ],
        "handovers": [_handover(handover) for handover in snapshot.handovers],
        "refusals": [_refusal(kept) for kept in snapshot.refusals],
        "possible_deadlock": possible_deadlock(snapshot),
        "reading": _reading(snapshot),
        "queries": [_query(query, snapshot) for query in snapshot.queries],
    }


def mount_onto(app: FastAPI, *, memory: MonitorMemory) -> None:
    """Add the monitor's routes and page to `app`, in place of its root route."""
    for route in list(app.routes):
        if getattr(route, "path", None) == "/" and "GET" in (
            getattr(route, "methods", None) or set()
        ):
            app.routes.remove(route)

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def state() -> dict:
        return monitor_state(memory.snapshot())

    @app.put("/api/mode")
    def switch(change: ModeChange) -> dict:
        """Switch between "graph + streams" and "graph only"."""
        memory.set_mode(change.mode)
        return _reading(memory.snapshot())

    @app.put("/api/graph-read-interval")
    def set_interval(change: IntervalChange) -> dict:
        """Read the graph every `seconds`, from 0.5 s to 10 s."""
        try:
            memory.set_graph_seconds(change.seconds)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _reading(memory.snapshot())

    app.mount("/static", StaticFiles(directory=STATIC), name="monitor-static")
    app.openapi_schema = None
