"""The waiting order of one FlexConveyorModule: the boxes that asked its Reserve, in the order each
first asked, so that the module, once it is free, serves the box that has waited longest.

A box takes its place at its first ask and keeps it, whatever the module answered: a box refused
through a look-ahead keeps its place in the order of the module that refused it, the inner ask
included, which a module asks on behalf of a box that is still on the module before it. The
WarehouseManagementSystem's Reserve for a spawn takes no place: its box is not in the graph yet,
and it waits behind every box in the order.

A box leaves the order once it has arrived on the module, or when it moved elsewhere or its
transport ended. The order sees the move itself when the box asks from another module, and the
module finds the rest in the graph (`FlexConveyorModule`). A box that has not asked for a while is
waiting for something else first, such as the module before this one: it keeps its place, but comes
first no more until it asks again, so that it cannot hold a free module for a box that cannot come.

A module that becomes free serves the asker that has waited longest. A box refused through a
look-ahead keeps its place in the order of the module that refused it, the inner ask included, and
a spawn's Reserve waits behind every box already in that order. An asker leaves the order when its
box moves elsewhere or its transport ends. A box that has stopped asking comes first no more:
without that, a free module would wait for a box whose own way here is held by a box that waits
for this module. A box that has asked no more for long is forgotten, and leaves the order when the
reservation it was given lapses.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from kapps_triplestore_interface import IRI


@dataclass
class Asker:
    """A box in the waiting order: when it first asked (its place) and last asked, where it was
    at its last ask (`at`), and which module asked (`via`): the module the box is on, or the
    module that asked on its behalf, in a look-ahead. `warned_left_out` holds each next module
    that the module's look-ahead has left out for the box and warned about, and that has not
    answered since."""

    box: IRI
    place: float
    last: float
    at: IRI
    via: IRI
    warned_left_out: set[IRI] = field(default_factory=set)

    def moved_from(self, at: IRI) -> bool:
        """Whether a box that is now at `at` has moved elsewhere since it took its place: neither
        where it was nor onto the module that asked for it."""
        return at not in (self.at, self.via)


class WaitingOrder:
    """The boxes that asked one module's Reserve, by their place.

    Args:
        asking_seconds: How long after its last ask a box still counts as asking, and so comes
            first.
        forgotten_seconds: How long after its last ask a box is forgotten: one whose transport
            ended without the module hearing of it asks no more.
    """

    def __init__(self, asking_seconds: float, forgotten_seconds: float) -> None:
        self.asking_seconds = asking_seconds
        self.forgotten_seconds = forgotten_seconds
        # Insertion order is the order of the places.
        self._askers: dict[IRI, Asker] = {}

    def asked(self, box: IRI, *, at: IRI, via: IRI, now: float) -> None:
        """`via` asked for `box`, which is at `at`, at the time `now`: `box` takes its place, or
        keeps it, or takes a new one at the end if it has moved elsewhere since."""
        for quiet in [
            a.box
            for a in self._askers.values()
            if now - a.last > self.forgotten_seconds
        ]:
            del self._askers[quiet]
        asker = self._askers.get(box)
        if asker is not None and asker.moved_from(at):
            del self._askers[box]
            asker = None
        if asker is None:
            self._askers[box] = Asker(box, place=now, last=now, at=at, via=via)
            return
        asker.last, asker.at, asker.via = now, at, via

    def ahead_of(self, box: IRI | None, now: float) -> Iterator[Asker]:
        """The boxes ahead of `box` that still ask, by their place; every box that still asks
        when `box` is None, as for the WarehouseManagementSystem's spawn."""
        for asker in list(self._askers.values()):
            if asker.box == box:
                return
            if now - asker.last <= self.asking_seconds:
                yield asker

    def first_left_out(self, box: IRI, module: IRI) -> bool:
        """Whether the look-ahead for `box` leaves `module` out for the first time since `box`
        took its place, or since `module` last answered: the module warns about it only then. It
        is kept with the box's place, and so forgotten when the box leaves the order. A box with
        no place, the WarehouseManagementSystem's, is always left out for the first time."""
        asker = self._askers.get(box)
        if asker is None:
            return True
        if module in asker.warned_left_out:
            return False
        asker.warned_left_out.add(module)
        return True

    def answered(self, box: IRI, module: IRI) -> None:
        """`module` answered the look-ahead for `box`: once it is left out again, that is a first
        time again."""
        asker = self._askers.get(box)
        if asker is not None:
            asker.warned_left_out.discard(module)

    def leave(self, box: IRI) -> None:
        """`box` leaves the order: it has arrived, moved elsewhere, or its transport ended."""
        self._askers.pop(box, None)
