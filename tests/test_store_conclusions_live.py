"""What the store materializes from a small ontology of its own, on a live store.

The ontology has one property with a domain and a range, one subclass axiom and one inverse pair.
The store must materialize the subclass conclusion and the inverse conclusion: the use cases rely
on both. The test prints whether the store also typed an individual from the range and from the
domain, and asserts nothing about either: no read of the use cases relies on those two (see the glossary entry
"Inference").

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.seeding import clear_dataset

from jms_kapps.common.triplestore import connect

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

NS = "urn:test:conclusions#"

ONTOLOGY = f"""
@prefix : <{NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Thing a owl:Class .
:SubThing a owl:Class ; rdfs:subClassOf :Thing .
:Owner a owl:Class .
:Held a owl:Class .

:holds a owl:ObjectProperty ; rdfs:domain :Owner ; rdfs:range :Held ; owl:inverseOf :heldBy .
:heldBy a owl:ObjectProperty .
"""

# `:sub` is explicitly a SubThing only. `:a` and `:b` hold nothing of a class: `:a :holds :b`
# is the only statement about either, so a type of either comes from a domain or a range.
INDIVIDUALS = f"""
@prefix : <{NS}> .
:sub a :SubThing .
:a :holds :b .
"""


def ask(db, pattern: str) -> bool:
    return bool(db.query(f"ASK {{ {pattern} }}").get("boolean", False))


@pytest.fixture(scope="module")
def db(dataset):
    db = connect(dataset)
    clear_dataset(db)
    db.graph_add(ONTOLOGY, content_type="application/x-turtle")
    db.graph_add(INDIVIDUALS, content_type="application/x-turtle")
    return db


def test_the_store_materializes_the_subclass_conclusion(db):
    assert ask(db, f"<{NS}sub> a <{NS}Thing>")


def test_the_store_materializes_the_inverse_conclusion(db):
    assert ask(db, f"<{NS}b> <{NS}heldBy> <{NS}a>")


def test_what_the_store_does_with_a_range_and_a_domain_is_printed_only(db, capsys):
    """Information only: nothing is asserted about either answer."""
    from_range = ask(db, f"<{NS}b> a <{NS}Held>")
    from_domain = ask(db, f"<{NS}a> a <{NS}Owner>")
    with capsys.disabled():
        print(
            f"\nstore conclusions: range -> {from_range}, domain -> {from_domain}",
            flush=True,
        )
