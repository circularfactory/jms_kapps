"""The southward benchmark strategies (iteration one of the evaluation's harness).

On a grid with its entries on the top row and its exits on the bottom row, every module asks only
its southern neighbour, and the WarehouseManagementSystem sends each box to the exit straight below
its origin: traffic that is known and repeatable, which a benchmark needs. Both strategies are named
`southward`, in the entry-point group of their kind (`pyproject.toml`), and are labelled
"Southward (synthetic)". Neither writes, reads the store or waits: each chooses from what its asker
passes in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.strategies import Choose, ModuleStrategy, WmsStrategy


def south_of(module: IRI, connections: Iterable[DirectedConnection]) -> IRI | None:
    """The module a connection of `module` leads to southward, or None."""
    return next(
        (
            c.target
            for c in connections
            if c.source == module and c.direction == "South"
        ),
        None,
    )


def straight_below(origin: IRI, connections: Sequence[DirectedConnection]) -> IRI:
    """The module reached from `origin` by going south until no connection leads south any more.
    It is `origin` itself where none leads south."""
    seen = {origin}
    here = origin
    while (south := south_of(here, connections)) is not None and south not in seen:
        seen.add(south)
        here = south
    return here


class SouthwardModuleStrategy(ModuleStrategy):
    """Asks only the southern neighbour of the module, whatever the box's destination, and never a
    detour. Where no connection leads south, it answers none."""

    label = "Southward (synthetic)"

    def neighbours_to_ask(
        self,
        module: IRI,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> list[IRI]:
        south = south_of(module, connections)
        return [] if south is None else [south]


class SouthwardWmsStrategy(WmsStrategy):
    """Sends each box to the exit straight below its origin. The WMS's chooser picks an origin
    among the candidates whose exit straight below is one of its destinations. Where there is
    none, it answers the first candidate origin with its exit straight below, a pair that the WMS
    refuses as no candidate, with a reason that names this strategy: the spawn ends in a refusal,
    never in an error."""

    label = "Southward (synthetic)"

    def origin_and_destination(
        self,
        candidates: Mapping[IRI, Sequence[IRI]],
        connections: Sequence[DirectedConnection],
        choose: Choose,
    ) -> tuple[IRI, IRI]:
        below = {origin: straight_below(origin, connections) for origin in candidates}
        possible = [o for o in candidates if below[o] in candidates[o]]
        origin = choose(possible) if possible else next(iter(candidates))
        return origin, below[origin]


SOUTHWARD_MODULE_STRATEGY = SouthwardModuleStrategy()
"""`southward` of the module strategies, as jms_kapps registers it."""

SOUTHWARD_WMS_STRATEGY = SouthwardWmsStrategy()
"""`southward` of the WMS strategies, as jms_kapps registers it."""
