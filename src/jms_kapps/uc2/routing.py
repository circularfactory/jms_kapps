"""Where a box goes next: the first module on a shortest path to its destination, and the one other
module it may step to when that one declines.

A path is counted in hops over the station's connections (`fc:hasConnection` / `fc:connectsTo`),
and each connection leads one way, from the module that has it. Of several shortest paths, the one
whose next module has the lowest IRI wins, so every module decides the same way on the same graph.
The old use case 2 ran Dijkstra with unit weights, which finds the same paths. A box never goes
straight back to the module it came from: a path that runs back through it counts for nothing
(`avoid`), unless a crash has left a neighbour out for the box (below).

The search runs in memory over the connections it is given. A module reads them from the graph at
every decision and routes by no table: a table filled once at start-up is a race against the other
modules' start. What it read last it keeps only for its dashboard.

**The detour.** When the next module declines, a box tries one other neighbour first, before it
waits and asks again (`detour`): not the module it came from, and one from which a path leads to
the destination without passing the box's module again, the nearest to the destination first.
So the module a next module ties with is the detour.

**After a crash.** A module that cannot reach a neighbour it asks leaves that neighbour out for the
box, and asks its strategy again with the connections to each module left out removed and with no
module the box came from: so with `real`, `next_module_choices` and `detour` are then given the
connections without those to the modules left out, and avoid no module the box came from, which
may then be the next module or the detour. The functions here do not change: they route over the
connections they are given.

**A pure two-way corridor** (`pure_corridor`) is a module whose only two connections both run both
ways: two boxes that meet head-on there, one each way, could wait for each other forever, with no
neighbour to step aside to. The load check refuses a topology that has one. Any other cycle of
connections loads: a box looks ahead before it moves, and takes a detour where it can, and boxes
on a cycle that still wait for each other raise the deadlock alarm, the visible backstop, which
fails nothing.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection, Iterable

from kapps_triplestore_interface import IRI


def next_module(
    connections: Iterable[tuple[IRI, IRI]], here: IRI, destination: IRI
) -> IRI | None:
    """The module a box on `here` goes to next on its way to `destination`.

    Args:
        connections: Each connection as (the module that has it, the module it leads to).

    Returns:
        The next module, or None when no path leads from `here` to `destination`, or `here` is
        the destination.
    """
    choices = next_module_choices(connections, here, destination)
    return choices[0] if choices else None


def _hops_to(
    connections: Iterable[tuple[IRI, IRI]],
    destination: IRI,
    *,
    without: Collection[IRI] = (),
) -> tuple[dict[IRI, int], dict[IRI, set[IRI]]]:
    """The hops to `destination` from every module that can reach it over paths that pass none of
    `without`, searched backwards from it, and the modules each connection leads to from each."""
    leads_to: dict[IRI, set[IRI]] = defaultdict(set)
    comes_from: dict[IRI, set[IRI]] = defaultdict(set)
    for source, target in connections:
        leads_to[source].add(target)
        if source not in without and target not in without:
            comes_from[target].add(source)
    hops = {destination: 0}
    frontier = deque([destination])
    while frontier:
        module = frontier.popleft()
        for previous in comes_from[module]:
            if previous not in hops:
                hops[previous] = hops[module] + 1
                frontier.append(previous)
    return hops, leads_to


def next_module_choices(
    connections: Iterable[tuple[IRI, IRI]],
    here: IRI,
    destination: IRI,
    *,
    avoid: Collection[IRI] = (),
) -> list[IRI]:
    """Every module that is first on a shortest path from `here` to `destination`, the lowest IRI
    first: the next module, then the modules it ties with.

    Args:
        connections: Each connection as (the module that has it, the module it leads to).
        avoid: The modules no path may pass: the module the box came from, so that it never goes
            straight back, for every choice of the next module.

    Returns:
        The modules, or none when no path leads from `here` to `destination`, or `here` is the
        destination.
    """
    hops, leads_to = _hops_to(connections, destination, without=set(avoid) - {here})
    if here == destination or here not in hops:
        return []
    return sorted(
        (n for n in leads_to[here] if n not in avoid and hops.get(n) == hops[here] - 1),
        key=str,
    )


def detour(
    connections: Iterable[tuple[IRI, IRI]],
    here: IRI,
    destination: IRI,
    *,
    avoid: Collection[IRI],
) -> IRI | None:
    """The one other module a box on `here` tries when its next module declines, before it waits
    and asks again, or None if there is none.

    It is a module a connection of `here` leads to, none of `avoid` (the next module, and the
    module the box came from), from which a path leads to `destination` without passing `here`
    again: a step that only leads back is no detour. Of several, the one nearest to the
    destination wins, then the lowest IRI, so the module the next module ties with comes first.
    """
    connections = list(connections)
    hops, leads_to = _hops_to(connections, destination, without={here})
    candidates = [
        n for n in leads_to[here] if n not in avoid and n != here and n in hops
    ]
    return min(candidates, key=lambda n: (hops[n], str(n)), default=None)


def reachable(connections: Iterable[tuple[IRI, IRI]], origin: IRI) -> set[IRI]:
    """Every module a box on `origin` can reach: each module the connections lead to from it, one
    way, as listed, over any number of hops. `origin` itself only where a cycle leads back to it.

    The WarehouseManagementSystem picks only a destination that its origin reaches, for every
    topology.
    """
    leads_to: dict[IRI, set[IRI]] = defaultdict(set)
    for source, target in connections:
        leads_to[source].add(target)
    reached: set[IRI] = set()
    frontier = deque([origin])
    while frontier:
        for following in leads_to[frontier.popleft()]:
            if following not in reached:
                reached.add(following)
                frontier.append(following)
    return reached


def pure_corridor(
    connections: Iterable[tuple[IRI, IRI]],
) -> tuple[IRI, IRI, IRI] | None:
    """A module whose only two connections both run both ways, with the two modules it connects
    to, the lower IRI first; or None if no module is one. Of several, the module with the lowest
    IRI is named.

    Two boxes that meet head-on on such a module, one each way, could wait for each other
    forever: neither has a neighbour to step aside to. A module with a third connection, or one
    of whose two connections runs one way, is none.
    """
    links = set(connections)
    leads_to: dict[IRI, set[IRI]] = defaultdict(set)
    for source, target in links:
        if source != target:
            leads_to[source].add(target)
    for module in sorted(leads_to, key=str):
        ends = leads_to[module]
        if len(ends) == 2 and all((end, module) in links for end in ends):
            one, other = sorted(ends, key=str)
            return module, one, other
    return None
