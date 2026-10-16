"""The two test strategies of jms_kapps 52, registered in the tests only, and a fake set of entry
points to register them in.

`SouthOnly` is a module strategy that asks only the southern neighbour, from the connections'
directions. `ExitStraightBelow` is a WMS strategy that sends each box to the exit straight below its
origin, from the connections: the module it reaches by going south until no connection leads south
any more. Both are synthetic, so their labels end in "(synthetic)". Neither is a benchmark strategy:
those live in the `evaluation` folder, built by their own tickets.

`entry_points` stands in for the installed entry points: a function that gives the entries of a
group, as the discovery of `jms_kapps.uc2.strategies` asks for them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib.metadata import EntryPoint

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.strategies import (
    MODULE_STRATEGIES,
    REAL_MODULE_STRATEGY,
    WMS_STRATEGIES,
    Choose,
    ModuleStrategy,
    WmsStrategy,
)


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


class SouthOnly(ModuleStrategy):
    """Asks only the southern neighbour of the module, whatever the box's destination."""

    label = "South only (synthetic)"

    def __init__(self) -> None:
        # Every question the strategy was asked: (module, destination, came_from, ahead).
        self.asked: list[tuple[IRI, IRI, IRI | None, IRI | None]] = []

    def neighbours_to_ask(
        self,
        module: IRI,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> list[IRI]:
        self.asked.append((module, destination, came_from, ahead))
        south = south_of(module, connections)
        return [] if south is None else [south]


class ExitStraightBelow(WmsStrategy):
    """Sends each box to the exit straight below its origin: the first candidate origin the
    chooser picks whose module straight below is one of its candidate destinations."""

    label = "Exit straight below (synthetic)"

    def origin_and_destination(
        self,
        candidates: Mapping[IRI, Sequence[IRI]],
        connections: Sequence[DirectedConnection],
        choose: Choose,
    ) -> tuple[IRI, IRI]:
        below = {origin: straight_below(origin, connections) for origin in candidates}
        possible = [o for o in candidates if below[o] in candidates[o]]
        origin = choose(possible)
        return origin, below[origin]


def straight_below(origin: IRI, connections: Sequence[DirectedConnection]) -> IRI:
    """The module reached from `origin` by going south until no connection leads south."""
    here = origin
    while (south := south_of(here, connections)) is not None:
        here = south
    return here


class Answers(ModuleStrategy):
    """A module strategy that answers what the test sets for a module, outside the contract or
    not, and as `real` does for every other module."""

    label = "Answers as set (synthetic)"

    def __init__(self, answers: Mapping[IRI, Sequence[IRI]]) -> None:
        self.answers = answers

    def neighbours_to_ask(self, module, destination, came_from, ahead, connections):
        if module in self.answers:
            return list(self.answers[module])
        return REAL_MODULE_STRATEGY.neighbours_to_ask(
            module, destination, came_from, ahead, connections
        )


class Pair(WmsStrategy):
    """A WMS strategy that answers the pair the test sets, a candidate or not."""

    label = "Pair as set (synthetic)"

    def __init__(self, origin: IRI, destination: IRI) -> None:
        self.pair = (origin, destination)

    def origin_and_destination(self, candidates, connections, choose):
        return self.pair


# What the fake entry points below load: one object each, by its name in this module.
SOUTH_ONLY = SouthOnly()
EXIT_STRAIGHT_BELOW = ExitStraightBelow()

# The test strategies, as an installation of the tests would register them: one entry in the
# group of each kind, named as the strategy.
TEST_ENTRIES: dict[str, list[tuple[str, str]]] = {
    MODULE_STRATEGIES: [("south", "_strategies:SOUTH_ONLY")],
    WMS_STRATEGIES: [("exit-below", "_strategies:EXIT_STRAIGHT_BELOW")],
}

# `real`, registered as jms_kapps' pyproject registers it.
REAL_ENTRIES: dict[str, list[tuple[str, str]]] = {
    MODULE_STRATEGIES: [("real", "jms_kapps.uc2.strategies:REAL_MODULE_STRATEGY")],
    WMS_STRATEGIES: [("real", "jms_kapps.uc2.strategies:REAL_WMS_STRATEGY")],
}


def entry_points(
    *registrations: Mapping[str, list[tuple[str, str]]],
) -> Callable[[str], list[EntryPoint]]:
    """A fake set of entry points: those of each of `registrations`, by group, in order, each as
    (name, value)."""

    def of(group: str) -> list[EntryPoint]:
        return [
            EntryPoint(name=name, value=value, group=group)
            for registration in registrations
            for name, value in registration.get(group, [])
        ]

    return of
