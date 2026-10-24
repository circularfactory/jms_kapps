"""Topologies seeded into a live store and read back from it, and the first iteration's cross run.

What the store holds is read as written, apart from what its reasoner infers (`_explicit`), and
compared with sources that are not the code under test (`_topologies`): what each shipped
topology seeded before it became a module list, and the first iteration's own file.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import logging

import pytest
from _explicit import explicit_objects
from _participants import participants
from _topologies import FIRST_ITERATIONS_CROSS, SEEDED_BEFORE, as_seeded, module_entry
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, MES, OperationStatus
from kapps_triplestore_interface import IRI, SPARQLQuery
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from jms_kapps.common.triplestore import Queryable, bindings, connect
from jms_kapps.uc2.monitor.reads import GraphReads
from jms_kapps.uc2.monitor.wires import wires
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.topology import load_topology, parse_topology, shipped_topology
from jms_kapps.uc2.transport import operation_status
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS, WMS, module_iri
from jms_kapps.uc2.wms import taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The handover abilities the seed gives every module, as it gave them before: the first
# iteration's files carry none.
ABILITIES = {URIRef(MES.Pass), URIRef(MES.Retrieve)}

JOURNEY_SECONDS = 120.0


def explicit_individuals(db: Queryable) -> Graph:
    """Every statement written about an individual in `fci:`, without what the reasoner infers."""
    graph = Graph()
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?s", "?p", "?o"],
            where_clauses=[f'?s ?p ?o . FILTER(STRSTARTS(STR(?s), "{FCI_NS}"))'],
            include_implicit=False,
        ),
        convert=False,
    )
    for row in rows:
        o = row["o"]
        term = (
            URIRef(o["value"])
            if o["type"] == "uri"
            else Literal(o["value"], datatype=o.get("datatype"), lang=o.get("xml:lang"))
        )
        graph.add((URIRef(row["s"]["value"]), URIRef(row["p"]["value"]), term))
    return graph


@pytest.fixture
def records(caplog):
    """Every record the parties log, as its message, in the order they logged them."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    def records() -> list[str]:
        return [
            r.getMessage() for r in caplog.records if r.name.startswith("jms_kapps")
        ]

    return records


@pytest.mark.parametrize("name", sorted(SEEDED_BEFORE))
def test_each_shipped_topology_converted_seeds_the_modules_it_seeded_before(
    dataset, name
):
    """The same types, labels and connections, read back from the store as written, and the
    handover abilities the seed gives every module. The shipped cross seeds Modules 1 and 2 as entry modules, Modules 4 and 5 as exit modules and
    Module 3 as a FlexConveyorModule, as `shipped_as_seeded_before.json` holds it."""
    db = connect(dataset)

    seed_topology(db, shipped_topology(name), transit_seconds=0)

    written = explicit_individuals(db)
    assert as_seeded(written) == SEEDED_BEFORE[name]
    for module in SEEDED_BEFORE[name]:
        resource = URIRef(FCI_NS + module["id"])
        assert (
            set(written.objects(resource, URIRef(MES.hasHandoverAbility))) == ABILITIES
        )


@pytest.mark.paper_claim
def test_every_module_of_a_seeded_topology_is_a_core_resource_by_the_ontology(
    dataset,
):
    """Paper claim 1 (§5.2): "Modules are typed as :FlexConveyorModule, a subclass of
    cfc:Resource". The store holds the subclass axiom as use case 2's ontology writes it, and it
    answers every module of the seeded cross as a `cfc:Resource`, although the seed writes each
    one as its role or as a FlexConveyorModule only: the store infers the rest from the ontology."""
    db = connect(dataset)
    cross = shipped_topology("cross")

    seed_topology(db, cross, transit_seconds=0)

    assert str(CFC.Resource) in explicit_objects(
        db, FC.FlexConveyorModule, IRI(str(RDFS.subClassOf))
    )
    resources = {
        str(row["module"])
        for row in bindings(
            db, f"SELECT ?module WHERE {{ ?module a <{CFC.Resource}> }}"
        )
    }
    for module in cross.modules:
        assert str(module.resource) in resources, module.id
        assert explicit_objects(db, module.resource, IRI(str(RDF.type))) == [
            FC_NS + module.types[0]
        ], module.id
    # The roles are subclasses of FlexConveyorModule, so Modules 1, 2, 4 and 5 are FlexConveyorModules
    # by inference, and Module 3 is one as the seed writes it.
    for role in (FC.EntryModule, FC.ExitModule):
        assert str(FC.FlexConveyorModule) in explicit_objects(
            db, role, IRI(str(RDFS.subClassOf))
        )


def test_a_module_listed_with_two_types_gets_both_and_a_one_way_connection_stays_one_way(
    dataset,
):
    db = connect(dataset)
    topology = parse_topology(
        [
            module_entry(
                "Module1",
                "FlexConveyorModule",
                "EntryModule",
                links=[("East", "Module2")],
            ),
            module_entry("Module2", "ExitModule"),
        ]
    )

    seed_topology(db, topology, transit_seconds=0)

    written = explicit_individuals(db)
    assert set(written.objects(URIRef(module_iri(1)), RDF.type)) == {
        URIRef(FC.FlexConveyorModule),
        URIRef(FC.EntryModule),
    }
    # Read with what the reasoner infers too: no way back appears.
    links = bindings(
        db,
        f"""
        SELECT ?from ?to WHERE {{
            ?from <{FC.hasConnection}> ?connection . ?connection <{FC.connectsTo}> ?to .
        }}
        """,
    )
    assert [(IRI(str(r["from"])), IRI(str(r["to"]))) for r in links] == [
        (module_iri(1), module_iri(2))
    ]


def test_the_first_iterations_cross_file_loads_and_a_box_crosses_it(dataset, records):
    """From an entry module over Module 3 to an exit module, and back to the
    WarehouseManagementSystem."""
    with participants(dataset, load_topology(str(FIRST_ITERATIONS_CROSS))) as party:
        spawned = party.spawn(origin=str(module_iri(1)), destination=str(module_iri(4)))

        assert spawned["accepted"], spawned
        box, transport = IRI(spawned["box"]), IRI(spawned["operation"])
        eventually(
            lambda: taken_back_record(WMS, box, transport) in records(),
            timeout=JOURNEY_SECONDS,
        )
        assert operation_status(party.db, transport) == OperationStatus.DONE


def test_the_monitor_reads_the_seeded_cross_with_its_roles_and_every_connection_leads_both_ways(
    dataset,
):
    """On the cross, as the store holds it, every connection leads both ways, and the monitor
    keeps the query it sent for it, exactly as the store received it, with the rows it answered.
    The monitor maps destinations but not routes."""
    db = connect(dataset)
    seed_topology(db, shipped_topology("cross"), transit_seconds=0)

    reads = GraphReads(db)
    modules = reads.modules()
    drawn = wires(modules)

    assert {str(m.resource): m.type for m in modules} == {
        str(module_iri(n)): role
        for n, role in enumerate(
            (
                "EntryModule",
                "EntryModule",
                "FlexConveyorModule",
                "ExitModule",
                "ExitModule",
            ),
            1,
        )
    }
    assert len(drawn) == 4
    for wire in drawn:
        assert wire.between == wire.leads_to, wire
    (sent,) = reads.sent()
    assert (sent.read, sent.error) == ("modules", None)
    assert f"<{FC.connectsTo}>" in sent.text
    # The text, sent again as it is, answers as many rows: one for each connection and matching
    # class. The role modules answer once for their module class and once for their role class:
    # the four modules around Module 3 have 2 rows each, and Module 3 has four with its one class.
    assert sent.rows == len(bindings(db, sent.text)) == 12
