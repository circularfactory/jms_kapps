"""The wires of the monitor's drawing: each pair of modules that a connection joins, and the way each
of its connections leads.

A connection leads one way, from the module that has it (`fc:hasConnection` / `fc:connectsTo`), so
a pair of modules joined both ways is one wire that leads to both. A wire is drawn from those
connections alone, on one of the monitor's reads, so a page that shows it causes no read of the
store.

A wire says nothing of the way boxes go over it: the monitor computes no route. Only the module
that holds a box knows where it goes next, and it decides that again at every hop. All
information at the monitor stems from graph polling, so the monitor maps destinations, not
routes.
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
