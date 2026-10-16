"""Who chooses where a box goes: the strategy of the modules and the strategy of the
WarehouseManagementSystem, each chosen by its name when use case 2 starts.

**Two kinds.** A strategy is an object with a `label`, a person's name for it. Its kind is its
class and the entry-point group it is registered in; its name is its entry's name, the one that
`jms-uc2 --module-strategy` and `--wms-strategy` take.

- A **module strategy** (`ModuleStrategy`) answers `neighbours_to_ask`: given the module, the box's
  destination, the module the box came from (or none), the module reserved ahead for the box (or
  none), and the station's connections as the module read them, each with its source, its target
  and its direction, it answers the neighbours the module asks, in order, at most two: the first,
  and the detour if the first declines; none where the box cannot go on. A module asks it wherever
  it chooses a neighbour: when it carries a box on, and at a look-ahead, where it asks the first.
  **After a crash** it is asked again, with the same signature: when a neighbour it named cannot
  be reached, or refuses "no live route on", the module leaves that neighbour out for the box and
  asks again with the connections to each module left out removed and with no module the box
  came from, which is then a valid choice; at a look-ahead, the module that holds the box stays
  the module it came from. So `real` and every strategy that names only neighbours a connection
  leads to need no change for it. (Added on 2026-10-11, jms_kapps 77, decided by Etienne: "The
  strategy is asked again with the connections to each left-out module removed and no module the
  box came from".)
- A **WMS strategy** (`WmsStrategy`) answers `origin_and_destination`: given the candidates of a
  spawn (each free origin the spawn allows, with the destinations it allows that the origin
  reaches, narrowed to what the request names), the station's connections as the WMS read them in
  the same read, and the WMS's chooser, which picks one module out of several, it answers one
  candidate pair. The WMS asks it only where it picks.

A strategy only chooses: it never writes, never reads the store, and never waits. The module and
the WMS read what they read anyway, and pass it in. Everything around the choice stays theirs:
the look-ahead before a module accepts, the bound of a reservation, the waiting order, the stop
rules, and what the store admits. So does what they make of an answer outside the contract: a
module keeps the first two neighbours, and drops one to which no connection of it leads; the WMS
refuses a spawn whose pair is not a candidate.

**`real`**, labelled "Real behaviour", is one strategy of each kind, and is the demonstration's
behaviour: a module asks the module reserved ahead first, while a connection leads there, and
otherwise the first module on a shortest path, and then the detour, which avoids the first and the
module the box came from (`routing`); the WMS's chooser picks the origin among the candidate
origins, then the destination among that origin's destinations. A synthetic strategy, one that
makes traffic a benchmark can repeat, says so: its label ends in "(synthetic)".

**Finding strategies.** Each kind has a Python entry-point group, declared in the pyproject of the
package that provides it: `jms_kapps.uc2.module_strategies` and `jms_kapps.uc2.wms_strategies`.
Each entry loads one strategy object, and the entry's name is the strategy's name; jms_kapps
registers `real` in both. An entry whose module cannot be imported, as when the `evaluation` folder
that holds the benchmark strategies is not there, is skipped with one WARNING; so is an object of
the other kind, or of none; and of two strategies of one kind with one name, the first found
counts, and one WARNING names the other. No registry holds strategies beyond the entry points.
Use case 2 imports nothing from the `evaluation` folder, so the demonstration runs without it.

(Added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07: "a strategy is chosen at
start in the Launcher, for the WMS and for the modules; the default is today's real behaviour".)
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.routing import detour, next_module_choices

if TYPE_CHECKING:
    from jms_kapps.uc2.discovery import DirectedConnection

logger = logging.getLogger(__name__)

MODULE_STRATEGIES = "jms_kapps.uc2.module_strategies"
"""The entry-point group of the module strategies."""

WMS_STRATEGIES = "jms_kapps.uc2.wms_strategies"
"""The entry-point group of the WMS strategies."""

REAL = "real"
"""The name of the demonstration's strategy of each kind, the default of both."""

Choose = Callable[[Sequence[IRI]], IRI]
"""Picks one module out of several. `random.choice` is the WarehouseManagementSystem's. (Moved here
from `wms` on 2026-10-10, jms_kapps 52, where it read "`random.choice` is the real one.", which
would now read as the strategy `real`.)"""


# The two kinds are plain classes, not abstract ones, so that each can be named as the kind it is
# (`found(ModuleStrategy)`); a strategy of a kind is an object of a subclass that answers its one
# question.


class ModuleStrategy:
    """The strategy by which a module chooses the neighbours it asks for a box. A subclass answers
    `neighbours_to_ask`."""

    label: str
    """A person's name for the strategy, as the Launcher's picker lists it."""

    def neighbours_to_ask(
        self,
        module: IRI,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> Sequence[IRI]:
        """The neighbours `module` asks for a box bound for `destination`, in order, at most
        two: the first, and the detour if the first declines; none where the box cannot go on.

        Args:
            module: The module that asks.
            destination: The box's destination, never `module`.
            came_from: The module the box came from, or None at its origin, at a look-ahead for
                the WarehouseManagementSystem's spawn, and when the module asks again after it
                left out a neighbour that cannot be reached (added on 2026-10-11, jms_kapps 77,
                decided by Etienne).
            ahead: The module reserved ahead for the box at `module`'s look-ahead, or None.
            connections: Every connection of the station, as `module` read them for this choice,
                without those to a module it left out for the box (added on 2026-10-11,
                jms_kapps 77, decided by Etienne).
        """
        raise NotImplementedError


class WmsStrategy:
    """The strategy by which the WarehouseManagementSystem picks the origin and the destination of
    a spawn. A subclass answers `origin_and_destination`."""

    label: str
    """A person's name for the strategy, as the Launcher's picker lists it."""

    def origin_and_destination(
        self,
        candidates: Mapping[IRI, Sequence[IRI]],
        connections: Sequence[DirectedConnection],
        choose: Choose,
    ) -> tuple[IRI, IRI]:
        """One candidate pair: an origin among `candidates`, and a destination among its own.

        Args:
            candidates: Each origin the spawn may start on, with the destinations it may end on
                that the origin reaches, both in their order. A module the request names is the
                only candidate for its end.
            connections: Every connection of the station, as the WarehouseManagementSystem read
                them with the modules.
            choose: The WarehouseManagementSystem's chooser. A module the request names is no
                pick: given it alone, the chooser answers it without picking.
        """
        raise NotImplementedError


class RealModuleStrategy(ModuleStrategy):
    """The demonstration's module strategy: the module reserved ahead, while a connection leads
    there, or else the first module on a shortest path, a tie going to the lowest IRI and no path
    running straight back to the module the box came from; then the detour (`routing.detour`)."""

    label = "Real behaviour"

    def neighbours_to_ask(
        self,
        module: IRI,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> list[IRI]:
        links = [(c.source, c.target) for c in connections]
        avoid = [] if came_from is None else [came_from]
        choices = next_module_choices(links, module, destination, avoid=avoid)
        first = (
            ahead
            if ahead is not None and (module, ahead) in links
            else next(iter(choices), None)
        )
        if first is None:
            return []
        second = detour(links, module, destination, avoid=[first, *avoid])
        return [first] if second is None else [first, second]


class RealWmsStrategy(WmsStrategy):
    """The demonstration's WMS strategy: the chooser picks the origin among the candidate
    origins, then the destination among that origin's destinations."""

    label = "Real behaviour"

    def origin_and_destination(
        self,
        candidates: Mapping[IRI, Sequence[IRI]],
        connections: Sequence[DirectedConnection],
        choose: Choose,
    ) -> tuple[IRI, IRI]:
        origin = choose(list(candidates))
        return origin, choose(candidates[origin])


REAL_MODULE_STRATEGY = RealModuleStrategy()
"""`real` of the module strategies, as jms_kapps registers it."""

REAL_WMS_STRATEGY = RealWmsStrategy()
"""`real` of the WMS strategies, as jms_kapps registers it."""


# Each kind: its entry-point group, and what a person calls one of it and several.
_KINDS: dict[type, tuple[str, str, str]] = {
    ModuleStrategy: (MODULE_STRATEGIES, "module strategy", "module strategies"),
    WmsStrategy: (WMS_STRATEGIES, "WMS strategy", "WMS strategies"),
}

EntryPoints = Callable[[str], Iterable[EntryPoint]]
"""The entries of an entry-point group, given its name."""


def installed(group: str) -> Iterable[EntryPoint]:
    """The entries of `group` that the installed packages register."""
    return entry_points(group=group)


class UnknownStrategy(ValueError):
    """No strategy of the kind asked for has the name asked for. The message names it and the
    strategies of its kind, for a person to read."""


def found[S: (ModuleStrategy, WmsStrategy)](
    kind: typing.Type[S],  # noqa: UP006 - the docs resolve `type` to two attributes
    entries: EntryPoints = installed,
) -> dict[str, S]:
    """The strategies of `kind` registered in its entry-point group, by name, in the order they
    were found. An entry is skipped, with one WARNING naming it, when its module cannot be
    imported, when it loads an object that is no strategy of `kind`, or when a strategy of `kind`
    with its name was found before it."""
    group, called, _ = _KINDS[kind]
    strategies: dict[str, S] = {}
    entry_of: dict[str, EntryPoint] = {}
    for entry in entries(group):
        if entry.name in strategies:
            logger.warning(
                "The %s %s (%s) is skipped: the %s %s (%s) was found first",
                called,
                entry.name,
                entry.value,
                called,
                entry.name,
                entry_of[entry.name].value,
            )
            continue
        try:
            strategy = entry.load()
        except Exception as error:  # noqa: BLE001 - any import error skips the entry
            logger.warning(
                "The %s %s (%s) is skipped: it cannot be loaded: %s",
                called,
                entry.name,
                entry.value,
                error,
            )
            continue
        if not isinstance(strategy, kind):
            logger.warning(
                "The %s %s (%s) is skipped: it is a %s, not a %s",
                called,
                entry.name,
                entry.value,
                type(strategy).__name__,
                kind.__name__,
            )
            continue
        strategies[entry.name] = strategy
        entry_of[entry.name] = entry
    return strategies


def chosen[S: (ModuleStrategy, WmsStrategy)](
    kind: typing.Type[S],  # noqa: UP006 - the docs resolve `type` to two attributes
    name: str,
    among: Mapping[str, S],
) -> S:
    """The strategy of `kind` named `name` among the strategies of that kind `among`.

    Raises:
        UnknownStrategy: If none has that name, naming it and the strategies of `kind`.
    """
    if name in among:
        return among[name]
    _, called, several = _KINDS[kind]
    known = ", ".join(among) if among else "none"
    raise UnknownStrategy(f"no {called} {name!r}; the {several} are {known}")
