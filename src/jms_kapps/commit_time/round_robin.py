"""The round-robin benchmark strategy: every entry module spawns at a fixed rate.

The WarehouseManagementSystem takes the entry modules in turn, the first after the last, and sends
each box to the exit straight below its origin (`straight_below`). So every
entry module gets every n-th spawn, and with the real module strategy each box takes the same
straight lane. Where the entry module whose turn it is is not free, the strategy answers it all the
same, a pair that the WMS refuses as no candidate: the slot is lost, and never given to another
module, so the order never depends on which module happens to be free.

The entry modules are the modules no connection leads into, in the order of their numbers. The
strategy is made for a grid with its entries on the top row, as the shipped `grid` is. It is a WMS
strategy only, named `roundrobin` in its entry-point group (`pyproject.toml`) and labelled "Round
robin (synthetic)". It never writes, reads the store or waits.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping, Sequence

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.strategies import Choose, WmsStrategy


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


def _in_number_order(module: IRI) -> tuple[int, str]:
    """A sort key that puts `Module2` before `Module10`; a module without a number comes first."""
    number = re.search(r"(\d+)$", str(module))
    return (int(number.group(1)) if number else 0, str(module))


def entry_modules(connections: Sequence[DirectedConnection]) -> list[IRI]:
    """The modules no connection leads into, in the order of their numbers."""
    targets = {c.target for c in connections}
    return sorted({c.source for c in connections} - targets, key=_in_number_order)


class RoundRobinWmsStrategy(WmsStrategy):
    """Takes the entry modules in turn and sends each box to the exit straight below. A turn
    whose entry module is not among the candidates is answered all the same, which the WMS
    refuses as no candidate, and the next call takes the next module.

    The turn advances once per call. The spawn timer starts each spawn on its own thread, so a
    lock guards it. A spawn that the WMS refuses before it asks the strategy does not advance
    it."""

    label = "Round robin (synthetic)"

    def __init__(self) -> None:
        self._turn = 0
        self._lock = threading.Lock()

    def origin_and_destination(
        self,
        candidates: Mapping[IRI, Sequence[IRI]],
        connections: Sequence[DirectedConnection],
        choose: Choose,
    ) -> tuple[IRI, IRI]:
        entries = entry_modules(connections)
        with self._lock:
            origin = entries[self._turn % len(entries)]
            self._turn += 1
        return origin, straight_below(origin, connections)


ROUND_ROBIN_WMS_STRATEGY = RoundRobinWmsStrategy()
"""`roundrobin` of the WMS strategies, as jms_kapps registers it."""
