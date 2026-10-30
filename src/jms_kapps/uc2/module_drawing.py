"""The two drawings of a module's dashboard, as inline SVG: the module seen from above, and the
adjacency matrix of the connections it read.

It is drawn as a real module looks from above: a green frame; inside it, silver motor rollers that
lie east–west side by side and carry a box north–south, and between them the dark strips of the
belt conveyors, which carry a box east–west. The light barriers are left out. North is up. A box
the module holds sits in the middle, with its name on it. Outside the frame, each side shows the
connections on that side: an arrow out to each module this one hands boxes to in that direction,
and an arrow in from each module that hands boxes to this one from there.

The adjacency matrix is the classic one, drawn to the width it is given: a row and a column for
each module, and in the cell of a row and a column the side by which the row's module hands boxes
to the column's.

Both drawings are static SVG, with no script: the dashboard asks for them again to show what
changed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from kapps_semantic_middleware.dashboard import escape
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.topology import Direction
from jms_kapps.uc2.vocabulary import UUID_BOX_PREFIX, local_name

Way = Literal["out", "in"]

# The side of a module that a connection in each direction leaves by, seen from the module it
# leads to: a box that leaves by the South comes in from the North.
_FROM: dict[Direction, Direction] = {
    "North": "South",
    "East": "West",
    "South": "North",
    "West": "East",
}

# The canvas, and the frame of the module in its middle.
_WIDTH, _HEIGHT = 360, 300
_LEFT, _TOP, _SIZE = 100, 70, 160
_RIGHT, _BOTTOM = _LEFT + _SIZE, _TOP + _SIZE
_MIDDLE_X, _MIDDLE_Y = _LEFT + _SIZE // 2, _TOP + _SIZE // 2

# Five rollers, each 20 high, with the 9 between two of them for a belt conveyor.
_ROLLERS = 5
_ROLLER_HEIGHT, _ROLLER_STEP = 20, 29
_FIRST_ROLLER = _TOP + 12

# The colours of the module, as the photograph shows it; the arrows take the dashboard's own.
_FRAME = "#3fae49"
_INSIDE = "#1d1d1d"
_ROLLER_END = "#7b7b7b"
_BELT = "#353535"
_BELT_PAD = "#8f969b"
_BOX = "#c9a265"
_BOX_EDGE = "#7a5a2b"
_BOX_TAPE = "#e0c48f"
_BOX_TEXT = "#2b1d0b"

# Where the arrow of each side and way runs, from its start to its head, and where its label is
# anchored: (x1, y1, x2, y2, label x, label y, label anchor).
_ARROWS: dict[tuple[Direction, Way], tuple[int, int, int, int, int, int, str]] = {
    ("North", "out"): (156, _TOP - 6, 156, 26, 150, 48, "end"),
    ("North", "in"): (204, 26, 204, _TOP - 6, 210, 48, "start"),
    ("South", "out"): (156, _BOTTOM + 6, 156, _HEIGHT - 26, 150, 262, "end"),
    ("South", "in"): (204, _HEIGHT - 26, 204, _BOTTOM + 6, 210, 262, "start"),
    ("East", "out"): (_RIGHT + 8, 126, _RIGHT + 50, 126, _RIGHT + 29, 117, "middle"),
    ("East", "in"): (_RIGHT + 50, 174, _RIGHT + 8, 174, _RIGHT + 29, 191, "middle"),
    ("West", "out"): (_LEFT - 8, 126, _LEFT - 50, 126, _LEFT - 29, 117, "middle"),
    ("West", "in"): (_LEFT - 50, 174, _LEFT - 8, 174, _LEFT - 29, 191, "middle"),
}

# What an arrow says when the pointer rests on it.
_TOOLTIP: dict[Way, str] = {
    "out": "{module} hands boxes {direction} to {others}",
    "in": "{module} takes boxes from {others}, to its {direction}",
}

_ARROW_STYLE: dict[Way, str] = {
    "out": "stroke: var(--accent); fill: var(--accent)",
    "in": "stroke: var(--fg); fill: var(--fg)",
}


def _sides(
    module: IRI, connections: Sequence[DirectedConnection]
) -> dict[tuple[Direction, Way], list[IRI]]:
    """The modules on each side of `module` and way: those it leads to by that side, and those
    that lead to it from there. A connection without one of the four directions is left out."""
    sides: dict[tuple[Direction, Way], list[IRI]] = defaultdict(list)
    for connection in connections:
        if connection.direction is None:
            continue
        if connection.source == module:
            sides[(connection.direction, "out")].append(connection.target)
        if connection.target == module:
            sides[(_FROM[connection.direction], "in")].append(connection.source)
    return sides


def _arrow(module: IRI, direction: Direction, way: Way, others: Sequence[IRI]) -> str:
    """The arrow of one side and way, with the names of `others` beside it."""
    x1, y1, x2, y2, label_x, label_y, anchor = _ARROWS[(direction, way)]
    # The head: a triangle at (x2, y2), pointing the way the arrow runs.
    dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
    back_x, back_y = x2 - 9 * dx, y2 - 9 * dy
    head = f"{x2},{y2} {back_x - 5 * dy},{back_y - 5 * dx} {back_x + 5 * dy},{back_y + 5 * dx}"
    names = ", ".join(local_name(other) for other in others)
    says = _TOOLTIP[way].format(
        module=local_name(module), direction=direction.lower(), others=names
    )
    return (
        f'<g class="connection" data-direction="{direction}" data-way="{way}" '
        f'style="{_ARROW_STYLE[way]}">'
        f"<title>{escape(says)}</title>"
        f'<line x1="{x1}" y1="{y1}" x2="{back_x}" y2="{back_y}" stroke-width="3"/>'
        f'<polygon points="{head}" stroke-width="1"/>'
        f'<text x="{label_x}" y="{label_y}" text-anchor="{anchor}" '
        f'dominant-baseline="middle" style="stroke: none">{escape(names)}</text>'
        "</g>"
    )


def _rollers_and_belts() -> str:
    """The rollers, silver cylinders lying east–west, and between each two the dark strip of a
    belt conveyor with its light pads."""
    parts = []
    for row in range(_ROLLERS):
        y = _FIRST_ROLLER + row * _ROLLER_STEP
        parts.append(
            f'<rect x="{_LEFT + 10}" y="{y}" width="{_SIZE - 20}" height="{_ROLLER_HEIGHT}" '
            'rx="3" fill="url(#module-roller)"/>'
            f'<rect x="{_LEFT + 10}" y="{y}" width="7" height="{_ROLLER_HEIGHT}" rx="2" '
            f'fill="{_ROLLER_END}"/>'
            f'<rect x="{_RIGHT - 17}" y="{y}" width="7" height="{_ROLLER_HEIGHT}" rx="2" '
            f'fill="{_ROLLER_END}"/>'
        )
        if row == _ROLLERS - 1:
            continue
        belt_y = y + _ROLLER_HEIGHT + 2
        parts.append(
            f'<rect x="{_LEFT + 18}" y="{belt_y}" width="{_SIZE - 36}" height="5" '
            f'fill="{_BELT}"/>'
        )
        # The pads sit in turn further west and further east, as on the photograph.
        for pad_x in (30, 92) if row % 2 == 0 else (46, 108):
            parts.append(
                f'<rect x="{_LEFT + pad_x}" y="{belt_y}" width="22" height="5" '
                f'fill="{_BELT_PAD}"/>'
            )
    return "".join(parts)


def _frame() -> str:
    """The green frame, with a silver joint at each corner."""
    corners = "".join(
        f'<circle cx="{x}" cy="{y}" r="5" fill="#bdbdbd"/>'
        for x in (_LEFT, _RIGHT)
        for y in (_TOP, _BOTTOM)
    )
    return (
        f'<rect x="{_LEFT}" y="{_TOP}" width="{_SIZE}" height="{_SIZE}" '
        f'fill="{_INSIDE}" stroke="{_FRAME}" stroke-width="6"/>{corners}'
    )


def _box_label(box: IRI) -> str:
    """What the cardboard box says: the box's name, or, for a box named by a UUID, `Box-` and
    the first four characters of the UUID, as the monitor's disc shows those four: a box's whole
    name, `Box-<UUID>`, does not fit on it."""
    name = local_name(box)
    if name.startswith(UUID_BOX_PREFIX):
        return name[: len(UUID_BOX_PREFIX) + 4]
    return name


def _held_box(box: IRI) -> str:
    """A cardboard box in the middle of the module, with its name on it (`_box_label`), and its
    whole IRI as its title."""
    size = 84
    x, y = _MIDDLE_X - size // 2, _MIDDLE_Y - size // 2
    return (
        f'<g class="held-box"><title>{escape(box)}</title>'
        f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="2" fill="{_BOX}" '
        f'stroke="{_BOX_EDGE}" stroke-width="2"/>'
        f'<rect x="{_MIDDLE_X - 6}" y="{y + 1}" width="12" height="{size - 2}" '
        f'fill="{_BOX_TAPE}"/>'
        f'<text x="{_MIDDLE_X}" y="{_MIDDLE_Y}" text-anchor="middle" '
        f'dominant-baseline="middle" font-weight="bold" fill="{_BOX_TEXT}" '
        f'stroke="{_BOX}" stroke-width="4" paint-order="stroke">'
        f"{escape(_box_label(box))}</text></g>"
    )


def _north() -> str:
    """Which way is north."""
    return (
        '<g style="fill: var(--muted); stroke: var(--muted)">'
        '<line x1="24" y1="44" x2="24" y2="22" stroke-width="2"/>'
        '<polygon points="24,14 19,24 29,24" stroke-width="1"/>'
        '<text x="24" y="58" text-anchor="middle" style="stroke: none">N</text></g>'
    )


def top_view(
    module: IRI, held: IRI | None, connections: Sequence[DirectedConnection]
) -> str:
    """`module` from above, holding `held` if it holds a box, with the connections of
    `connections` that lead from or to it, as an inline SVG element."""
    sides = _sides(module, connections)
    arrows = "".join(
        _arrow(module, direction, way, sides[(direction, way)])
        for direction, way in _ARROWS
        if sides.get((direction, way))
    )
    held_text = f"holds {local_name(held)}" if held else "holds no box"
    label = f"{local_name(module)} from above; it {held_text}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'role="img" aria-label="{escape(label)}" '
        'style="display: block; width: 100%; max-width: 420px; height: auto; '
        'margin: 0 auto; font-family: var(--sans); font-size: 12px">'
        "<defs>"
        '<linearGradient id="module-roller" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#fbfbfb"/>'
        '<stop offset="0.45" stop-color="#cfcfcf"/>'
        '<stop offset="0.75" stop-color="#8a8a8a"/>'
        '<stop offset="1" stop-color="#d6d6d6"/>'
        "</linearGradient>"
        "</defs>"
        f"{_north()}{_frame()}{_rollers_and_belts()}"
        f"{_held_box(held) if held else ''}{arrows}"
        "</svg>"
    )


# -- The adjacency matrix ---------------------------------------------------------------------- #

# The side of a cell, and the room a character of a module's name takes, in the drawing's units.
_CELL = 30
_CHARACTER = 7.4
# How far the matrix may grow beyond its own size: a small station's fits its box without
# drawing its letters several times their size.
_MOST_SCALE = 2.5


def _in_number_order(module: IRI) -> tuple:
    """A sort key that puts Module2 before Module10: the local name, its numbers as numbers."""
    parts = re.split(r"(\d+)", local_name(module))
    return (*((int(p) if p.isdigit() else p) for p in parts), str(module))


def _cell_text(direction: Direction | None, both_ways: bool) -> str:
    """The side a connection leaves by, as its first letter, or a dot if the graph names none;
    with a double arrow where the two modules connect both ways."""
    return (direction[0] if direction else "•") + ("↔" if both_ways else "")


def _matrix_cell(
    source: IRI,
    target: IRI,
    x: int,
    y: int,
    leads: dict[tuple[IRI, IRI], Direction | None],
) -> str:
    """The cell of the row of `source` and the column of `target`: the side by which `source`
    hands boxes to `target`, if it does, and empty otherwise."""
    group = (
        f'<g class="cell" data-from="{escape(local_name(source))}" '
        f'data-to="{escape(local_name(target))}">'
    )

    def square(fill: str) -> str:
        return (
            f'<rect x="{x}" y="{y}" width="{_CELL}" height="{_CELL}" '
            f'style="stroke: var(--panel-border); fill: {fill}"/>'
        )

    if (source, target) not in leads:
        return f"{group}{square('none')}</g>"
    direction = leads[(source, target)]
    both_ways = (target, source) in leads
    says = f"{local_name(source)} hands boxes"
    says += f" {direction.lower()}" if direction else ""
    says += f" to {local_name(target)}"
    says += "; the knowledge graph names no direction" if direction is None else ""
    says += f", and {local_name(target)} hands boxes back" if both_ways else ""
    return (
        f"{group}<title>{escape(says)}</title>"
        f"{square('var(--accent); fill-opacity: 0.45')}"
        f'<text x="{x + _CELL // 2}" y="{y + _CELL // 2}" text-anchor="middle" '
        'dominant-baseline="middle" font-weight="bold" font-size="15" style="fill: var(--fg)">'
        f"{escape(_cell_text(direction, both_ways))}</text></g>"
    )


def adjacency_matrix(here: IRI, connections: Sequence[DirectedConnection]) -> str:
    """The connections as a classic adjacency matrix, as inline SVG that scales to the width it
    is given, up to two and a half times its own size: a row for each module and a column for
    each, in the order of their numbers. A row
    is the module a connection leaves, a column the module it leads to, and the cell says the
    side it leaves by, with a double arrow where the two connect both ways. The row of `here` is
    marked."""
    leads: dict[tuple[IRI, IRI], Direction | None] = {}
    for connection in connections:
        leads.setdefault((connection.source, connection.target), connection.direction)
    modules = sorted(
        {here} | {module for pair in leads for module in pair}, key=_in_number_order
    )
    names = [local_name(module) for module in modules]
    # Room for the longest name, left of the rows and above the columns.
    margin = round(max(len(name) for name in names) * _CHARACTER) + 12
    size = margin + len(modules) * _CELL + 2
    columns = "".join(
        f'<g class="column" data-module="{escape(name)}">'
        f'<text transform="translate({margin + j * _CELL + _CELL // 2}, {margin - 6}) '
        f'rotate(-90)" dominant-baseline="middle" style="fill: var(--fg)">'
        f"{escape(name)}</text></g>"
        for j, name in enumerate(names)
    )
    rows = []
    for i, (source, name) in enumerate(zip(modules, names, strict=True)):
        y = margin + i * _CELL
        is_here = source == here
        mark = (
            f'<rect x="0" y="{y}" width="{size}" height="{_CELL}" '
            'style="fill: var(--accent); fill-opacity: 0.25"/>'
            if is_here
            else ""
        )
        cells = "".join(
            _matrix_cell(source, target, margin + j * _CELL, y, leads)
            for j, target in enumerate(modules)
        )
        rows.append(
            f'<g class="row{" here" if is_here else ""}" data-module="{escape(name)}">'
            f'{mark}<text x="{margin - 6}" y="{y + _CELL // 2}" text-anchor="end" '
            f'dominant-baseline="middle"{' font-weight="bold"' if is_here else ""} '
            f'style="fill: var(--fg)">{escape(name)}</text>{cells}</g>'
        )
    label = f"Adjacency matrix of the connections {local_name(here)} read"
    return (
        f'<svg class="adjacency-matrix" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" role="img" aria-label="{escape(label)}" '
        f'style="display: block; margin: 0 auto; width: 100%; '
        f"max-width: {round(size * _MOST_SCALE)}px; "
        'height: auto; font-family: var(--sans); font-size: 13px">'
        f"{columns}{''.join(rows)}</svg>"
    )
