"""The shapes document that a UC1 or UC2 seed deploys holds the middleware's operation-status
shape, whatever else it holds, and a SHACL-off seed sends none.

Without a store: a stand-in records the one `shapes_set` of each seed, and the document is parsed
as Turtle, so a clash of prefixes or a lost statement shows.
"""

from __future__ import annotations

from kapps_semantic_middleware.seeding import read_service_shapes
from rdflib import Graph, Namespace
from rdflib.compare import isomorphic
from test_uc2_seed_shapes import StandIn, seed

from jms_kapps.common.triplestore import read_asset, reset
from jms_kapps.uc1.seed import reset_uc1
from jms_kapps.uc2.seed import shapes_document

SVC = Namespace("http://w3id.org/circularfactory/Service#")
ONTOLOGY = read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl")


def sent_shapes(store: StandIn) -> str:
    (data,) = [data for kind, data in store.sent if kind == "shapes"]
    return str(data)


def parsed(document: str) -> Graph:
    return Graph().parse(data=document, format="turtle")


def test_the_use_case_1_seed_deploys_the_operation_status_shape() -> None:
    store = StandIn()

    reset_uc1(store)

    deployed = parsed(sent_shapes(store))
    assert (SVC.OperationStatusShape, None, None) in deployed
    uc1 = parsed(
        read_asset("jms_kapps.uc1", "shapes", "integration_usecase_shapes.ttl")
    )
    assert len(deployed) == len(uc1) + len(parsed(read_service_shapes()))


def test_the_use_case_1_seed_loads_the_uncertainty_module_and_its_shapes() -> None:
    """Every launch of use case 1 resets through `reset_uc1`: it imports the ontology with the
    uncertainty module, and deploys the shapes of a measured position."""
    store = StandIn()

    reset_uc1(store)

    uq = Namespace("http://w3id.org/circularfactory/Uncertainty#")
    uns = Namespace("http://w3id.org/circularfactory/Unscrewing#")
    imported = Graph()
    for kind, content in store.sent:
        if kind == "import":
            imported.parse(data=str(content), format="turtle")
    assert (uq.RandomVariable, None, None) in imported
    assert (uns.hasScrewHeadPositionVariable, None, None) in imported
    deployed = parsed(sent_shapes(store))
    for name in (
        "RandomVariableShape",
        "ScrewMeasuredPositionShape",
        "UnscrewingOperationVariableShape",
    ):
        assert (uns[name], None, None) in deployed


def test_the_use_case_2_seed_deploys_it_with_either_shapes_file_set() -> None:
    for sparql in (False, True):
        store = StandIn()

        seed(store, sparql_shapes=sparql)

        deployed = parsed(sent_shapes(store))
        assert (SVC.OperationStatusShape, None, None) in deployed
        own = parsed(shapes_document(sparql_shapes=sparql))
        assert len(deployed) == len(own) + len(parsed(read_service_shapes()))


def test_an_empty_shapes_document_still_gets_the_shape() -> None:
    store = StandIn()

    reset(store, ontology=ONTOLOGY, shapes="")

    assert isomorphic(parsed(sent_shapes(store)), parsed(read_service_shapes()))


def test_a_reset_without_shapes_sends_none() -> None:
    store = StandIn()

    reset(store, ontology=ONTOLOGY, shapes=None)

    assert "shapes" not in store.kinds()
