"""Every capability belongs to exactly one resource, proven on a live store.

A capability cannot exist without a resource, and a resource can have several. The shapes that
both use cases seed hold that rule, `:CapabilityResourceShape`: exactly one resource links to each
capability (`cfc:hasCapability`). The store checks it at every commit, so it refuses a write that
leaves a capability with no resource, or with two, and keeps nothing of it. The writes are the ones
`jms-uc2 capability-resource` makes, plain triples in one SPARQL update each, since the middleware
never writes a capability without its resource's link.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import io

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import validation_results
from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.seed import reset_uc1
from jms_kapps.uc1.vocabulary import JMS, JMS_NS
from jms_kapps.uc2.capability_resource import (
    NO_RESOURCE,
    RESOURCE_2,
    TRANSPORT_1,
    TWO_RESOURCES,
    TYPE,
    WRITES,
    Write,
    demo,
    make,
    stored,
)
from jms_kapps.uc2.seed import reset_uc2
from jms_kapps.uc2.vocabulary import FC, FC_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

ADMITTED, NO_RESOURCE_WRITE, TWO_RESOURCES_WRITE = WRITES
UC2_RULE = f"{FC_NS}CapabilityResourceShape"


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


@pytest.mark.paper_claim
def test_a_resource_that_registers_two_capabilities_is_admitted(db):
    """The resource and both its capabilities, each linked from it, in one write: admitted, and
    the store holds every triple of it.

    Paper claim 18: every capability belongs to exactly one resource, checked at commit."""
    reset_uc2(db)

    assert make(db, ADMITTED).admitted
    assert sorted(stored(db, ADMITTED.triples)) == sorted(ADMITTED.triples)


@pytest.mark.paper_claim
def test_a_capability_with_no_resource_is_refused(seeded):
    """A capability that no resource links to: refused, by the rule at that capability.

    Paper claim 18: every capability belongs to exactly one resource, checked at commit."""
    assert refused_by(seeded, NO_RESOURCE_WRITE) == {(str(NO_RESOURCE), UC2_RULE)}


@pytest.mark.paper_claim
def test_a_capability_that_two_resources_claim_is_refused(seeded):
    """A new capability, and a new resource, and both resources linking to the capability, in one
    write: refused, by the rule at that capability.

    Paper claim 18: every capability belongs to exactly one resource, checked at commit."""
    assert refused_by(seeded, TWO_RESOURCES_WRITE) == {(str(TWO_RESOURCES), UC2_RULE)}


@pytest.mark.paper_claim
def test_a_second_resource_that_claims_a_registered_capability_later_is_refused(seeded):
    """A capability that its resource registered in an earlier commit, and a second resource
    that links to it in a later one, the way a second claim would come: refused, by the rule at
    that capability, although the write does not touch the capability itself.

    Paper claim 18: every capability belongs to exactly one resource, checked at commit."""
    later = Write(
        "A second resource that claims a registered capability",
        (
            (RESOURCE_2, TYPE, FC.FlexConveyorModule),
            (RESOURCE_2, CFC.hasCapability, TRANSPORT_1),
        ),
        should_be_admitted=False,
    )

    assert refused_by(seeded, later) == {(str(TRANSPORT_1), UC2_RULE)}


@pytest.mark.paper_claim
def test_use_case_1_seeds_the_rule_too(db):
    """One of use case 1's capabilities with no resource, on use case 1's seed: refused, by use
    case 1's copy of the rule.

    Paper claim 18: every capability belongs to exactly one resource, checked at commit."""
    reset_uc1(db)
    orphan = IRI("UnscrewingCapability_of_no_resource", base=JMS_NS)
    write = Write(
        "A capability of use case 1 with no resource",
        ((orphan, TYPE, JMS.UnscrewingCapability),),
        should_be_admitted=False,
    )

    assert refused_by(db, write) == {(str(orphan), f"{JMS_NS}CapabilityResourceShape")}


def test_the_demonstration_finds_each_answer_as_it_expects(db):
    """`jms-uc2 capability-resource`'s three writes, on the live store: it exits 0."""
    out = io.StringIO()

    assert demo(db, out=out) == 0, out.getvalue()
