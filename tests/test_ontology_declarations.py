"""The dataset's ruleset is enough for the ontologies the use cases load.

`graphdb/jms-kapps-repo-config.ttl` runs the `rdfsplus-optimized` ruleset. Under it, GraphDB does
not report a class that is named only as the `rdfs:range` of a property as an `owl:Class` or an
`rdfs:Class`, and the object-graph mapper refuses an IRI as a class without that report. So every
class a range names must be declared: in a use case's own ontology, or in one of the middleware's
three shared ontologies that a seed loads beside it.

Offline: the files are read from the installed packages, and no store is involved.
"""

from __future__ import annotations

from importlib import resources

import pytest
from rdflib import OWL, RDF, RDFS, XSD, Graph, URIRef

# Every Turtle file in these folders is an ontology a seed loads.
USE_CASE_ONTOLOGY_FOLDERS = [
    ("jms_kapps.uc1", "ontology"),
    ("jms_kapps.uc2", "ontology"),
]

# The three a seed loads beside a use case's ontology, each into its own named graph.
SHARED_ONTOLOGIES = [
    ("kapps_semantic_middleware", "ontology/core.ttl"),
    ("kapps_semantic_middleware", "ontology/service.ttl"),
    ("kapps_semantic_middleware", "ontology/mes.ttl"),
]

# Ranges that name literals or everything, not a class the mapper looks up.
NOT_CLASSES = {RDFS.Literal, RDFS.Resource, RDF.langString, OWL.Thing}


@pytest.fixture(scope="module")
def loaded() -> Graph:
    """The union of every ontology a seed loads, as one graph."""
    graph = Graph()
    for package, folder in USE_CASE_ONTOLOGY_FOLDERS:
        files = [
            entry
            for entry in resources.files(package).joinpath(folder).iterdir()
            if entry.name.endswith(".ttl")
        ]
        assert files, f"{package}/{folder} holds no ontology"
        for entry in files:
            graph.parse(data=entry.read_text(encoding="utf-8"), format="turtle")
    for package, path in SHARED_ONTOLOGIES:
        text = resources.files(package).joinpath(path).read_text(encoding="utf-8")
        graph.parse(data=text, format="turtle")
    return graph


def test_every_class_a_range_names_is_declared(loaded: Graph) -> None:
    declared = set(loaded.subjects(RDF.type, OWL.Class)) | set(
        loaded.subjects(RDF.type, RDFS.Class)
    )
    undeclared = sorted(
        f"{prop} rdfs:range {range_}"
        for prop, range_ in loaded.subject_objects(RDFS.range)
        if isinstance(range_, URIRef)
        and not str(range_).startswith(str(XSD))
        and range_ not in NOT_CLASSES
        and range_ not in declared
    )
    assert not undeclared, (
        "These ranges name a class that no loaded ontology declares. Declare each one, or the "
        "dataset needs a ruleset that infers it:\n" + "\n".join(undeclared)
    )
