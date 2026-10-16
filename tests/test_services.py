"""The one reader of a participant's service: which services it finds live, and what it reads of
each.

The query runs as it is written, against an rdflib graph in memory that answers the way the GraphDB
client does with its bindings converted. A service is written the way the middleware writes one:
bound to its resource, with an address and a heartbeat, and its workflows, each with its class and
its endpoint. Discovery's own tests (`test_uc2_discovery.py`) and the guard's
(`test_supervision.py`) run their reads through this reader too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI
from rdflib import RDF, XSD, Graph, Literal, URIRef

from jms_kapps.common.services import (
    MAX_HEARTBEAT_AGE_SECONDS,
    live_resources,
    reader,
    services,
)
from jms_kapps.uc2.vocabulary import FC, WMS, module_iri

M1, M2 = module_iri(1), module_iri(2)
FRESH = 2.0
OLDER_BUT_FRESH = MAX_HEARTBEAT_AGE_SECONDS / 2
STALE = MAX_HEARTBEAT_AGE_SECONDS * 3


class Store:
    """Services in memory, and the client's `query` over them."""

    def __init__(self) -> None:
        self.graph = Graph()
        self.reads = 0

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        self.reads += 1
        rows = [
            {
                name: IRI(str(value)) if isinstance(value, URIRef) else value.toPython()
                for name, value in row.asdict().items()
            }
            for row in self.graph.query(query)
        ]
        return {"results": {"bindings": rows}}

    def service(
        self,
        resource: IRI,
        port: int,
        *,
        heartbeat_age: float | None = FRESH,
        address: bool = True,
        workflows: tuple[IRI, ...] = (),
    ) -> str:
        """Bind a service at `port` to `resource`, with a workflow of each class in `workflows`,
        and return its address. `heartbeat_age` None writes no heartbeat yet; `address` False is a
        service that deregistered, which keeps no address and no endpoint."""
        base = f"http://127.0.0.1:{port}"
        service = URIRef(f"{resource}_service_{port}")
        self.graph.add((service, URIRef(SVC.isServiceOf), URIRef(resource)))
        if address:
            self.graph.add((service, URIRef(SVC.address), Literal(base)))
        if heartbeat_age is not None:
            beat = datetime.now(UTC) - timedelta(seconds=heartbeat_age)
            self.graph.add(
                (
                    service,
                    URIRef(SVC.lastHeartbeat),
                    Literal(beat, datatype=XSD.dateTime),
                )
            )
        for workflow_class in workflows:
            workflow = URIRef(f"{service}_{workflow_class.fragment}")
            self.graph.add((workflow, URIRef(SVC.isWorkflowOf), service))
            self.graph.add((workflow, RDF.type, URIRef(workflow_class)))
            if address:
                self.graph.add(
                    (
                        workflow,
                        URIRef(SVC.endpoint),
                        Literal(f"{base}/{workflow_class.fragment}"),
                    )
                )
        return base


def test_a_live_service_is_read_with_its_resource_its_address_and_its_heartbeat():
    store = Store()
    m1 = store.service(M1, 40001)

    [found] = services(store)

    assert (found.resource, found.address) == (M1, m1)
    assert found.heartbeat is not None
    assert found.heartbeat > datetime.now(UTC) - timedelta(seconds=FRESH + 5)


@pytest.mark.parametrize(
    "heartbeat_age, address",
    [(STALE, True), (None, True), (FRESH, False)],
    ids=["killed, its heartbeat stale", "starting, no heartbeat yet", "deregistered"],
)
def test_a_service_that_is_not_live_is_not_read(heartbeat_age, address):
    store = Store()
    store.service(M1, 40001, heartbeat_age=heartbeat_age, address=address)

    assert services(store) == []
    assert live_resources(store) == set()


def test_every_service_is_read_with_what_it_has_when_the_read_is_not_only_of_live_ones():
    """What a check reads after a run has stopped: each service deregistered, and keeps its
    heartbeat."""
    store = Store()
    store.service(M1, 40001, address=False)
    store.service(M2, 40002, heartbeat_age=None)

    found = {s.resource: s for s in services(store, live_only=False)}

    assert found[M1].address is None and found[M1].heartbeat is not None
    assert found[M2].address == "http://127.0.0.1:40002" and found[M2].heartbeat is None


def test_the_newest_heartbeat_comes_first_of_two_live_services_of_one_resource():
    # An instance killed a moment ago, whose heartbeat has not gone stale yet, and its restart.
    store = Store()
    store.service(M1, 40002, heartbeat_age=OLDER_BUT_FRESH)
    restart = store.service(M1, 40001, heartbeat_age=FRESH)

    assert [s.address for s in services(store, resource=M1)] == [
        restart,
        "http://127.0.0.1:40002",
    ]


def test_a_read_of_one_resource_reads_no_other_and_one_of_every_resource_reads_each():
    store = Store()
    m1 = store.service(M1, 40001)
    m2 = store.service(M2, 40002)

    assert [s.address for s in services(store, resource=M2)] == [m2]
    assert {s.resource: s.address for s in services(store)} == {M1: m1, M2: m2}


def test_the_workflows_asked_for_are_read_with_their_endpoints_and_no_other():
    store = Store()
    m1 = store.service(M1, 40001, workflows=(FC.ReserveWorkflow, FC.ReceiveWorkflow))

    [found] = services(store, workflows=(FC.ReserveWorkflow, FC.BoxTransportWorkflow))

    assert found.workflows == {FC.ReserveWorkflow: f"{m1}/ReserveWorkflow"}
    [unasked] = services(store)
    assert unasked.workflows == {}


def test_a_service_that_has_not_registered_its_workflows_yet_is_read_without_them():
    """The middleware writes the service and its first heartbeat before its workflows, so a live
    service can lack them for a moment: the WMS's spawn workflow did, after a launch."""
    store = Store()
    store.service(WMS, 40003)

    [found] = services(store, workflows=(FC.SpawnBoxWorkflow, FC.SetSpawnRateWorkflow))

    assert found.workflows == {}


def test_a_live_service_with_many_workflows_is_one_service_in_one_read():
    store = Store()
    store.service(
        M1,
        40001,
        workflows=(FC.ReserveWorkflow, FC.ReceiveWorkflow, FC.BoxTransportWorkflow),
    )
    store.service(WMS, 40003, workflows=(FC.SpawnBoxWorkflow, FC.SetSpawnRateWorkflow))

    read = reader(store)(
        (
            FC.ReserveWorkflow,
            FC.ReceiveWorkflow,
            FC.BoxTransportWorkflow,
            FC.SpawnBoxWorkflow,
            FC.SetSpawnRateWorkflow,
        )
    )

    assert {s.resource: len(s.workflows) for s in read} == {M1: 3, WMS: 2}
    assert store.reads == 1


def test_a_heartbeat_counts_as_fresh_up_to_the_age_it_is_given():
    store = Store()
    store.service(M1, 40001, heartbeat_age=STALE)

    assert live_resources(store) == set()
    assert live_resources(store, max_age_seconds=STALE * 2) == {M1}
