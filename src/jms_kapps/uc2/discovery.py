"""What the parties read from the graph: their peers' workflows, the connections, what each holds,
which modules a box enters and leaves by, and which resource is the WarehouseManagementSystem.

Every answer comes from the graph at the moment it is asked. A module routes by no table of its
neighbours, because a table filled once at start-up is a race against the neighbour's own start.
The connections it read last stay in its memory for its dashboard alone.

A resource may have several services, one for each middleware instance bound to it, and a
process killed before it deregisters leaves its service behind with its address. So every read
that finds a service takes only a live one, through the one reader of a service
(`common.services`): a service with an address and a heartbeat younger than
`MAX_HEARTBEAT_AGE_SECONDS`, judged against this reader's clock. When several are live, the read
takes the one whose heartbeat is newest: of a killed instance and its restart, the restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple, Protocol

from kapps_ogm import Node
from kapps_ogm.utils.class_scope import ClassScope
from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import services
from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.topology import DIRECTIONS, MODULE_TYPES, Direction, ModuleType
from jms_kapps.uc2.transport import BOX_SCOPE, Transport, transport_of_box
from jms_kapps.uc2.vocabulary import FC, FC_NS


def workflow_endpoint(db: Queryable, resource: IRI, workflow_class: IRI) -> str | None:
    """The endpoint of the workflow of class `workflow_class` on `resource`'s live service. A
    caller calls it directly, so it needs no capability's link to the workflow."""
    for service in services(
        db, resource=resource, workflows=(workflow_class,), capability_linked=False
    ):
        if workflow_class in service.workflows:
            return service.workflows[workflow_class]
    return None


def service_address(db: Queryable, resource: IRI) -> str | None:
    """The base address of `resource`'s live service."""
    found = services(db, resource=resource)
    return found[0].address if found else None


def handover_event_stream(db: Queryable, resource: IRI) -> str | None:
    """Where `resource` serves its handover event stream, as it committed it at its start."""
    rows = bindings(
        db,
        f"SELECT ?stream WHERE {{ <{resource}> <{FC.hasHandoverEventStream}> ?stream }} "
        "LIMIT 1",
    )
    return str(rows[0]["stream"]) if rows else None


def service_addresses(db: Queryable) -> dict[IRI, str]:
    """The base address of every live service, by the resource it serves."""
    addresses: dict[IRI, str] = {}
    for service in services(db):
        # The services come newest heartbeat first, so the first one of a resource is its choice.
        if service.address is not None:
            addresses.setdefault(service.resource, service.address)
    return addresses


def wms(db: Queryable) -> IRI | None:
    """The WarehouseManagementSystem: the resource of its class, which takes every box back.

    It is found by its class, as a module finds a peer by its workflows, so no module names it.
    """
    rows = bindings(
        db,
        f"""
        SELECT ?resource WHERE {{ ?resource a <{FC.WarehouseManagementSystem}> }}
        ORDER BY ?resource LIMIT 1
        """,
    )
    return IRI(str(rows[0]["resource"])) if rows else None


# The three module classes, by their IRIs.
_MODULE_CLASSES: dict[IRI, ModuleType] = {
    IRI(module_type, base=FC_NS): module_type for module_type in MODULE_TYPES
}


class ModulesAndConnections(NamedTuple):
    """Every module of the station with its types, in the order of their IRIs, and every
    connection, as (the module that has it, the module it leads to), with the direction of each
    that names one the use case knows: what the WarehouseManagementSystem picks the modules of a
    spawn from. The WMS's strategy is given the connections as the WMS read them, each with its
    direction, as a module's is."""

    types: dict[IRI, list[ModuleType]]
    connections: list[tuple[IRI, IRI]]
    directions: Mapping[tuple[IRI, IRI], Direction] = MappingProxyType({})

    def directed(self) -> list[DirectedConnection]:
        """Every connection, with its direction, or None where the read named none."""
        return [
            DirectedConnection(source, target, self.directions.get((source, target)))
            for source, target in self.connections
        ]


def modules_and_connections(db: Queryable) -> ModulesAndConnections:
    """Every module of the station with its types, and every connection, in one read: each
    resource the graph types as one of the three module classes, with those of its classes, and
    where each connection leads. A module's roles are among its types, `fc:EntryModule` and
    `fc:ExitModule`, where the topology names them (`topology.choices`); the connections say which
    destinations an origin reaches (`routing.reachable`)."""
    classes = " ".join(f"<{module_class}>" for module_class in _MODULE_CLASSES)
    rows = bindings(
        db,
        f"""
        SELECT DISTINCT ?module ?class ?source ?target ?direction WHERE {{
            {{ VALUES ?class {{ {classes} }} ?module a ?class . }}
            UNION
            {{ {_CONNECTIONS_PATTERN} }}
        }} ORDER BY ?module ?class
        """,
    )
    types: dict[IRI, list[ModuleType]] = {}
    found: list[tuple[IRI, IRI]] = []
    directions: dict[tuple[IRI, IRI], Direction] = {}
    for row in rows:
        if "module" in row:
            types.setdefault(IRI(str(row["module"])), []).append(
                _MODULE_CLASSES[IRI(str(row["class"]))]
            )
            continue
        connection = _connection(row)
        found.append((connection.source, connection.target))
        if connection.direction is not None:
            directions[(connection.source, connection.target)] = connection.direction
    return ModulesAndConnections(types, found, directions)


class TransitTimes(NamedTuple):
    """What a module reads of transit times when it starts: its own, and the longest of any
    module in the graph, T of the bounds on a wait for a box (`module.ARRIVAL_BOUND_SECONDS`)."""

    own: float
    longest: float


class TransitTimeError(ValueError):
    """The graph does not hold exactly one transit time for a module."""


def transit_times(db: Queryable, resource: IRI) -> TransitTimes:
    """The transit time of `resource` and the longest one of any module, in one read.

    Raises:
        TransitTimeError: If `resource` has no `fc:hasTransitTime`, or more than one, naming the
            module.
    """
    rows = bindings(
        db, f"SELECT ?module ?time WHERE {{ ?module <{FC.hasTransitTime}> ?time }}"
    )
    times = [(IRI(str(row["module"])), float(row["time"])) for row in rows]
    own = [seconds for module, seconds in times if module == resource]
    if len(own) != 1:
        held = (
            "no transit time"
            if not own
            else f"{len(own)} transit times ({', '.join(f'{s:g}' for s in sorted(own))} s)"
        )
        raise TransitTimeError(
            f"{resource.fragment} has {held} in the knowledge graph (fc:hasTransitTime), and needs exactly "
            "one, which a launch writes, so it does not start"
        )
    return TransitTimes(own[0], max(seconds for _, seconds in times))


def capability(db: Queryable, resource: IRI, capability_class: IRI) -> IRI | None:
    """The Capability of class `capability_class` that `resource` provides, or None.

    The middleware writes it when the resource registers the workflow that realizes it, and
    keeps it when the resource stops.
    """
    rows = bindings(
        db,
        f"""
        SELECT ?capability WHERE {{
            <{resource}> <{CFC.hasCapability}> ?capability .
            ?capability a <{capability_class}> .
        }} ORDER BY ?capability LIMIT 1
        """,
    )
    return IRI(str(rows[0]["capability"])) if rows else None


class Fetch(Protocol):
    """The middleware's `mw.fetch(instance_iri, class_scope=...)`: one instance read, under a
    class scope, into a node."""

    def __call__(
        self, instance_iri: str, *, class_scope: ClassScope | None = None
    ) -> Node: ...


class DirectedConnection(NamedTuple):
    """One connection of the station: the module that has it, the module it leads to, and the
    side of `source` it leaves by, or None if the graph names no direction the use case knows."""

    source: IRI
    target: IRI
    direction: Direction | None


# The four directions of the use case, by their IRIs.
_DIRECTIONS: dict[IRI, Direction] = {IRI(d, base=FC_NS): d for d in DIRECTIONS}


def _direction(row: dict) -> Direction | None:
    """The direction a row binds as `?direction`, if it is one of the four."""
    return _DIRECTIONS.get(IRI(str(row["direction"]))) if "direction" in row else None


# Every connection of the station, as `?source ?target ?direction`.
_CONNECTIONS_PATTERN = f"""
    ?source <{FC.hasConnection}> ?connection .
    ?connection <{FC.connectsTo}> ?target .
    OPTIONAL {{ ?connection <{FC.hasDirection}> ?direction }}
"""


def _possession_pattern(resource: IRI) -> str:
    """Each box `resource` possesses, as `?box`, from Core's reified possession."""
    return f"""
    ?box <{CFC.hasPossessedWorkpiece}> ?possession .
    <{resource}> <{CFC.hasPossessor}> ?possession .
"""


def _connection(row: dict) -> DirectedConnection:
    return DirectedConnection(
        IRI(str(row["source"])), IRI(str(row["target"])), _direction(row)
    )


def connections(db: Queryable) -> list[DirectedConnection]:
    """Every connection of the station, with its direction, in one read: what a module finds the
    next module of a box on, and what its dashboard draws."""
    rows = bindings(
        db,
        f"SELECT DISTINCT ?source ?target ?direction WHERE {{ {_CONNECTIONS_PATTERN} }}",
    )
    return [_connection(row) for row in rows]


def boxes_held_by(db: Queryable, resource: IRI) -> list[IRI]:
    """The boxes `resource` possesses now, read from Core's reified possession."""
    rows = bindings(
        db, f"SELECT DISTINCT ?box WHERE {{ {_possession_pattern(resource)} }}"
    )
    return [IRI(str(row["box"])) for row in rows]


def holds_query(resource: IRI, box: IRI) -> str:
    """The `ASK` whether `resource` possesses `box` now, from Core's reified possession."""
    return (
        f"ASK {{ <{box}> <{CFC.hasPossessedWorkpiece}> ?possession . "
        f"<{resource}> <{CFC.hasPossessor}> ?possession . }}"
    )


def holds(db: Queryable, resource: IRI, box: IRI) -> bool:
    """Whether `resource` possesses `box` now, in one `ASK` about that one box: its answer is the
    same size however many boxes `resource` holds.
    """
    answer = db.query(holds_query(resource, box))
    return bool(answer.get("boolean", False)) if isinstance(answer, dict) else False


class StartRead(NamedTuple):
    """What a module reads when it starts, for its dashboard: the station's connections, and
    the boxes it possesses, so that a restarted module shows the box it holds."""

    connections: list[DirectedConnection]
    held: list[IRI]


def at_start(db: Queryable, resource: IRI) -> StartRead:
    """The connections of the station and the boxes `resource` possesses, in one read."""
    rows = bindings(
        db,
        f"""
        SELECT DISTINCT ?source ?target ?direction ?box WHERE {{
            {{ {_CONNECTIONS_PATTERN} }} UNION {{ {_possession_pattern(resource)} }}
        }}
        """,
    )
    return StartRead(
        connections=[_connection(row) for row in rows if "source" in row],
        held=[IRI(str(row["box"])) for row in rows if "box" in row],
    )


class GraphView:
    """The reads above, bound to one client, and the fetch of a box with its transport, through
    the party's middleware. What a running module and the WarehouseManagementSystem are given as
    their graph. None of them reads every box in the graph.

    Neither reads a transport it is notified of: the middleware's intake hands it that one. A
    module reads a transport when a box arrives on it, from its Receive, and when the bound of its
    reservation finds the box its own with no Receive: then it reads the box with its transport
    through the box's link to it, with `fetch`, its middleware's `mw.fetch`. A party finds a
    transport through its box, never from the box's name.
    """

    def __init__(self, db: Queryable, fetch: Fetch) -> None:
        self.db = db
        self._fetch = fetch

    def transport_of(self, box: IRI) -> Transport | None:
        """The transport of `box`, read with the box in one fetch through `fc:hasTransport`, or
        None if the box links to none.

        Raises:
            ValueError: If the store holds no class for the box: it is not in the graph.
        """
        return transport_of_box(self._fetch(str(box), class_scope=BOX_SCOPE))

    def boxes_held_by(self, resource: IRI) -> list[IRI]:
        return boxes_held_by(self.db, resource)

    def holds(self, resource: IRI, box: IRI) -> bool:
        return holds(self.db, resource, box)

    def connections(self) -> list[DirectedConnection]:
        return connections(self.db)

    def at_start(self, resource: IRI) -> StartRead:
        return at_start(self.db, resource)

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None:
        return workflow_endpoint(self.db, resource, workflow_class)

    def wms(self) -> IRI | None:
        return wms(self.db)

    def modules_and_connections(self) -> ModulesAndConnections:
        return modules_and_connections(self.db)

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None:
        return capability(self.db, resource, capability_class)
