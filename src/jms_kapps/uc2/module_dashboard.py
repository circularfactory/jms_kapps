"""One FlexConveyorModule's dashboard boxes, on its own dashboard, in a tab named after the module.

The dashboard is the middleware's: each dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. Every dashboard box renders what the module holds
in memory, and nothing else: no read of the graph and no call to a peer, so an open tab causes no
load.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import groupby
from typing import Any

from kapps_semantic_middleware.dashboard import (
    DashboardRegistry,
    escape,
    hint,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handshake import Refusal
from jms_kapps.uc2.module import FAULTS, FlexConveyorModule
from jms_kapps.uc2.module_drawing import adjacency_matrix, top_view
from jms_kapps.uc2.module_memory import ConnectionsRead, Decision
from jms_kapps.uc2.vocabulary import local_name

# The ids of the three dashboard boxes.
MODULE_BOX = "module"
MATRIX_BOX = "adjacency-matrix"
DECISIONS_BOX = "routing-decisions"

# How often a dashboard box asks for itself again. It reads only memory.
REFRESH_SECONDS = 2


def _whole_width(box_id: str) -> str:
    """The style that lays the box `box_id` across every column of the tab's grid, carried by the
    box's own fragment: the dashboard draws each box in its element `box-<id>`, one cell of the
    grid, and has no option of its own for a box's width. "Routing decisions" takes the whole
    width because eight columns do not fit in half of it; the adjacency matrix, so that a
    station of many modules draws its cells large enough to read."""
    return f"<style>#box-{box_id} {{ grid-column: 1 / -1; }}</style>"


# A short value stays on one line, and the table scrolls sideways instead.
_ONE_LINE = ' style="white-space: nowrap"'


def _name(iri: IRI | None) -> str:
    """`iri` shortened to its local name, in full when the pointer rests on it; a dash if none."""
    if iri is None:
        return escape(None)
    return f'<span title="{escape(iri)}">{escape(local_name(iri))}</span>'


def _names(iris: Sequence[IRI]) -> str:
    """Each of `iris` shortened, separated by commas; a dash if there is none."""
    return ", ".join(_name(iri) for iri in iris) if iris else escape(None)


def _table(head: Sequence[str], rows: Iterable[Iterable[str]]) -> str:
    """A table whose cells are HTML already."""
    header = "".join(f"<th>{escape(title)}</th>" for title in head)
    body = "".join(
        "<tr>" + "".join(f"<td{_ONE_LINE}>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll"><table><tr>{header}</tr>{body}</table></div>'


def _next_module(module: IRI, decision: Decision) -> str:
    """Where the decision sends the box: the next module, back to the WMS, or nowhere."""
    if decision.destination == module:
        return escape("back to the WMS")
    if decision.next_module is None:
        return escape("none: no path leads there")
    return _name(decision.next_module)


def _runs(refusals: Sequence[Refusal]) -> str:
    """The Reserve refusals in order, each run of one kind counted: "occupied ×2, reserved"."""
    runs = [(kind, len(list(run))) for kind, run in groupby(refusals)]
    return ", ".join(kind if count == 1 else f"{kind} ×{count}" for kind, count in runs)


def _refusals(refusals: Sequence[Refusal]) -> str:
    """The Reserve refusals in order, each run of one kind counted, or a dash if none."""
    return escape(_runs(refusals)) if refusals else escape(None)


def _detour(decision: Decision) -> str:
    """The one other module the decision would try when the next module declines, and what came
    of it: taken, refused, or not asked, since the next module accepted."""
    if decision.detour is None:
        return escape(None)
    if decision.detoured:
        outcome = "taken"
    elif decision.detour_refusals:
        outcome = f"refused as {_runs(decision.detour_refusals)}"
    else:
        outcome = "not asked"
    return f"{_name(decision.detour)}{escape(f': {outcome}')}"


def _read_when(module_name: str, read: ConnectionsRead) -> str:
    """When the connections were read, and for what."""
    at = read.at.strftime("%H:%M:%S")
    if read.box is None:
        return f"Read at {at}, when {module_name} started."
    return f"Read at {at} for the decision on {local_name(read.box)}."


def register_boxes(
    registry: DashboardRegistry, module: FlexConveyorModule, transit_seconds: float
) -> None:
    """Register `module`'s dashboard boxes in `registry`, in a tab named after the module.
    `transit_seconds` is the module's transit time, which its process read from the graph at its
    start, and which its drawing box shows."""
    tab = local_name(module.resource)
    memory = module.memory

    @registry.box(
        MODULE_BOX, title=f"{tab} from above", tab=tab, refresh_seconds=REFRESH_SECONDS
    )
    def drawing(middleware: Any) -> str:
        held, read = memory.held(), memory.connections()
        armed = [fault for fault in FAULTS if module.faults[fault]]
        holds = f"{tab} holds {local_name(held)}." if held else f"{tab} holds no box."
        transit = hint(f"transit time: {transit_seconds:g} s")
        faults = (
            f'<p style="color: var(--error-border)">'
            f"{escape(f'Faults armed: {", ".join(armed)}.')}</p>"
            if armed
            else hint("Faults armed: none.")
        )
        connections = (
            hint(
                f"Connections: {_read_when(tab, read)} An arrow out points to a module {tab} hands "
                "boxes to, an arrow in comes from a module that hands boxes to it."
            )
            if read
            else hint(f"{tab} has read no connections yet.")
        )
        return (
            top_view(module.resource, held, read.connections if read else ())
            + f"<p>{escape(holds)}</p>"
            + transit
            + faults
            + connections
            + hint(
                "Rollers, the silver cylinders, carry a box north–south; belt conveyors, "
                "the dark strips between them, carry it east–west. North is up."
            )
        )

    @registry.box(
        MATRIX_BOX, title="Adjacency matrix", tab=tab, refresh_seconds=REFRESH_SECONDS
    )
    def matrix(middleware: Any) -> str:
        read = memory.connections()
        if read is None:
            return _whole_width(MATRIX_BOX) + hint(
                f"{tab} has read no connections yet."
            )
        return (
            _whole_width(MATRIX_BOX)
            + hint(
                f"{_read_when(tab, read)} A row is the module a connection leaves, a column "
                "the module it leads to, and a cell the side it leaves by: N, E, S or W, or • "
                "where the knowledge graph names none, with ↔ where the two connect both ways. "
                f"The row of {tab} is marked."
            )
            + adjacency_matrix(module.resource, read.connections)
        )

    @registry.box(
        DECISIONS_BOX,
        title="Routing decisions",
        tab=tab,
        refresh_seconds=REFRESH_SECONDS,
    )
    def decisions(middleware: Any) -> str:
        shown = memory.decisions()
        if not shown:
            return _whole_width(DECISIONS_BOX) + hint(f"{tab} has decided nothing yet.")
        count = memory.decision_count()
        newest = (
            ""
            if len(shown) == count
            else hint(f"The newest {len(shown)} of {count} decisions, newest first.")
        )
        return (
            _whole_width(DECISIONS_BOX)
            + newest
            + _table(
                [
                    "Time",
                    "Box",
                    "Destination",
                    "Next module",
                    "Ties",
                    "Reserve refused as",
                    "Detour",
                    "Retries",
                ],
                (
                    [
                        escape(decision.at.strftime("%H:%M:%S")),
                        _name(decision.box),
                        _name(decision.destination),
                        _next_module(module.resource, decision),
                        _names(decision.ties),
                        _refusals(decision.refusals),
                        _detour(decision),
                        escape(decision.retries),
                    ]
                    for decision in shown
                ),
            )
        )
