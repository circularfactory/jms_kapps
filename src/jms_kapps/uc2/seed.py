"""What use case 2 finds in the graph before any module starts.

Every module can give and take a box actively (`mes:Pass` and `mes:Retrieve`, the both-active
pair of the middleware's handover), and so can the WarehouseManagementSystem, which a launch
seeds beside every topology: it creates each box on its origin and takes it back, so a box has a
possessor all its journey, as Core's exactly-one possession requires. It hands no box onto the
station with the middleware's handover, so its `mes:Pass` goes unused. The
two-module seed has module 1, connected to module 2, and the WarehouseManagementSystem as an
individual only, with no handover ability: no box goes back to it, and a destination module keeps
each box.
"""

from __future__ import annotations

from collections.abc import Mapping

from kapps_ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_semantic_middleware.possession import mint_possession_state_iri
from kapps_semantic_middleware.vocabulary import CFC, MES, SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

from jms_kapps.common.prefixes import PREFIXES
from jms_kapps.common.triplestore import bindings, read_asset, reset
from jms_kapps.uc2.history import enable_history
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import (
    FC,
    WMS,
    connection_iri,
    module_iri,
    transit_time_literal,
    transport_iri,
)

MODULES = (module_iri(1), module_iri(2))

# The transit time of a launch's modules unless `--transit-time` or the page's field sets it: a few seconds, so that a person
# watching the demo sees each box on each module. The tests give 0. Not 5 s: a party reads the
# store right after a move, and a store that closes a connection after 5 s idle can close it just
# as that read is sent. The store's client does not send such a read again, so the read fails. The
# ticket "A query on a connection that the store closed when idle fails instead of being retried
# (14)" in kapps_triplestore_interface fixes the client; after its release the default can go
# back to 5 s.
DEFAULT_TRANSIT_SECONDS = 3.0

Triple = tuple[IRI, IRI, IRI]


def shapes_document(*, sparql_shapes: bool) -> str:
    """The shapes that the store receives as one document: the Core file, followed by the
    SHACL-SPARQL file when `sparql_shapes`.

    The store's `shapes_set` replaces every shape, so two files must go as one document. `reset`
    adds the middleware's operation-status shape in front of it.
    """
    document = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_shapes.ttl")
    if sparql_shapes:
        document += "\n" + read_asset(
            "jms_kapps.uc2", "shapes", "flexconveyor_usecase_sparql_shapes.ttl"
        )
    return document


def reset_uc2(
    db: TripleStore,
    *,
    individuals: str = "",
    collect_history: bool = False,
    with_shapes: bool = True,
    sparql_shapes: bool = False,
) -> None:
    """Empty the dataset and load use case 2's ontology and shapes, then `individuals`.

    The shapes are set through the store's `shapes_set`, so a dataset without SHACL validation
    refuses the reset, before it loads anything: shapes stored there would validate nothing.
    Only with `with_shapes` False does the reset send no shapes, and seed such a dataset without
    them: the measurement harness sets it for its SHACL-off twin, and nothing else does. The
    SHACL-SPARQL rule is loaded only with `sparql_shapes`, which the shape tests, the audit and the
    SHACL-SPARQL demo ask for and a launch does not.

    With `collect_history`, the launch option, the reset then turns on the store's history
    collection, with no filter, after it loaded the ontology: its last step
    (`history.enable_history`). Without it, the reset sends nothing to the history, neither on nor
    off, so a dataset whose collection an earlier launch turned on keeps collecting. Nothing
    else in the reset changes: with the collection on, the store refuses only a clear of the whole
    dataset, and the reset clears the default graph and each graph it loads by name. It never
    turns the collection off around a clear, because a clear is then not logged, and the cleared
    data disappears from every earlier point in time.
    """
    reset(
        db,
        ontology=read_asset("jms_kapps.uc2", "ontology", "flexconveyor_usecase.ttl"),
        shapes=shapes_document(sparql_shapes=sparql_shapes) if with_shapes else None,
        individuals=individuals,
    )
    if collect_history:
        enable_history(db)


def seed_topology(
    db: TripleStore,
    topology: Topology,
    *,
    transit_seconds: float,
    resources: Mapping[IRI, str] | None = None,
    collect_history: bool = False,
    with_shapes: bool = True,
    sparql_shapes: bool = False,
) -> None:
    """Empty the dataset, then seed every module of `topology`, the WarehouseManagementSystem,
    and each of `resources`. With `collect_history`, the launch option, the reset then turns on the
    store's history collection (`reset_uc2`). With
    `with_shapes` False, the reset sends no shapes, for a dataset without SHACL validation; by
    default a dataset without it refuses the seed (`reset_uc2`). The SHACL-SPARQL rule is loaded
    only with `sparql_shapes`, which the shape tests, the audit and the SHACL-SPARQL demo ask for.

    Every module gets exactly one `fc:hasTransitTime`: its own from the topology, else
    `transit_seconds`, the launch's. Each of `resources` is a plain `cfc:Resource` with the label
    it maps to: a participant that the caller starts beside every run, as the demo Launcher starts
    the monitor. The run core starts no monitor, so the seed names none.

    One import, so the triple store checks the modules and their connections together: a
    connection's shape needs the module it leads to. It is the launch's last import, and the reset
    registers the dataset's prefixes after it. Its Turtle declares those prefixes, because
    the store keeps a prefix an import declares: left to itself, rdflib would declare names of its
    own invention, such as `ns1:`, for the registration to delete again.
    """
    graph = topology.graph(transit_seconds)
    for prefix, namespace in PREFIXES.items():
        graph.bind(prefix, namespace, replace=True)
    graph.add((URIRef(WMS), RDF.type, URIRef(FC.WarehouseManagementSystem)))
    graph.add((URIRef(WMS), RDFS.label, Literal("Warehouse Management System")))
    for resource in (*(m.resource for m in topology.modules), WMS):
        for ability in (MES.Pass, MES.Retrieve):
            graph.add(
                (
                    URIRef(resource),
                    URIRef(MES.hasHandoverAbility),
                    URIRef(ability),
                )
            )
    for resource, label in (resources or {}).items():
        graph.add((URIRef(resource), RDF.type, URIRef(CFC.Resource)))
        graph.add((URIRef(resource), RDFS.label, Literal(label)))
    reset_uc2(
        db,
        individuals=graph.serialize(format="turtle"),
        collect_history=collect_history,
        with_shapes=with_shapes,
        sparql_shapes=sparql_shapes,
    )


def seed_two_modules(
    db: TripleStore, *, transit_seconds: float, sparql_shapes: bool = False
) -> tuple[IRI, IRI]:
    """Seed module 1, connected east to module 2, and the WarehouseManagementSystem, with both
    modules at `transit_seconds`. The SHACL-SPARQL rule is loaded only with `sparql_shapes`, which
    the shape tests ask for.

    Returns:
        The two module IRIs, upstream first.
    """
    reset_uc2(db, sparql_shapes=sparql_shapes)
    upstream, downstream = MODULES
    for n, module in enumerate(MODULES, start=1):
        _add_module(db, module, n, transit_seconds)
    db.triple_add((WMS, RDF.type, FC.WarehouseManagementSystem))
    db.triple_add((WMS, RDFS.label, Literal("Warehouse Management System")))

    # One write, because the store refuses a connection that lacks any of the three
    # (`:ConnectionShape`).
    connection = connection_iri("Module1", "East")
    _create(
        OGM(db=db),
        FC.Connection,
        connection,
        {
            str(FC.connectsTo): [{"id": str(downstream)}],
            str(FC.hasDirection): [{"id": str(FC.East)}],
            str(FC.onPort): [1],
        },
    )
    db.triple_add((upstream, FC.hasConnection, connection))
    return upstream, downstream


def seed_three_modules(
    db: TripleStore, *, transit_seconds: float, sparql_shapes: bool = False
) -> tuple[IRI, IRI, IRI]:
    """Seed the two modules of `seed_two_modules`, and a third, which no connection reaches or
    leaves, at `transit_seconds` like the other two. The SHACL-SPARQL rule is loaded only with
    `sparql_shapes`, which the SHACL-SPARQL demonstration asks for.

    Returns:
        The three module IRIs: module 1, connected to module 2, and module 3 apart.
    """
    upstream, downstream = seed_two_modules(
        db, transit_seconds=transit_seconds, sparql_shapes=sparql_shapes
    )
    apart = module_iri(3)
    _add_module(db, apart, 3, transit_seconds)
    return upstream, downstream, apart


def _add_module(db: TripleStore, module: IRI, n: int, transit_seconds: float) -> None:
    """Add module `n`: its type, a label, its transit time and both handover abilities."""
    db.triple_add((module, RDF.type, FC.FlexConveyorModule))
    # A name for whatever shows the module later, as the middleware's demo seeds its units.
    db.triple_add((module, RDFS.label, Literal(f"Module {n}")))
    db.triple_add((module, FC.hasTransitTime, transit_time_literal(transit_seconds)))
    db.triple_add((module, MES.hasHandoverAbility, MES.Pass))
    db.triple_add((module, MES.hasHandoverAbility, MES.Retrieve))


def _create(ogm: OGM, class_iri: IRI, instance: IRI, data: dict) -> None:
    """Create `instance` with all of `data` in one validated write."""
    ogm.create(
        class_iri=class_iri,
        instance_iri=instance,
        data=data,
        class_scope=ClassScope.from_property_chains([[IRI(p)] for p in data]),
        persist=True,
    )


def possession_triples(db: TripleStore, box: IRI, possessor: IRI) -> list[Triple]:
    """The triples that make `possessor` the possessor of `box`, for the write that needs them.

    The box points at the possessor's one possession state, as the middleware's handover points
    it. The state is found by a query, as the middleware finds it, never from its IRI. A possessor
    that has a state keeps it, and its link is not written again. A possessor without one gets
    `<possessor>_possession`, the IRI the middleware gives a state it creates, typed
    `cfc:PossessionState` and linked from the possessor, so the state comes into being with its
    first box.

    Raises:
        ValueError: If `possessor` has more than one possession state, as the middleware raises:
            a resource has one.
    """
    rows = bindings(
        db,
        f"SELECT DISTINCT ?state WHERE {{ <{possessor}> <{CFC.hasPossessor}> ?state }}",
    )
    states = sorted(str(row["state"]) for row in rows)
    if len(states) > 1:
        raise ValueError(
            f"{possessor} has {len(states)} possession states ({', '.join(states)}); "
            "a Resource has one"
        )
    if states:
        return [(box, CFC.hasPossessedWorkpiece, IRI(states[0]))]
    state = mint_possession_state_iri(possessor)
    return [
        (possessor, CFC.hasPossessor, state),
        (state, IRI(RDF.type), CFC.PossessionState),
        (box, CFC.hasPossessedWorkpiece, state),
    ]


def place_box(
    ogm: OGM,
    box: IRI,
    *,
    on: IRI,
    origin: IRI,
    destination: IRI,
    transport: IRI | None = None,
) -> IRI:
    """Create `box` in the possession of module `on`, with its transport running from `origin` to
    `destination` and assigned to `on`, and return the transport.

    The seed chooses the transport's IRI: `transport` if given, and otherwise the one the
    WarehouseManagementSystem's opening gives a transport. The box links to its transport
    (`fc:hasTransport`) and the transport to its box (`fc:hasBox`), both written, as the opening
    writes them. The box points at the module's one possession state (see
    `possession_triples`). The transport names no capability: a module's BoxTransportCapability
    comes into the graph when the module registers its BoxTransportWorkflow, after the seed.

    The box, its possession and its transport arrive in one write, because each needs the others:
    a box on a module must have a transport, and the box of a running transport must be on exactly
    one module. A seed is demonstration setup, made before any participant starts, so it writes
    directly and not through a context manager of the middleware.

    Raises:
        ValueError: If the box or its transport is in the graph already, or the module has more
            than one possession state.
    """
    transport = transport if transport is not None else transport_iri(box)
    placed = ogm.db.triples_add(
        [
            *possession_triples(ogm.db, box, on),
            (box, RDF.type, FC.Box),
            (box, FC.hasTransport, transport),
            (transport, RDF.type, FC.BoxTransportOperation),
            (transport, FC.hasBox, box),
            (transport, FC.hasOrigin, origin),
            (transport, FC.hasDestination, destination),
            (transport, SVC.hasOperationStatus, Literal(OperationStatus.RUNNING)),
            (transport, CFC.executedOn, on),
        ]
    )
    if not placed:
        raise ValueError(f"{box.fragment} or its transport is in the graph already")
    return transport
