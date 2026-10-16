"""The layout of a FlexConveyor: its modules, and which module each one hands boxes to.

**A topology is the first iteration's module list**, the one format in which machines that run
use case 2 exchange topologies: a JSON list of modules, as the first iteration of this use case
(the paper's artifact) wrote them. Each module is a JSON object with its `id`, the module's IRI in
`fci:`, and its properties under their IRIs, each IRI encoded as the OGM encodes a field name
(`IRI.lined`), with a list of values::

    [{"id": "http://w3id.org/circularfactory/FlexConveyorInstances#Module1",
      "http_c__s__s_www_d_w3_d_org_s_1999_s_02_s_22-rdf-syntax-ns_h_type":
          ["http://w3id.org/circularfactory/FlexConveyor#EntryModule"],
      "http_c__s__s_www_d_w3_d_org_s_2000_s_01_s_rdf-schema_h_label": ["Module 1"],
      "http_c__s__s_w3id_d_org_s_circularfactory_s_FlexConveyor_h_hasConnection": [
          {"http_c__s__s_w3id_d_org_s_circularfactory_s_FlexConveyor_h_connectsTo":
               ["http://w3id.org/circularfactory/FlexConveyorInstances#Module3"],
           "http_c__s__s_w3id_d_org_s_circularfactory_s_FlexConveyor_h_hasDirection":
               ["http://w3id.org/circularfactory/FlexConveyor#South"],
           "http_c__s__s_w3id_d_org_s_circularfactory_s_FlexConveyor_h_onPort": [0]}]},
     ...]

- `rdf:type` holds the module's types: `fc:FlexConveyorModule`, and the roles `fc:EntryModule`
  and `fc:ExitModule`, which are optional. A module that lists no type is a FlexConveyorModule,
  as the first iteration seeded it. Every type listed is written into the graph, where the first
  iteration's seed kept the first and dropped the rest.
- `rdfs:label` is optional. Without one, the pages show the module's local name.
- `fc:hasTransitTime` is optional: the module's own transit time, a list of one number, the seconds
  it takes to convey a box. 0 is allowed and means that it conveys at once, with no wait. A module
  without one takes the transit time of its launch (`seed.seed_topology`). (Added on 2026-10-07,
  jms_kapps 8, decided by Etienne.)
- `fc:hasConnection` holds the connections, each a node with the module it leads to
  (`fc:connectsTo`), its direction (`fc:hasDirection`: `fc:North`, `fc:East`, `fc:South` or
  `fc:West`) and its port (`fc:onPort`, 0 where none is given). A connection runs one way, as it is
  listed: a module that hands boxes back has a connection of its own. The first iteration's seed
  added the way back of every link, which would make a topology that runs one way run both ways.
  A node's own `id`, which some of the first iteration's files give, is not taken: the seed names
  a connection by its module and its direction.
- Every value is in a list, even where there is one. An IRI is written as a string or as
  `{"id": ...}`, as the first iteration wrote both.
- A key that is none of these is refused, so that a misspelt one is not silently dropped.

`parse_topology` reads a module list, and `Topology.module_list` writes one. Every topology this
package ships is one, below `topologies/`, and the first iteration's files load as they are.
In memory a topology is a `Topology`, which names each module by the local name of its IRI in
`fci:`, so that it reads without prefixes; the Launcher page shows the running one in those terms.
(Changed on 2026-10-06, jms_kapps 44, decided by Etienne: "the old topology worked. so use it here
too, adapt if you need too". Until then this head began: "A topology is a JSON document. The
Launcher seeds the graph from one, the Launcher page shows one, and `jms-uc2 --topology` names
one", and ended its first paragraph: "The document names each module by the local name of its IRI
in `fci:`, so it reads without prefixes:", above the example `{"modules": [{"id": "Module1",
"label": "Module 1", "type": "EntryModule", "connections": [{"direction": "South", "to":
"Module3", "port": 0}]}, ...]}`; the next paragraph began "A topology that
`Topology.model_validate` accepts is one the graph accepts"; and one more paragraph read:
"`parse_topology` also reads the format the paper's artifact exported, a list of modules keyed by
encoded IRIs, and adds the way back of every one-way link, as the artifact did when it seeded.")

The Launcher seeds the graph from a topology, and `jms-uc2 --topology` names one: a shipped one by
its name, the stem of its file, or else a file by its path. The Launcher page names a shipped one,
or sends a module list edited on it whole (`edited_topology`), never a path. The monitor builds no
topology: it lays out the modules the graph holds, by their IRIs, with `layout`, the walk a
topology's `places` uses.

A topology that `parse_topology` accepts is one the graph accepts: every connection leads to a
module of the same topology, and a module has at most one connection in each direction. No module
takes the IRI of the WarehouseManagementSystem, which every run seeds and starts beside its
topology. The monitor is the demo Launcher's, so the Launcher refuses a module on its IRI, and a
run without the Launcher, as a benchmark's, may have one. (Changed on 2026-10-10, jms_kapps 31,
decided by Etienne: the monitor's reservation moved into the Launcher. Until then the sentence read "No module takes
the IRI of the WarehouseManagementSystem or the monitor, which the Launcher seeds and starts beside
every topology.")

**A topology that is loaded holds at least one module**: with none, the WarehouseManagementSystem
and the monitor would run and no box could be spawned, so loading a topology, shipped or from a
file, or launching one edited on the page, refuses an empty module list with "a topology needs at
least one module". Only the load check refuses it: reading a module list, for the editor's layout
or an opened file, accepts the empty list, so that a person can edit it until it passes. A single
module loads. (Added on 2026-10-10, jms_kapps 69, decided by Etienne: the editor's "Empty grid"
passed the check and enabled "Launch this".)

**A topology that is loaded may hold no pure two-way corridor** (`routing.pure_corridor`): a
module whose only two connections both run both ways. Two boxes that meet head-on there, one each
way, could wait for each other forever, with no neighbour to step aside to, so loading a topology,
shipped or from a file, refuses one that has such a module, and names it and its two neighbours.
Any other cycle of connections loads, two-way connections too: a module accepts a box only where
the box can go on at once, and a box whose next module declines tries a detour first. Where a
cycle could still hold boxes, the deadlock alarm is the visible backstop, which fails nothing
(changed on 2026-10-11, jms_kapps 77, decided by Etienne; until then "a bound on a wait with
escalation, a build of its own, is the backstop"). (Changed on 2026-10-06, jms_kapps 43, decided
by Etienne: "it refuses only a pure two-way corridor, a module whose only two connections both run both ways; any other routed cycle loads".
Until then this paragraph read: "**A topology that is loaded must let every box from an entry to
an exit arrive.** A module refused as occupied waits and asks the same next module again, with no
bound, so two boxes that each wait for the other's module wait forever. Loading a topology,
shipped or from a file, therefore refuses one where the hops a box can make from an entry module
to an exit module run in a cycle, and names the modules of one such cycle (`routing.hop_cycle`).
Without such a cycle every wait of such boxes ends, because a box on its destination gives itself
back. The connections themselves may hold cycles that no such box takes, as the cross's do. The
WarehouseManagementSystem starts a box only on an entry module and ends it only on an exit module,
a named one too, so every box it spawns is covered. A topology that names no entry module counts
every module as one, and one that names no exit module counts every module as an exit, so that
the check errs on the safe side (`entries_and_exits`); the WarehouseManagementSystem spawns nothing
on such a topology." The sentences on a topology without roles had changed on 2026-10-05, KSM 413,
decided by Etienne.)

**Roles are optional** (`choices`). A box starts on an entry module where the topology names any,
and on any module where it names none; it ends on an exit module where the topology names any, and
on any module where it names none; never on its origin. The WarehouseManagementSystem spawns by
this rule and its form offers by it (`entries_and_exits`). The load check looks at no role. The
shipped cross names no roles, so every module of it can send a box to every other; the grid, the
tee and the pair name theirs. (Changed on 2026-10-06, jms_kapps 45, decided by Etienne: "in
general entry and exit module are roles. a module can be both", and roles are optional, as the
first iteration's WarehouseManagementSystem had them. Until then this paragraph read:
"`entries_and_exits` names the entry modules and the exit modules of a topology, a module without
roles counting as both: the monitor draws strong the hops a box from an entry module to an exit
module makes." Changed again on 2026-10-10, jms_kapps 24, decided by Etienne on 2026-10-06: "we can
map destinations but not routes", so the monitor draws no hop strong. Until then the second
sentence went on: "and the monitor draws strong the hops a box makes from where it may start to
where it may end".)
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, get_args

from kapps_triplestore_interface import IRI
from pydantic import BaseModel, Field, ValidationError, model_validator
from rdflib import Graph, URIRef
from rdflib import Literal as RDFLiteral
from rdflib.namespace import RDF, RDFS

from jms_kapps.uc2.routing import pure_corridor
from jms_kapps.uc2.vocabulary import (
    FC,
    FC_NS,
    FCI_NS,
    WMS,
    connection_iri,
    transit_time_literal,
)

Direction = Literal["North", "East", "South", "West"]
ModuleType = Literal["FlexConveyorModule", "EntryModule", "ExitModule"]

# The directions in the order `Direction` names them, which is the order the pages show them in.
DIRECTIONS: tuple[Direction, ...] = get_args(Direction)
MODULE_TYPES: tuple[ModuleType, ...] = get_args(ModuleType)

# One step on the grid the page draws: x grows to the East, y grows to the South.
_STEP: dict[Direction, tuple[int, int]] = {
    "North": (0, -1),
    "East": (1, 0),
    "South": (0, 1),
    "West": (-1, 0),
}

# The keys of a module list: each the IRI of a property, encoded as the OGM encodes a field name.
TYPE_KEY = IRI(str(RDF.type)).lined
LABEL_KEY = IRI(str(RDFS.label)).lined
CONNECTION_KEY = FC.hasConnection.lined
CONNECTS_TO_KEY = FC.connectsTo.lined
DIRECTION_KEY = FC.hasDirection.lined
PORT_KEY = FC.onPort.lined
TRANSIT_TIME_KEY = FC.hasTransitTime.lined

# A local name that makes a valid IRI in `fci:` without escaping, and is safe in a path.
_LOCAL_NAME = r"[A-Za-z_][A-Za-z0-9_-]*"

# The format a topology must have, for a refusal to name.
TOPOLOGY_FORMAT = (
    "A topology is a JSON list of modules, as the first iteration wrote them: each module a JSON "
    f'object with its "id", an IRI in {FCI_NS}, its types under "{TYPE_KEY}", an optional label '
    f'under "{LABEL_KEY}", an optional transit time in seconds under "{TRANSIT_TIME_KEY}", and its '
    f'connections under "{CONNECTION_KEY}", each one a JSON object with the module it leads to '
    f'under "{CONNECTS_TO_KEY}", its direction under "{DIRECTION_KEY}" and its port under '
    f'"{PORT_KEY}". Each value is a list.'
)


# The types of a module that lists none, as the first iteration seeded it.
_DEFAULT_TYPES: list[ModuleType] = ["FlexConveyorModule"]


class Connection(BaseModel):
    direction: Direction
    to: str
    port: int = 0


class Module(BaseModel):
    """One module. `types` holds every type the module has, a role or not."""

    id: str = Field(pattern=rf"^{_LOCAL_NAME}$")
    label: str | None = Field(default=None, min_length=1)
    types: list[ModuleType] = Field(
        default_factory=lambda: _DEFAULT_TYPES.copy(), min_length=1
    )
    connections: list[Connection] = []
    # The module's own transit time in seconds, or None where it takes its launch's. The loader
    # (`_transit_time`) refuses a negative one, with a reason that names the module.
    transit_seconds: float | None = None

    @property
    def resource(self) -> IRI:
        return IRI(self.id, base=FCI_NS)

    @property
    def name(self) -> str:
        """What a page calls the module: its label, or else its id, its local name."""
        return self.label or self.id


class Topology(BaseModel):
    modules: list[Module]

    @model_validator(mode="after")
    def _seedable(self) -> Topology:
        ids = [m.id for m in self.modules]
        twice = sorted({i for i in ids if ids.count(i) > 1})
        if twice:
            raise ValueError(f"a module id is used twice: {', '.join(twice)}")
        for module in self.modules:
            if module.resource == WMS:
                raise ValueError(
                    f"{module.id} is the WarehouseManagementSystem's resource, which every "
                    "launch seeds and starts, so no module can take that id"
                )
            directions = [c.direction for c in module.connections]
            for direction in dict.fromkeys(
                d for d in directions if directions.count(d) > 1
            ):
                raise ValueError(f"{module.id} has two connections to the {direction}")
            for connection in module.connections:
                if connection.to not in ids:
                    raise ValueError(
                        f"{module.id} connects to {connection.to}, which is not in the topology"
                    )
        return self

    def graph(self, transit_seconds: float) -> Graph:
        """The modules and their connections as the graph holds them: every type of each module,
        its label where it has one, its transit time, and each connection as it is listed.

        Args:
            transit_seconds: The transit time of a module that has none of its own, the launch's.
        """
        graph = Graph()
        for module in self.modules:
            subject = URIRef(module.resource)
            own = module.transit_seconds
            seconds = own if own is not None else transit_seconds
            graph.add(
                (subject, URIRef(FC.hasTransitTime), transit_time_literal(seconds))
            )
            for module_type in module.types:
                graph.add((subject, RDF.type, URIRef(FC_NS + module_type)))
            if module.label is not None:
                graph.add((subject, RDFS.label, RDFLiteral(module.label)))
            for connection in module.connections:
                node = URIRef(connection_iri(module.id, connection.direction))
                graph.add((subject, URIRef(FC.hasConnection), node))
                graph.add((node, RDF.type, URIRef(FC.Connection)))
                graph.add((node, URIRef(FC.connectsTo), URIRef(FCI_NS + connection.to)))
                graph.add(
                    (
                        node,
                        URIRef(FC.hasDirection),
                        URIRef(FC_NS + connection.direction),
                    )
                )
                graph.add((node, URIRef(FC.onPort), RDFLiteral(connection.port)))
        return graph

    def module_list(self) -> list[dict[str, Any]]:
        """This topology as a module list, the one topology format, which `parse_topology` reads
        back: every type, the label and the transit time where there are ones, and every
        connection with its port."""
        return [
            {
                "id": str(module.resource),
                TYPE_KEY: [FC_NS + module_type for module_type in module.types],
                **({LABEL_KEY: [module.label]} if module.label is not None else {}),
                **(
                    {TRANSIT_TIME_KEY: [module.transit_seconds]}
                    if module.transit_seconds is not None
                    else {}
                ),
                CONNECTION_KEY: [
                    {
                        CONNECTS_TO_KEY: [FCI_NS + connection.to],
                        DIRECTION_KEY: [FC_NS + connection.direction],
                        PORT_KEY: [connection.port],
                    }
                    for connection in module.connections
                ],
            }
            for module in self.modules
        ]

    def places(self) -> dict[str, tuple[int, int]]:
        """A cell on a grid for each module, by its id: see `layout`."""
        return layout(
            {m.id: [(c.direction, c.to) for c in m.connections] for m in self.modules}
        )


def layout[Key](
    connections: Mapping[Key, Sequence[tuple[Direction, Key]]],
) -> dict[Key, tuple[int, int]]:
    """A cell on a grid for each module, found by walking the connections' directions.

    `connections` gives each module, by any key, its connections as (direction, module) pairs.
    The walk starts at the first module, and walks each connection from either end: the module it
    leads to lies one step in its direction, and the module it leads from one step back. So a
    one-way flow with several starts, such as the grid's top row, is laid out whole. A module the
    walk does not reach, or whose cell is taken already, starts a part of its own: the walk lays
    that part out on its own, from the next module not yet placed, and puts it on the rows below
    the others, from the left edge. So a part never takes a cell of another, and a topology the
    page's editor wrote opens again in it. A connection to a key that is not a module of
    `connections` is not walked, so a graph that is only partly populated still has a layout, and
    no module has none. (Changed on 2026-10-06, jms_kapps 29, whose acceptance criterion reads "A
    topology built in the editor round-trips through JSON in today's format: download, then
    upload, gives the same grid." Until then the sentence on a module the walk does not reach
    read: "A module the walk does not reach, or whose cell is taken already, gets the next free
    cell on a row below the others.", and a part was walked from its start on that row, so that a
    module of it could lie on a cell that another part held, and was then set aside on a row of
    its own.)
    """
    # Each module's neighbours, each with the step that leads from the module to it.
    neighbours: dict[Key, list[tuple[tuple[int, int], Key]]] = {
        module: [] for module in connections
    }
    for module, links in connections.items():
        for direction, to in links:
            if to not in neighbours:
                continue
            dx, dy = _STEP[direction]
            neighbours[module].append(((dx, dy), to))
            neighbours[to].append(((-dx, -dy), module))
    places: dict[Key, tuple[int, int]] = {}
    for start in connections:
        if start in places:
            continue
        part = {start: (0, 0)}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            x, y = part[current]
            for (dx, dy), to in neighbours[current]:
                cell = (x + dx, y + dy)
                if to in places or to in part or cell in part.values():
                    continue
                part[to] = cell
                queue.append(to)
        # The part on the rows below the parts before it, from the left edge.
        left = min(x for x, _ in part.values())
        top = min(y for _, y in part.values())
        below = max((y for _, y in places.values()), default=-1) + 1
        places.update({m: (x - left, y - top + below) for m, (x, y) in part.items()})
    return places


def parse_topology(data: Any) -> Topology:
    """The topology of a module list, the one topology format: see this module's head.

    Raises:
        TopologyRefused: If `data` is no module list, naming the format a topology has, or a
            module list that cannot be seeded, naming why.
    """
    if not isinstance(data, list):
        if isinstance(data, dict) and "modules" in data:
            found = (
                'This is a JSON object with its modules under "modules", the short topology '
                "document, which use case 2 reads no longer."
            )
        else:
            found = f"This is {_json_kind(data)}, not a list of modules."
        raise TopologyRefused(f"{found} {TOPOLOGY_FORMAT}")
    return _validated(
        {"modules": [_module(entry, at) for at, entry in enumerate(data)]}
    )


def _json_kind(value: Any) -> str:
    """What kind of JSON value `value` is, for a person to read."""
    if isinstance(value, dict):
        return "a JSON object"
    if isinstance(value, list):
        return "a JSON list"
    if isinstance(value, str):
        return "a JSON string"
    if isinstance(value, bool):
        return "a JSON boolean"
    if isinstance(value, int | float):
        return "a JSON number"
    return "JSON null"


def _values(entry: dict, key: str) -> list[Any]:
    """The values under `key` in `entry`, a module or a connection of a module list: a list, as the
    first iteration wrote them. A key that is missing has none.

    Raises:
        ValueError: If the value under `key` is not a list.
    """
    values = entry.get(key, [])
    if not isinstance(values, list):
        raise ValueError(  # noqa: TRY004 - the file is wrong
            f'"{key}" holds a list of values, not {json.dumps(values)}'
        )
    return values


def _iri(value: Any) -> str | None:
    """The IRI a value names, written as a string or as `{"id": ...}`, or None if it names none."""
    if isinstance(value, dict) and set(value) == {"id"}:
        value = value["id"]
    return value if isinstance(value, str) else None


def _one(values: list[Any], key: str) -> Any:
    """The one value of `values`, the values under `key`.

    Raises:
        ValueError: If there is none, or more than one, naming `key`.
    """
    if not values:
        raise ValueError(f'it has no "{key}"')
    if len(values) > 1:
        raise ValueError(f'"{key}" holds one value, not {len(values)}')
    return values[0]


def _local(iri: Any, namespace: str) -> str | None:
    """The local name of `iri` in `namespace`, or None if it is no IRI there."""
    if isinstance(iri, str) and iri.startswith(namespace):
        return iri[len(namespace) :]
    return None


def _term(value: Any, namespace: str, names: Sequence[str], what: str) -> str:
    """The local name of `value`, an IRI in `namespace` with one of `names` as its local name.

    Raises:
        ValueError: If it is no such IRI, naming the ones it may be.
    """
    local = _local(_iri(value), namespace)
    if local is not None and local in names:
        return local
    allowed = ", ".join(namespace + name for name in names)
    raise ValueError(f"{json.dumps(value)} is no {what}: it is one of {allowed}")


def _keys(entry: dict, allowed: Sequence[str], whose: str) -> None:
    """Refuse a key of `entry` that is not one of `allowed`, naming the ones there are, so that a
    misspelt key is not silently dropped.

    Raises:
        ValueError: If `entry` has a key that is not allowed.
    """
    for key in entry:
        if key not in allowed:
            names = ", ".join(f'"{a}"' for a in allowed)
            raise ValueError(f'"{key}" is no key of {whose}, which has {names}')


def _module(entry: Any, at: int) -> dict[str, Any]:
    """The module `entry` of a module list, the `at`th from 0, as `Module` takes it.

    Raises:
        TopologyRefused: If it is no module, naming why and, once its id is read, the module.
    """
    if not isinstance(entry, dict):
        raise TopologyRefused(
            f"The module at index {at} is {_json_kind(entry)}, not a JSON object."
        )
    iri = entry.get("id")
    local = _local(iri, FCI_NS)
    if local is None or not re.fullmatch(_LOCAL_NAME, local):
        raise TopologyRefused(
            f"The module at index {at} has the id {json.dumps(iri)}: the id of a module is its "
            f'IRI in {FCI_NS}, with a local name that begins with a letter or "_" and holds only '
            f'letters, digits, "_" and "-", such as "{FCI_NS}Module1".'
        )
    try:
        _keys(
            entry,
            ("id", TYPE_KEY, LABEL_KEY, TRANSIT_TIME_KEY, CONNECTION_KEY),
            "a module",
        )
        types = [
            _term(value, FC_NS, MODULE_TYPES, "type of a module")
            for value in _values(entry, TYPE_KEY)
        ]
        labels = _values(entry, LABEL_KEY)
        label = _one(labels, LABEL_KEY) if labels else None
        if label is not None and not (isinstance(label, str) and label):
            raise ValueError(
                f"its label is a string that is not empty, not {json.dumps(label)}"
            )
        times = _values(entry, TRANSIT_TIME_KEY)
        transit = _transit_time(_one(times, TRANSIT_TIME_KEY)) if times else None
        connections = [
            _connection(link, n)
            for n, link in enumerate(_values(entry, CONNECTION_KEY))
        ]
    except ValueError as error:
        raise TopologyRefused(f"{local}: {error}") from None
    return {
        "id": local,
        "label": label,
        "transit_seconds": transit,
        "types": list(dict.fromkeys(types)) or _DEFAULT_TYPES.copy(),
        "connections": connections,
    }


def _transit_time(value: Any) -> float:
    """A module's own transit time: a number of seconds, 0 or more.

    Raises:
        ValueError: If it is no number, or is negative.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(  # noqa: TRY004 - the file is wrong
            f'its transit time ("{TRANSIT_TIME_KEY}") is a number of seconds, not '
            f"{json.dumps(value)}"
        )
    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f'its transit time ("{TRANSIT_TIME_KEY}") is 0 or more seconds, not {value}'
        )
    return float(value)


def _connection(link: Any, at: int) -> dict[str, Any]:
    """The connection `link` of a module, its `at`th from 0, as `Connection` takes it.

    Raises:
        ValueError: If it is no connection, naming why.
    """
    where = f"its connection at index {at}"
    if not isinstance(link, dict):
        raise ValueError(  # noqa: TRY004 - the file is wrong
            f"{where} is {_json_kind(link)}, not a JSON object"
        )
    try:
        # A node's own id is allowed, and not taken: the seed names the connection.
        _keys(link, ("id", CONNECTS_TO_KEY, DIRECTION_KEY, PORT_KEY), "a connection")
        target = _one(_values(link, CONNECTS_TO_KEY), CONNECTS_TO_KEY)
        to = _iri(target)
        if to is None:
            raise ValueError(
                f'"{CONNECTS_TO_KEY}" names a module by its IRI, not {json.dumps(target)}'
            )
        direction = _term(
            _one(_values(link, DIRECTION_KEY), DIRECTION_KEY),
            FC_NS,
            DIRECTIONS,
            "direction",
        )
        ports = _values(link, PORT_KEY)
        port = _one(ports, PORT_KEY) if ports else 0
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError(  # noqa: TRY004 - the file is wrong
                f"its port is a whole number, not {json.dumps(port)}"
            )
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None
    # A module of another namespace is named whole, so that it is not taken for one in `fci:`.
    return {"direction": direction, "to": _local(to, FCI_NS) or to, "port": port}


def _validated(data: Any) -> Topology:
    """The modules `data` as a `Topology`.

    Raises:
        TopologyRefused: If they are no topology that can be seeded, naming why in words a
            person reads, without the parser's report around them.
    """
    try:
        return Topology.model_validate(data)
    except ValidationError as error:
        raise TopologyRefused(
            "; ".join(
                str(wrong["ctx"]["error"])
                if wrong["type"] == "value_error"
                else wrong["msg"]
                for wrong in error.errors()
            )
        ) from None


@dataclass(frozen=True)
class Choice[Key]:
    """The modules a box may start on, or end on, and what a reason calls one of them: `name` is
    "entry module" or "exit module" where the topology names that role, and "module" where it
    names none, so that every module may take that end."""

    modules: list[Key]
    name: str

    @property
    def with_article(self) -> str:
        """`name` as a reason says it: "an entry module", "a module"."""
        return f"{'an' if self.name[0] in 'aeiou' else 'a'} {self.name}"


def choices[Key](
    types: Mapping[Key, Collection[ModuleType]],
) -> tuple[Choice[Key], Choice[Key]]:
    """Where a box may start, its origins, and where it may end, its destinations, on the modules
    of `types`, which gives each module its types, in their order.

    **Roles are optional.** A box starts on an entry module where the modules name any, and on any
    module where they name none; it ends on an exit module where they name any, and on any module
    where they name none. A module of both roles is both. (Added on 2026-10-06, jms_kapps 45,
    decided by Etienne: "in general entry and exit module are roles. a module can be both", and
    roles are optional, as the first iteration's WMS had them.)
    """
    everyone = list(types)
    entries = [m for m, its_types in types.items() if "EntryModule" in its_types]
    exits = [m for m, its_types in types.items() if "ExitModule" in its_types]
    return (
        Choice(entries, "entry module") if entries else Choice(everyone, "module"),
        Choice(exits, "exit module") if exits else Choice(everyone, "module"),
    )


def entries_and_exits[Key](
    types: Mapping[Key, Collection[ModuleType]],
) -> tuple[list[Key], list[Key]]:
    """The entry modules and the exit modules of the modules of `types`, which gives each module
    its types, in their order: the modules the WarehouseManagementSystem's form offers. A module
    of both roles is both.
    Modules that name no entry module count every module as one, and modules that name no exit
    module count every module as an exit, as the WarehouseManagementSystem does (`choices`).
    (Changed on 2026-10-06, jms_kapps 43, decided by Etienne: the load check no longer looks at
    these hops. Until then "whose hops the load check looks at" and "so that the check errs on the
    safe side". Changed again on 2026-10-06, jms_kapps 45, decided by Etienne: roles are optional
    for the WarehouseManagementSystem too. Until then the first sentence ended at "strong", and the
    last ended at "exit". Changed on 2026-10-10, jms_kapps 24, decided by Etienne on 2026-10-06:
    "we can map destinations but not routes", so the monitor draws no hop strong. Until then the
    first sentence went on: "where the boxes start and end whose hops the monitor draws strong,
    and the modules the WarehouseManagementSystem's form offers".)"""
    origins, destinations = choices(types)
    return origins.modules, destinations.modules


def _runnable(topology: Topology) -> Topology:
    """`topology`, if it holds a module and no pure two-way corridor (`routing.pure_corridor`).

    Raises:
        TopologyRefused: If it holds no module, or if a module's only two connections both run
            both ways, naming it and its two neighbours. (The empty list was added on 2026-10-10,
            jms_kapps 69, decided by Etienne. The corridor was changed on 2026-10-06, jms_kapps
            43, decided by Etienne. Until then: "If the hops a box can make from an entry module
            to an exit module run in a cycle, naming its modules.")
    """
    if not topology.modules:
        raise TopologyRefused("a topology needs at least one module")
    corridor = pure_corridor(
        (m.resource, IRI(c.to, base=FCI_NS))
        for m in topology.modules
        for c in m.connections
    )
    if corridor is not None:
        ids = {m.resource: m.id for m in topology.modules}
        module, one, other = (ids[m] for m in corridor)
        raise TopologyRefused(
            f"{module} is a pure two-way corridor: its only two connections, to {one} and to "
            f"{other}, both run both ways. Two boxes that meet there head-on, one each way, "
            "could wait for each other forever, with no neighbour to step aside to. Let one of "
            f"those connections run one way, or give {module} a third connection."
        )
    return topology


class TopologyRefused(ValueError):
    """Loading refuses a topology: what is read is no module list, or one that cannot be seeded,
    or the load check refuses it. The message is the reason, for a person to read: the Launcher
    page shows it, and `jms-uc2 --topology` prints it. Every refusal of this module is one, so
    that a caller can tell a refusal from any other error, as a bug's. (Added on 2026-10-11,
    jms_kapps 33.)"""


class UnknownTopology(TopologyRefused):
    """The package ships no topology of the name asked for. The message names it and the
    topologies the package ships, for a person to read. `shipped_topology` is the one check of a
    shipped name, and every caller of it refuses with this. (Added on 2026-10-11, jms_kapps 33.)"""


def _shipped() -> dict[str, str]:
    """The topologies below `topologies/`, by stem, as JSON text."""
    folder = resources.files("jms_kapps.uc2").joinpath("topologies")
    return {
        entry.name.removesuffix(".json"): entry.read_text(encoding="utf-8")
        for entry in folder.iterdir()
        if entry.name.endswith(".json")
    }


def topology_names() -> list[str]:
    """The names of the shipped topologies: what `shipped_topology` accepts. For a list to offer;
    whether a name is one of them is `shipped_topology`'s check."""
    return sorted(_shipped())


def shipped_topology(name: str) -> Topology:
    """The topology this package ships as `name`. Nothing outside the package is read.

    This is the one check of a shipped name: a caller asks for the topology by its name, and
    checks the name no more itself. (Added on 2026-10-11, jms_kapps 33: until then the Launcher's
    routes and `load_topology` each checked the name first, in words of their own.)

    Raises:
        UnknownTopology: If the package ships no topology of that name, naming the ones it ships.
        TopologyRefused: If the topology it ships as `name` cannot be seeded, or boxes can wait
            for each other forever on it, naming why.
    """
    shipped = _shipped()
    if name not in shipped:
        raise UnknownTopology(
            f"There is no shipped topology {name!r}. Shipped: {', '.join(sorted(shipped))}."
        )
    return _runnable(parse_topology(json.loads(shipped[name])))


def parse_topology_file(content: bytes) -> Topology:
    """The topology of a topology file whose bytes are `content`: JSON in UTF-8, a module list
    (`parse_topology`). Runs no load check: the Launcher page's editor opens a file with it, and
    `topology_file` reads a file with it before the check.

    Raises:
        TopologyRefused: If the file is no JSON, naming why and the format a topology has, or
            holds no module list that can be seeded, naming why.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TopologyRefused(
            f"This file is no text in UTF-8, so no JSON: {error}. {TOPOLOGY_FORMAT}"
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TopologyRefused(
            f"This file is no JSON: {error}. {TOPOLOGY_FORMAT}"
        ) from None
    return parse_topology(data)


def topology_file(path: Path) -> Topology:
    """The topology in the file at `path`. Nothing the package ships is read.

    Raises:
        TopologyRefused: If the file holds no topology that can be seeded, or one on which boxes
            can wait for each other forever, naming why.
    """
    return _runnable(parse_topology_file(path.read_bytes()))


def edited_topology(module_list: Any) -> Topology:
    """The topology of `module_list`, which a person edited, checked as loading checks a shipped
    topology or a file. (Changed on 2026-10-06, jms_kapps 44, decided by Etienne. Until then this
    read: "It takes the format of the shipped topologies only, not the paper's artifact's module
    list, which a file may hold.")

    Raises:
        TopologyRefused: If it is no module list, naming the format a topology has, or one that
            cannot be seeded, or one on which boxes can wait for each other forever, naming why.
    """
    return _runnable(parse_topology(module_list))


def load_topology(name_or_path: str) -> Topology:
    """The shipped topology called `name_or_path`, or else the one in the file at that path.

    A shipped name always means the shipped topology, whatever the working directory holds, so a
    file of that name cannot take its place. Such a file is reached by a path that is not a bare
    name, such as `./cross`. Anything that is not a shipped name is a path, and only that file is
    read.

    Raises:
        UnknownTopology: If it is neither a shipped name nor a file, in the words of the check of
            a shipped name, which name the shipped topologies, and saying that there is no such
            file either. (Changed on 2026-10-11, jms_kapps 33: until then in words of its own,
            "There is no topology <name>, and no such file. Shipped: <names>.")
        TopologyRefused: If its topology cannot be seeded, naming why.
    """
    path = Path(name_or_path)
    try:
        return shipped_topology(name_or_path)
    except UnknownTopology as unknown:
        if not path.is_file():
            raise UnknownTopology(
                f"{unknown} Nor is there a file at {name_or_path!r}."
            ) from None
    return topology_file(path)
