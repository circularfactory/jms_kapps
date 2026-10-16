"""The WarehouseManagementSystem's dashboard boxes, on its own dashboard, in the tab "WMS".

The dashboard is the middleware's: each dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. A form posts JSON to one of the
WarehouseManagementSystem's own two workflows that spawn, through htmx's `json-enc` extension,
which the dashboard ships, and leaves the page as it is. The dashboard boxes that poll show what the
workflow changed. Every path a dashboard box asks for is relative, so that it works at the WMS's
own address and through the Launcher's port alike. None carries JavaScript of its own;
"Transports" carries one style rule, which gives it the whole width of the tab.

Every dashboard box renders what the WarehouseManagementSystem holds in memory, and nothing else:
no read of the graph and no call to a module, so an open tab causes no load.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence, Sized
from typing import Any

from kapps_semantic_middleware.dashboard import (
    SHORTENING_NOTE,
    DashboardRegistry,
    escape,
    hint,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.spawn_rate import SpawnTimer
from jms_kapps.uc2.vocabulary import local_name
from jms_kapps.uc2.wms_memory import WmsMemory

TAB = "WMS"

# The id of the dashboard box of the spawn rate, which its line of the rate in force asks again.
RATE_BOX = "spawn-rate"

# The id of the dashboard box "Transports".
TRANSPORTS_BOX = "transports"

# "Transports" takes the whole width of the tab. Half of it, the width of a box, leaves its column
# "Reason" only what the four columns that stay on one line leave over, which cuts the column to
# its first letter. It stands alone in the tab's last row anyway. The dashboard draws each box in
# its element `box-<id>`, one cell of the tab's grid.
_WHOLE_WIDTH = f"<style>#box-{TRANSPORTS_BOX} {{ grid-column: 1 / -1; }}</style>"

# How often a dashboard box that shows what changes asks for itself again. It reads only memory.
REFRESH_SECONDS = 2

# The routes the middleware gives the two workflows, after their function names.
SPAWN_ROUTE = "/workflows/spawn_box/execute"
SET_RATE_ROUTE = "/workflows/set_spawn_rate/execute"

# The route of the dashboard box of the spawn rate, which the middleware's dashboard serves.
RATE_ROUTE = f"/dashboard/box/{RATE_BOX}"


def _from_the_dashboard(route: str) -> str:
    """`route` as the dashboard, at `/dashboard`, asks for it: relative, so that it reaches the
    WMS's route at the WMS's own address and through the Launcher, under `/p/<id>/`, alike."""
    return route.removeprefix("/")


# How many rows "Held boxes" and "Transports" show: the newest. The memory keeps every one.
SHOWN_ROWS = 50

# What a select offers for a module left to the WarehouseManagementSystem: an empty value, which
# the spawn takes as none named.
WMS_CHOOSES = "the WMS chooses"


def _short(iri: str | None) -> str:
    """`iri` shortened to its local name, in full when the pointer rests on it; a dash if none."""
    if iri is None:
        return escape(None)
    return f'<span title="{escape(iri)}">{escape(local_name(iri))}</span>'


def _origin_to_destination(origin: str | None, destination: str | None) -> str:
    """Where a box goes: its origin, an arrow, its destination."""
    return f"{_short(origin)} → {_short(destination)}"


# The column of prose, the only one whose cells wrap.
REASON = "Reason"

# A short value stays on one line. The dashboard breaks a cell anywhere, which suits a long IRI
# but cuts "accepted" or a time of day in two in a box half the page wide; the table scrolls
# sideways instead.
_ONE_LINE = ' style="white-space: nowrap"'


def _table(head: Sequence[str], rows: Iterable[Iterable[str]]) -> str:
    """A table under the note that its identifiers are shortened. Each cell is HTML already, and
    stays on one line, except in the column "Reason"."""
    header = "".join(f"<th>{escape(title)}</th>" for title in head)
    cell_attributes = ["" if title == REASON else _ONE_LINE for title in head]
    body = "".join(
        "<tr>"
        + "".join(
            f"<td{attributes}>{cell}</td>"
            for attributes, cell in zip(cell_attributes, row, strict=True)
        )
        + "</tr>"
        for row in rows
    )
    return (
        f"{hint(SHORTENING_NOTE)}"
        f'<div class="table-scroll"><table><tr>{header}</tr>{body}</table></div>'
    )


def _newest(shown: Sized, count: int, things: str) -> str:
    """The line above a table that shows only the newest of `count` `things`, if it does."""
    if len(shown) == count:
        return ""
    return hint(f"The newest {len(shown)} of {count} {things}, newest first.")


def _options(modules: Iterable[IRI]) -> str:
    """A select's options: the WarehouseManagementSystem's choice first, then each module."""
    return f'<option value="">{WMS_CHOOSES}</option>' + "".join(
        f'<option value="{escape(module)}" title="{escape(module)}">'
        f"{escape(local_name(module))}</option>"
        for module in modules
    )


def _rate_in_force(boxes_per_minute: float) -> str:
    """The line that shows the rate in force. It asks for its dashboard box again every few
    seconds and takes only itself from the answer, so the form above it keeps what a person
    types."""
    text = f"In force: {boxes_per_minute:g} boxes per minute"
    if boxes_per_minute == 0:
        text += ", so none spawns"
    return (
        f'<p class="rate-in-force" hx-get="{_from_the_dashboard(RATE_ROUTE)}" '
        f'hx-trigger="every {REFRESH_SECONDS}s" hx-select=".rate-in-force" '
        f'hx-swap="outerHTML">{escape(text)}</p>'
    )


def register_boxes(
    registry: DashboardRegistry,
    memory: WmsMemory,
    timer: SpawnTimer,
    *,
    origins: Iterable[IRI],
    destinations: Iterable[IRI],
) -> None:
    """Register the WarehouseManagementSystem's dashboard boxes in `registry`, in the tab "WMS".

    Args:
        registry: The dashboard of the WarehouseManagementSystem's middleware.
        memory: What the WarehouseManagementSystem knows of its own work, which "Last spawns",
            "Held boxes" and "Transports" show.
        timer: Its spawn timer, whose rate in force "Spawn rate" shows.
        origins: The modules "Spawn a box" offers as the origin: the entry modules of the
            FlexConveyor station, or every module where it names none.
        destinations: Those it offers as the destination: its exit modules, or every module
            where it names none. (Changed on 2026-10-06, jms_kapps 45, decided by Etienne: roles
            are optional. Until then the two were `entry_modules`, "The entry modules of the
            FlexConveyor station, which "Spawn a box" offers as the origin", and
            `exit_modules`, "Its exit modules, which "Spawn a box" offers as the destination".)
    """
    origin_options, destination_options = _options(origins), _options(destinations)

    @registry.box("spawn", title="Spawn a box", tab=TAB)
    def spawn(middleware: Any) -> str:
        return (
            f'<form hx-post="{_from_the_dashboard(SPAWN_ROUTE)}" hx-ext="json-enc" '
            'hx-swap="none" '
            'hx-disabled-elt="find button">'
            f'<label>Origin <select name="origin">{origin_options}</select></label> '
            f'<label>Destination <select name="destination">{destination_options}'
            "</select></label> "
            '<button type="submit">Spawn</button>'
            "</form>"
        )

    # Not polled, so that it keeps what a person types: only its line of the rate in force is.
    @registry.box(RATE_BOX, title="Spawn rate", tab=TAB)
    def spawn_rate(middleware: Any) -> str:
        boxes_per_minute = timer.boxes_per_minute
        return (
            f'<form hx-post="{_from_the_dashboard(SET_RATE_ROUTE)}" hx-ext="json-enc" '
            'hx-swap="none" '
            'hx-disabled-elt="find button">'
            '<label>Boxes per minute <input type="number" name="boxes_per_minute" '
            f'min="0" step="any" required value="{boxes_per_minute:g}"></label> '
            '<button type="submit">Set</button>'
            "</form>"
            f"{_rate_in_force(boxes_per_minute)}"
            # Changed on 2026-10-06, jms_kapps 41, decided by Etienne: a spawn moves no box.
            # Until then the hint went on "A spawn lasts until its box is on its origin, a move
            # long, so a rate above one box per move spawns as fast as the moves allow."
            # Changed again on 2026-10-06, jms_kapps 45, decided by Etienne: roles are optional.
            # Until then "created on a free entry module and that module is notified" and "A
            # spawn while no entry module is free is refused."
            # Changed again on 2026-10-07, jms_kapps 51, decided by Etienne: spawns overlap, and
            # each one beyond the cap is refused. Until then: "The WMS spawns one box after
            # another at this rate, and chooses each box's origin and destination. 0 stops it. A
            # spawn lasts until its box is created on a free entry module, or on any free module
            # where the topology names no entry module, and that module is notified, so a rate
            # faster than that spawns as fast as the store answers. A spawn while no such module
            # is free is refused."
            + hint(
                "The WMS starts a spawn each time one is due at this rate, whether the "
                "one before it has ended or not, and chooses each box's origin and "
                "destination. 0 stops it. At most as many spawns are under way at once "
                "as there are entry modules, or modules where the topology names no "
                "entry module: a spawn beyond that is refused, and so is one while no "
                "such module is free, or none reaches a destination."
            )
        )

    @registry.box(
        "last-spawns", title="Last spawns", tab=TAB, refresh_seconds=REFRESH_SECONDS
    )
    def last_spawns(middleware: Any) -> str:
        answers = memory.answers()
        if not answers:
            return hint("No box has been spawned yet.")
        return _newest(answers, memory.answer_count(), "spawns") + _table(
            ["Time", "Box", "Origin → destination", "Answer", REASON],
            (
                [
                    escape(answer.at.strftime("%H:%M:%S")),
                    _short(answer.result.box),
                    _origin_to_destination(
                        answer.result.origin, answer.result.destination
                    ),
                    "accepted" if answer.result.accepted else "refused",
                    escape(answer.why),
                ]
                for answer in answers
            ),
        )

    @registry.box(
        "held-boxes", title="Held boxes", tab=TAB, refresh_seconds=REFRESH_SECONDS
    )
    def held_boxes(middleware: Any) -> str:
        shown = memory.transports_of_held_boxes(SHOWN_ROWS)
        if not shown:
            return hint("The WMS holds no box.")
        return _newest(shown, memory.held_count(), "boxes it holds") + _table(
            ["Box", "Transport", "Last known status"],
            (
                [
                    _short(known.transport.box),
                    _short(known.transport.operation),
                    escape(known.transport.status),
                ]
                for known in shown
            ),
        )

    @registry.box(
        TRANSPORTS_BOX, title="Transports", tab=TAB, refresh_seconds=REFRESH_SECONDS
    )
    def transports(middleware: Any) -> str:
        shown = memory.transports(SHOWN_ROWS)
        if not shown:
            return _WHOLE_WIDTH + hint("The WMS has opened no transport yet.")
        return (
            _WHOLE_WIDTH
            + _newest(shown, memory.transport_count(), "transports")
            + _table(
                [
                    "Transport",
                    "Box",
                    "Origin → destination",
                    "Last known status",
                    REASON,
                ],
                (
                    [
                        _short(known.transport.operation),
                        _short(known.transport.box),
                        _origin_to_destination(
                            known.transport.origin, known.transport.destination
                        ),
                        escape(known.transport.status),
                        escape(known.why),
                    ]
                    for known in shown
                ),
            )
        )
