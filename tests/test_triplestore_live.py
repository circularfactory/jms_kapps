"""A seed on a live GraphDB replaces the `svc:` ontology that an earlier release of the middleware
left in the dataset.

Core, `svc:` and `mes:` each live in a named graph of their own, which a seed's clear leaves
standing. A dataset seeded under an earlier release of the middleware therefore still holds
that release's `svc:` ontology. A newer release writes terms the older ontology does not declare,
such as an Operation's issuer, and the object-graph mapper refuses a write of a property that the
class does not have. So every seed loads the three again from the installed middleware. The test
checks `svc:`, the one whose change showed it.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.seeding import (
    SERVICE_GRAPH,
    clear_dataset,
    load_shared_ontologies,
)
from kapps_semantic_middleware.vocabulary import SVC
from rdflib.namespace import OWL

from jms_kapps.common.triplestore import connect, reset
from jms_kapps.uc1.seed import uc1_ontology

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# What an earlier release left in the graph of `svc:`: a statement that the installed ontology
# does not hold, and no declaration of the issuer.
LEFT_OVER = "<urn:test:earlier-release> <urn:test:left> <urn:test:over>"


def ask(db, pattern: str) -> bool:
    """Whether the graph of `svc:` holds `pattern`."""
    query = f"ASK {{ GRAPH <{SERVICE_GRAPH}> {{ {pattern} }} }}"
    return bool(db.query(query).get("boolean", False))


@pytest.fixture
def db(dataset):
    """The dataset, with the shared ontologies of the installed middleware again at the end,
    also when the seed under test left them stale."""
    db = connect(dataset)
    yield db
    load_shared_ontologies(db, reload=True)


def test_a_seed_replaces_the_service_ontology_that_an_earlier_release_left(db):
    # The data goes first, as in every seed: the dataset validates the data it holds the
    # moment a graph it reasons over changes.
    clear_dataset(db)
    db.graph_clear(SERVICE_GRAPH)
    db.graph_add(
        f"{LEFT_OVER} .", graph_iri=SERVICE_GRAPH, content_type="application/x-turtle"
    )

    reset(
        db,
        ontology=uc1_ontology(),
        shapes="",
    )

    assert not ask(db, LEFT_OVER)
    assert ask(db, f"<{SVC.issuedBy}> a <{OWL.ObjectProperty}>")
