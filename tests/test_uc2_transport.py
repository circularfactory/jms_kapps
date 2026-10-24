"""A box and its transport, written through the middleware's commits on a live GraphDB.

The records write through the WarehouseManagementSystem's middleware, which is built here and
never served: a commit needs only its object-graph mapper. The object-graph mapper finds a
property of a class only through the property's `rdfs:domain`, so each write here proves that the
ontology declares one: the transport's own properties, and the middleware's `svc:hasOperationStatus`
and `svc:hasExecutionResult`, whose domain is Core's `cfc:Operation`. The fetch is the one the
middleware's event trigger makes when a party is notified of the transport. The seed, which writes
directly before any party starts, links a box and its transport as the opening does.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import pytest
from _bulk import bulk_validation_report, conforms
from _explicit import explicit_links
from _participants import TWO_MODULES
from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, OperationStatus
from kapps_triplestore_interface import (
    IRI,
    PreconditionFailedError,
    ShaclValidationError,
)

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.discovery import GraphView, boxes_held_by
from jms_kapps.uc2.seed import place_box, seed_topology
from jms_kapps.uc2.transport import (
    TRANSPORT_SCOPE,
    Transport,
    TransportRecords,
    boxes,
    execution_result,
    failure,
    operation_status,
    transport_from,
)
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, box_iri, module_iri, transport_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

M1, M2 = module_iri(1), module_iri(2)
B1 = box_iri(1)
T1 = transport_iri(B1)
# A Capability the transport implements: the origin's BoxTransportCapability, which the
# middleware writes when the origin registers its BoxTransportWorkflow; none is registered here.
CAPABILITY = FCI_NS + "Module1_capability_box_transport"


@pytest.fixture
def mw(dataset):
    """The WMS's middleware on module 1 connected to module 2, as a launch seeds them."""
    db = connect(dataset)
    seed_topology(db, TWO_MODULES, transit_seconds=0)
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(WMS),
        service_class=str(FC.WarehouseManagementSystemService),
        ogm=OGM(db=db),
    )


@pytest.fixture
def records(mw):
    return TransportRecords(mw)


def opened(records) -> None:
    records.open(B1, origin=M1, destination=M2, capability=CAPABILITY, possessor=WMS)


def test_one_commit_opens_the_transport_queued_assigned_to_the_origin_with_the_box_in_the_wms(
    mw, records
):
    operation = records.open(
        B1, origin=M1, destination=M2, capability=CAPABILITY, possessor=WMS
    )

    db = mw.ogm.db
    assert operation == T1
    assert boxes(db) == [B1]
    assert boxes_held_by(db, WMS) == [B1]
    # Read as the middleware's intake reads it for a module: once, under the transport's scope.
    read = mw.fetch(T1, class_scope=TRANSPORT_SCOPE)
    assert read.class_spec.iri == FC.BoxTransportOperation
    assert transport_from(read) == Transport(
        T1, B1, M1, M2, OperationStatus.QUEUED, assigned=M1
    )
    implements = bindings(
        db, f"SELECT ?c WHERE {{ <{T1}> <{CFC.implementsCapability}> ?c }}"
    )
    assert [str(row["c"]) for row in implements] == [CAPABILITY]
    # The box links to its transport, and the transport to its box: both written, not inferred.
    assert explicit_links(db, B1, T1) == ([str(T1)], [str(B1)])
    report = bulk_validation_report(db)
    assert conforms(report), report.serialize()


def test_a_box_is_read_with_its_transport_in_one_fetch_through_its_link(mw, records):
    """What a module reads of a box it holds: the box with its transport, through
    `fc:hasTransport`, never from the box's name. The reasoner infers the same link from
    `fc:hasBox`, and the fetch still finds one transport."""
    opened(records)

    graph = GraphView(mw.ogm.db, fetch=mw.fetch)

    assert graph.transport_of(B1) == Transport(
        T1, B1, M1, M2, OperationStatus.QUEUED, assigned=M1
    )


def test_a_box_is_opened_once(records):
    opened(records)

    with pytest.raises(PreconditionFailedError):
        opened(records)


def test_a_transport_does_not_run_while_the_wms_holds_its_box(mw, records):
    """The store refuses `running` before the box is on its origin."""
    opened(records)

    with pytest.raises(ShaclValidationError):
        records.start(T1)

    assert operation_status(mw.ogm.db, T1) == OperationStatus.QUEUED


def test_a_transport_that_fails_says_why_with_the_stores_report(mw, records):
    opened(records)

    records.fail(T1, "the triple store refused the handover", "_:r <p> <o> .")

    db = mw.ogm.db
    assert operation_status(db, T1) == OperationStatus.FAILED
    assert execution_result(db, T1) == failure(
        "the triple store refused the handover", "_:r <p> <o> ."
    )
    assert boxes_held_by(db, WMS) == [B1]
    report = bulk_validation_report(db)
    assert conforms(report), report.serialize()


def test_the_seed_places_a_box_linked_to_its_transport_both_ways(mw):
    """The seed chooses the IRI of the transport it creates: here one that does not follow the
    box's name, which the box's link still finds."""
    transport = IRI("Transport7", base=FCI_NS)

    placed = place_box(
        mw.ogm, B1, on=M1, origin=M1, destination=M2, transport=transport
    )

    db = mw.ogm.db
    assert placed == transport
    assert explicit_links(db, B1, transport) == ([str(transport)], [str(B1)])
    assert GraphView(db, fetch=mw.fetch).transport_of(B1) == Transport(
        transport, B1, M1, M2, OperationStatus.RUNNING, assigned=M1
    )
    report = bulk_validation_report(db)
    assert conforms(report), report.serialize()
