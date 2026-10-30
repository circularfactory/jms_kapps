"""Every capability belongs to exactly one resource, proven on a live store.

A capability cannot exist without a resource, and a resource can have several. The shapes that
both use cases seed hold that rule, `:CapabilityResourceShape`: exactly one resource links to each
capability (`cfc:hasCapability`). The store checks it at every commit, so it refuses a write that
leaves a capability with no resource, or with two, and keeps nothing of it. The writes are plain
triples in one SPARQL update each, since the middleware never writes a capability without its
resource's link.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import validation_results
from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import (
    IRI,
    ShaclValidationError,
    SPARQLQuery,
    TripleStore,
)
from rdflib.namespace import RDF

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.seed import reset_uc1
from jms_kapps.uc1.vocabulary import JMS, JMS_NS
from jms_kapps.uc2.seed import Triple, reset_uc2
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

RESOURCE_1 = IRI("CapabilityDemoModule1", base=FCI_NS)
RESOURCE_2 = IRI("CapabilityDemoModule2", base=FCI_NS)
TRANSPORT_1 = IRI("CapabilityDemoModule1_capability_transport", base=FCI_NS)
RESERVE_1 = IRI("CapabilityDemoModule1_capability_reserve", base=FCI_NS)
NO_RESOURCE = IRI("CapabilityDemoCapability_of_no_resource", base=FCI_NS)
TWO_RESOURCES = IRI("CapabilityDemoCapability_of_two_resources", base=FCI_NS)

TYPE = IRI(str(RDF.type))

UC2_RULE = f"{FC_NS}CapabilityResourceShape"


@dataclass(frozen=True)
class Write:
    """One write: what it shows, its triples, and whether a store seeded with the shapes admits
    it."""

    title: str
    triples: tuple[Triple, ...]
    should_be_admitted: bool

    def update(self) -> str:
        """The SPARQL update that makes the write, in one commit."""
        lines = "\n".join(f"  <{s}> <{p}> <{o}> ." for s, p, o in self.triples)
        return f"INSERT DATA {{\n{lines}\n}}"


ADMITTED = Write(
    "A resource that registers two capabilities",
    (
        (RESOURCE_1, TYPE, FC.FlexConveyorModule),
        (TRANSPORT_1, TYPE, FC.BoxTransportCapability),
        (RESERVE_1, TYPE, FC.ReserveCapability),
        (RESOURCE_1, CFC.hasCapability, TRANSPORT_1),
        (RESOURCE_1, CFC.hasCapability, RESERVE_1),
    ),
    should_be_admitted=True,
)
NO_RESOURCE_WRITE = Write(
    "A capability with no resource",
    ((NO_RESOURCE, TYPE, FC.BoxTransportCapability),),
    should_be_admitted=False,
)
TWO_RESOURCES_WRITE = Write(
    "A capability that two resources claim",
    (
        (RESOURCE_2, TYPE, FC.FlexConveyorModule),
        (TWO_RESOURCES, TYPE, FC.BoxTransportCapability),
        (RESOURCE_1, CFC.hasCapability, TWO_RESOURCES),
        (RESOURCE_2, CFC.hasCapability, TWO_RESOURCES),
    ),
    should_be_admitted=False,
)


@dataclass(frozen=True)
class Answer:
    """What the store answered to a write: whether it admitted it, and the report of a refusal."""

    admitted: bool
    report: str = ""


def make(db: TripleStore, write: Write) -> Answer:
    """Make `write` against `db`, and return what the store answered."""
    try:
        db.query(write.update(), update=True)
    except ShaclValidationError as refused:
        return Answer(admitted=False, report=refused.report)
    return Answer(admitted=True)


def stored(db: TripleStore, triples: tuple[Triple, ...]) -> list[Triple]:
    """The ones of `triples` that `db` holds as explicit statements."""
    values = " ".join(f"(<{s}> <{p}> <{o}>)" for s, p, o in triples)
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?s", "?p", "?o"],
            where_clauses=[f"VALUES (?s ?p ?o) {{ {values} }}", "?s ?p ?o"],
            include_implicit=False,
        ),
        convert=False,
    )
    return [
        (IRI(row["s"]["value"]), IRI(row["p"]["value"]), IRI(row["o"]["value"]))
        for row in rows
    ]


@pytest.fixture(scope="module")
def db(dataset):
    return connect(dataset)


@pytest.fixture
def seeded(db):
    """Use case 2's ontology and shapes, and a resource that registered two capabilities."""
    reset_uc2(db)
    assert make(db, ADMITTED).admitted
    return db


def refused_by(db, write: Write) -> set[tuple[str, str]]:
    """Make `write`, which the store must refuse and keep nothing of, and return the focus node
    and the source shape of each result of the store's report."""
    answer = make(db, write)
    assert not answer.admitted
    assert stored(db, write.triples) == []
    return {
        (result.focus, result.shape) for result in validation_results(answer.report)
    }


def test_a_resource_that_registers_two_capabilities_is_admitted(db):
    """The resource and both its capabilities, each linked from it, in one write: admitted, and
    the store holds every triple of it.

    Every capability belongs to exactly one resource, checked at commit."""
    reset_uc2(db)

    assert make(db, ADMITTED).admitted
    assert sorted(stored(db, ADMITTED.triples)) == sorted(ADMITTED.triples)


def test_a_capability_with_no_resource_is_refused(seeded):
    """A capability that no resource links to: refused, by the rule at that capability.

    Every capability belongs to exactly one resource, checked at commit."""
    assert refused_by(seeded, NO_RESOURCE_WRITE) == {(str(NO_RESOURCE), UC2_RULE)}


def test_a_capability_that_two_resources_claim_is_refused(seeded):
    """A new capability, and a new resource, and both resources linking to the capability, in one
    write: refused, by the rule at that capability.

    Every capability belongs to exactly one resource, checked at commit."""
    assert refused_by(seeded, TWO_RESOURCES_WRITE) == {(str(TWO_RESOURCES), UC2_RULE)}


def test_a_second_resource_that_claims_a_registered_capability_later_is_refused(seeded):
    """A capability that its resource registered in an earlier commit, and a second resource
    that links to it in a later one, the way a second claim would come: refused, by the rule at
    that capability, although the write does not touch the capability itself.

    Every capability belongs to exactly one resource, checked at commit."""
    later = Write(
        "A second resource that claims a registered capability",
        (
            (RESOURCE_2, TYPE, FC.FlexConveyorModule),
            (RESOURCE_2, CFC.hasCapability, TRANSPORT_1),
        ),
        should_be_admitted=False,
    )

    assert refused_by(seeded, later) == {(str(TRANSPORT_1), UC2_RULE)}


def test_use_case_1_seeds_the_rule_too(db):
    """One of use case 1's capabilities with no resource, on use case 1's seed: refused, by use
    case 1's copy of the rule.

    Every capability belongs to exactly one resource, checked at commit."""
    reset_uc1(db)
    orphan = IRI("UnscrewingCapability_of_no_resource", base=JMS_NS)
    write = Write(
        "A capability of use case 1 with no resource",
        ((orphan, TYPE, JMS.UnscrewingCapability),),
        should_be_admitted=False,
    )

    assert refused_by(db, write) == {(str(orphan), f"{JMS_NS}CapabilityResourceShape")}
