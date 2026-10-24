"""Every capability belongs to exactly one resource: the shape in the shapes both use cases seed.

A capability cannot exist without a resource, and a resource can have several. So every
capability individual has exactly one resource that links to it (`cfc:hasCapability`), and the
store refuses a write that leaves a capability with none or with two, at commit. The shape is SHACL
Core, a property shape on the inverse of `cfc:hasCapability` with a minimum and a maximum of one.

Without a store: these tests parse each use case's shapes file and ontology. The store's SHACL may
not follow `rdfs:subClassOf`, and the middleware types a capability by its use case's class only,
so the shape targets `cfc:Capability` and every class the ontology declares a direct subclass of
it. `test_capability_shape_live.py` makes the writes on a live store.
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.vocabulary import CFC
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, SH

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.vocabulary import JMS_NS
from jms_kapps.uc2.vocabulary import FC_NS

CAPABILITY = URIRef(str(CFC.Capability))
HAS_CAPABILITY = URIRef(str(CFC.hasCapability))

# Each use case: its package, its ontology file, its shapes file, and its namespace.
USE_CASES = {
    "uc1": (
        "jms_kapps.uc1",
        "integration_usecase.ttl",
        "integration_usecase_shapes.ttl",
        JMS_NS,
    ),
    "uc2": (
        "jms_kapps.uc2",
        "flexconveyor_usecase.ttl",
        "flexconveyor_usecase_shapes.ttl",
        FC_NS,
    ),
}


def parsed(package: str, folder: str, name: str) -> Graph:
    return Graph().parse(data=read_asset(package, folder, name), format="turtle")


@pytest.fixture(params=sorted(USE_CASES))
def use_case(request) -> tuple[Graph, Graph, str]:
    """The ontology, the shapes and the namespace of one use case."""
    package, ontology, shapes, namespace = USE_CASES[request.param]
    return (
        parsed(package, "ontology", ontology),
        parsed(package, "shapes", shapes),
        namespace,
    )


def capability_shape(namespace: str) -> URIRef:
    return URIRef(f"{namespace}CapabilityShape")


def resource_shape(namespace: str) -> URIRef:
    return URIRef(f"{namespace}CapabilityResourceShape")


def test_the_capability_shape_is_a_node_shape_with_one_named_property_shape(
    use_case,
) -> None:
    _, shapes, namespace = use_case
    node = capability_shape(namespace)
    assert (node, RDF.type, SH.NodeShape) in shapes
    assert list(shapes.objects(node, SH.property)) == [resource_shape(namespace)]


def test_the_property_shape_asks_for_exactly_one_resource_that_links_to_the_capability(
    use_case,
) -> None:
    _, shapes, namespace = use_case
    prop = resource_shape(namespace)
    assert (prop, RDF.type, SH.PropertyShape) in shapes
    path = shapes.value(prop, SH.path)
    assert shapes.value(path, SH.inversePath) == HAS_CAPABILITY
    assert shapes.value(prop, SH.minCount) == Literal(1)
    assert shapes.value(prop, SH.maxCount) == Literal(1)
    message = shapes.value(prop, SH.message)
    assert message is not None and "exactly one resource" in str(message)


def test_the_shape_targets_capability_and_each_capability_class_of_the_ontology(
    use_case,
) -> None:
    ontology, shapes, namespace = use_case
    classes = set(ontology.subjects(RDFS.subClassOf, CAPABILITY))
    assert classes, "the ontology declares no capability class"
    targets = set(shapes.objects(capability_shape(namespace), SH.targetClass))
    assert targets == classes | {CAPABILITY}
