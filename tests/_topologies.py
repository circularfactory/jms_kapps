"""The topologies the tests compare with, from sources that are not the code under test.

`topologies/` holds two files. `modules_preview_cross.json` is the first iteration's cross,
copied unchanged from the `paper` branch of the old use-case repository, at commit
d32011805be9199291eec0731acab241e5bd74f1. `shipped_as_seeded_before.json` is what the seed wrote
for each shipped topology before the shipped topologies became module lists (jms_kapps 44): every
module's types, label and connections, as `Topology.graph()` on main at c432426 wrote them from
the short topology documents of that time, one module to a line, with one change since: the
cross's modules are each a FlexConveyorModule.

(Changed on 2026-10-06, jms_kapps 45, decided by Etienne: the shipped cross names no roles, "in
the current topologies already the cross topology would make sense to have this". Until then
`shipped_as_seeded_before.json` held the cross as it was seeded at c432426, with Modules 1 and 2
typed "EntryModule" and Modules 4 and 5 "ExitModule", and this paragraph ended at "one module to
a line".)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

from jms_kapps.uc2.topology import (
    CONNECTION_KEY,
    CONNECTS_TO_KEY,
    DIRECTION_KEY,
    LABEL_KEY,
    PORT_KEY,
    TRANSIT_TIME_KEY,
    TYPE_KEY,
)
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS

TOPOLOGIES = Path(__file__).parent / "topologies"
FIRST_ITERATIONS_CROSS = TOPOLOGIES / "modules_preview_cross.json"
SEEDED_BEFORE: dict[str, list[dict]] = json.loads(
    (TOPOLOGIES / "shipped_as_seeded_before.json").read_text(encoding="utf-8")
)

_MODULE_CLASSES = {
    URIRef(FC_NS + kind) for kind in ("FlexConveyorModule", "EntryModule", "ExitModule")
}


def module_entry(
    local: str,
    *types: str,
    label: str | None = None,
    transit_time: object | None = None,
    links: Sequence[tuple[object, ...]] = (),
) -> dict:
    """One module of a module list, written as the first iteration wrote one: `local` is its local
    name in `fci:`, each type a class of `fc:`, and each link a (direction, module) pair, on port
    0, or a (direction, module, port) triple."""
    entry: dict = {"id": FCI_NS + local}
    if types:
        entry[TYPE_KEY] = [FC_NS + module_type for module_type in types]
    if label is not None:
        entry[LABEL_KEY] = [label]
    if transit_time is not None:
        entry[TRANSIT_TIME_KEY] = [transit_time]
    if links:
        entry[CONNECTION_KEY] = [
            {
                CONNECTS_TO_KEY: [f"{FCI_NS}{to}"],
                DIRECTION_KEY: [f"{FC_NS}{direction}"],
                PORT_KEY: [port[0] if port else 0],
            }
            for direction, to, *port in links
        ]
    return entry


def as_seeded(graph: Graph) -> list[dict]:
    """The modules `graph` holds, in the form of `shipped_as_seeded_before.json`: each module by
    its local name, in the order of its number, with its label, its types among the three module
    classes, and its connections as sorted (direction, module, port) lists."""
    modules = sorted(
        {s for s, o in graph.subject_objects(RDF.type) if o in _MODULE_CLASSES},
        key=lambda s: int(str(s).removeprefix(FCI_NS + "Module")),
    )
    return [
        {
            "id": str(module).removeprefix(FCI_NS),
            "label": str(graph.value(module, RDFS.label)),
            "types": sorted(
                str(o).removeprefix(FC_NS)
                for o in graph.objects(module, RDF.type)
                if o in _MODULE_CLASSES
            ),
            "connections": sorted(
                [
                    str(graph.value(c, URIRef(FC.hasDirection))).removeprefix(FC_NS),
                    str(graph.value(c, URIRef(FC.connectsTo))).removeprefix(FCI_NS),
                    graph.value(c, URIRef(FC.onPort)).toPython(),
                ]
                for c in graph.objects(module, URIRef(FC.hasConnection))
            ),
        }
        for module in modules
    ]
