"""The wires of the monitor's drawing: each pair of modules that a connection joins, and the way each
of its connections leads.

A connection leads one way, from the module that has it (`fc:hasConnection` / `fc:connectsTo`), so
a pair of modules joined both ways is one wire that leads to both. A wire is drawn from those
connections alone, on one of the monitor's reads, so a page that shows it causes no read of the
store.

A wire says nothing of the way boxes go over it: the monitor computes no route. Only the module
that holds a box knows where it goes next, and it decides that again at every hop. (Changed on
2026-10-10, jms_kapps 24, decided by Etienne on 2026-10-06: "all information at the monitor must
stem from graph polling ... no one but the module holding a box knows the planned route at the
moment. calculating one on your own is not useful ... we can map destinations but not routes".
Until then the first sentence ended "and the way a box from an entry module to an exit module hops
over it", and the second paragraph went on: "The hops are those a box from an entry module to an
exit module makes on a shortest path (`routing.hops_from_entries_to_exits`), found on one of the
monitor's reads with the search every module makes, so a page that shows them causes no read of
the store. A connection that no such hop takes leads where no box from an entry to an exit goes:
back toward an entry module, or on past an exit.", and then to a box that takes such a
connection as its detour, with a note of jms_kapps 43. A third paragraph, added on 2026-10-06 by
jms_kapps 45, read: "Roles are optional: where the graph names no entry module, every module counts
as one, and where it names no exit module, every module counts as an exit
(`topology.entries_and_exits`), as the WarehouseManagementSystem's spawns do. So on the cross,
which names no roles, every connection carries a hop both ways.")
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.monitor.reads import ModuleRow


@dataclass(frozen=True)
class Wire:
    """Two modules, in the order of their IRIs, and a connection between them at least.

    `leads_to` holds each of the two that a connection leads to, in the order of `between`.
    """

    between: tuple[IRI, IRI]
    leads_to: tuple[IRI, ...]


def wires(modules: Sequence[ModuleRow]) -> list[Wire]:
    """The wires between `modules`, in the order of their modules' IRIs.

    A connection to something that is not one of `modules`, or from a module to itself, joins no
    two modules, and is no wire.
    """
    known = {module.resource for module in modules}
    leads_to: dict[tuple[IRI, IRI], set[IRI]] = defaultdict(set)
    for module in modules:
        for connection in module.connections:
            source, target = module.resource, connection.to
            if target not in known or target == source:
                continue
            low, high = sorted((source, target), key=str)
            leads_to[low, high].add(target)
    return [
        Wire(between, tuple(end for end in between if end in leads_to[between]))
        for between in sorted(leads_to, key=lambda pair: (str(pair[0]), str(pair[1])))
    ]
