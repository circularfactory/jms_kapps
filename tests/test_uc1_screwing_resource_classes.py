"""The classes of the screwing resource, and the capability of the detector it calls, offline.

Each is declared in `uc1/ontology/integration_usecase.ttl` as a direct subclass of its Core or
service class: the middleware checks a class of a service, a workflow or a capability by one
`rdfs:subClassOf` triple, and does not follow a chain.
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.vocabulary import CFC, SVC
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.vocabulary import JMS


@pytest.fixture(scope="module")
def graph() -> Graph:
    graph = Graph()
    graph.parse(
        data=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        format="turtle",
    )
    return graph


@pytest.mark.parametrize(
    ("cls", "superclass"),
    [
        (JMS.ScrewingResourceService, SVC.Service),
        (JMS.UnscrewingWorkflow, SVC.Workflow),
        (JMS.UnscrewingCapability, CFC.Capability),
        (JMS.ClassifyCapability, CFC.Capability),
    ],
)
def test_each_class_is_a_direct_subclass_of_its_core_or_service_class(
    graph: Graph, cls, superclass
) -> None:
    assert (URIRef(str(cls)), RDF.type, OWL.Class) in graph
    assert set(graph.objects(URIRef(str(cls)), RDFS.subClassOf)) == {
        URIRef(str(superclass))
    }
