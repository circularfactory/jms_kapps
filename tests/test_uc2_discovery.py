"""Which service discovery finds for a resource, when the resource has more than one.

The queries run as they are written, against an rdflib graph in memory that answers the way the
GraphDB client does with its bindings converted. A service is written the way the middleware
writes it: bound to its resource, with an address, a heartbeat and one workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from kapps_semantic_middleware.vocabulary import CFC, SVC
from kapps_triplestore_interface import IRI
from rdflib import RDF, XSD, Graph, Literal, URIRef

from jms_kapps.common.services import MAX_HEARTBEAT_AGE_SECONDS
from jms_kapps.uc2.discovery import (
    DirectedConnection,
    at_start,
    connections,
    modules_and_connections,
    service_address,
    service_addresses,
    workflow_endpoint,
)
from jms_kapps.uc2.routing import next_module
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.vocabulary import FC, FC_NS, module_iri

M1, M2 = module_iri(1), module_iri(2)

# How old a heartbeat is, in seconds.
FRESH = 2.0
OLDER_BUT_FRESH = MAX_HEARTBEAT_AGE_SECONDS / 2
STALE = MAX_HEARTBEAT_AGE_SECONDS * 3


class Store:
    """Services in memory, and the client's `query` over them."""

    def __init__(self) -> None:
        self.graph = Graph()

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
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
        heartbeat_age: float | None,
        address: bool = True,
    ) -> str:
        """Bind a service at `port` to `resource`, and return its address.

        `heartbeat_age` None writes no heartbeat. `address` False is a service part-way through
        deregistering: the middleware removes the address first, and each endpoint in a later write.
        """
        base = f"http://127.0.0.1:{port}"
        service = URIRef(f"{resource}_service_{port}")
        workflow = URIRef(f"{service}_receive")
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
        self.graph.add((workflow, URIRef(SVC.isWorkflowOf), service))
        self.graph.add((workflow, RDF.type, URIRef(FC.ReceiveWorkflow)))
        self.graph.add((workflow, URIRef(SVC.endpoint), Literal(f"{base}/receive")))
        return base


def found(store: Store, resource: IRI) -> tuple[str | None, str | None, str | None]:
    """What each of the three reads finds for `resource`: address, endpoint, and the map's entry."""
    return (
        service_address(store, resource),
        workflow_endpoint(store, resource, FC.ReceiveWorkflow),
        service_addresses(store).get(resource),
    )


def test_a_live_service_is_found_by_every_read():
    store = Store()
    live = store.service(M1, 40001, heartbeat_age=FRESH)

    assert found(store, M1) == (live, f"{live}/receive", live)


@pytest.mark.parametrize(
    "heartbeat_age, address",
    [(STALE, True), (None, True), (FRESH, False)],
    ids=["killed, its heartbeat stale", "starting, no heartbeat yet", "deregistering"],
)
def test_a_service_that_is_not_live_is_not_found(heartbeat_age, address):
    store = Store()
    store.service(M1, 40001, heartbeat_age=heartbeat_age, address=address)

    assert found(store, M1) == (None, None, None)
    assert service_addresses(store) == {}


# Both orders of the ports, so that neither service is found only because it sorts first.
@pytest.mark.parametrize("dead_port, live_port", [(40001, 40002), (40002, 40001)])
def test_of_two_services_on_one_resource_the_live_one_is_found(dead_port, live_port):
    store = Store()
    store.service(M1, dead_port, heartbeat_age=STALE)
    live = store.service(M1, live_port, heartbeat_age=FRESH)

    assert found(store, M1) == (live, f"{live}/receive", live)


@pytest.mark.parametrize("killed_port, restart_port", [(40001, 40002), (40002, 40001)])
def test_of_two_live_services_the_newest_heartbeat_is_found(killed_port, restart_port):
    # An instance killed a moment ago, whose heartbeat has not gone stale yet, and its restart.
    store = Store()
    store.service(M1, killed_port, heartbeat_age=OLDER_BUT_FRESH)
    restart = store.service(M1, restart_port, heartbeat_age=FRESH)

    assert found(store, M1) == (restart, f"{restart}/receive", restart)


def test_the_map_keeps_one_live_address_for_each_resource():
    store = Store()
    store.service(M1, 40001, heartbeat_age=STALE)
    m1 = store.service(M1, 40002, heartbeat_age=FRESH)
    store.service(M2, 40003, heartbeat_age=OLDER_BUT_FRESH)
    m2 = store.service(M2, 40004, heartbeat_age=FRESH)

    assert service_addresses(store) == {M1: m1, M2: m2}


def test_the_connections_of_the_seeded_cross_are_read_with_their_directions_in_one_read_and_route_through_module_3():
    store = Store()
    store.graph += shipped_topology("cross").graph(0)

    found = connections(store)

    m3 = module_iri(3)
    assert len(found) == 8
    assert {
        (m3, module_iri(1), "North"),
        (m3, module_iri(2), "West"),
        (m3, module_iri(4), "East"),
        (m3, module_iri(5), "South"),
    } <= set(found)
    pairs = [(c.source, c.target) for c in found]
    for entry in (module_iri(1), module_iri(2)):
        for exit_ in (module_iri(4), module_iri(5)):
            assert next_module(pairs, entry, exit_) == m3


def test_a_connection_whose_direction_is_none_of_the_four_is_read_without_one():
    store = Store()
    connection = URIRef(f"{M1}_connection")
    store.graph.add((URIRef(M1), URIRef(FC.hasConnection), connection))
    store.graph.add((connection, URIRef(FC.connectsTo), URIRef(M2)))
    store.graph.add((connection, URIRef(FC.hasDirection), URIRef(f"{FC.North}east")))

    assert connections(store) == [(M1, M2, None)]


class CountingStore(Store):
    """A store that counts the queries it answers: each is one round trip to GraphDB."""

    def __init__(self) -> None:
        super().__init__()
        self.queries = 0

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        self.queries += 1
        return super().query(query, convert_bindings=convert_bindings)


def test_a_module_reads_the_connections_and_the_boxes_it_possesses_at_its_start_in_one_read():
    store = CountingStore()
    store.graph += shipped_topology("cross").graph(0)
    m3, box = module_iri(3), URIRef(f"{FC_NS}Box7")
    possession = URIRef(f"{FC_NS}Box7_possession")
    store.graph.add((box, URIRef(CFC.hasPossessedWorkpiece), possession))
    store.graph.add((URIRef(m3), URIRef(CFC.hasPossessor), possession))

    read = at_start(store, m3)

    assert store.queries == 1
    assert set(read.connections) == set(connections(uncounted(store)))
    assert len(read.connections) == 8
    assert read.held == [IRI(str(box))]
    assert at_start(store, M1).held == []


def uncounted(store: Store) -> Store:
    """Another client of the same graph, whose queries are not counted."""
    other = Store()
    other.graph = store.graph
    return other


def test_the_wms_reads_the_modules_with_their_types_and_the_connections_in_one_read():
    """What a spawn reads to pick: the roles of each module, and where the connections lead, so
    that it picks only a destination the origin reaches (jms_kapps 51)."""
    store = CountingStore()
    store.graph += shipped_topology("tee").graph(0)

    read = modules_and_connections(store)

    m1, m2, m3, m4 = (module_iri(n) for n in range(1, 5))
    assert store.queries == 1
    # As the tee lists them: this store infers no class from another.
    assert read.types == {
        m1: ["EntryModule"],
        m2: ["FlexConveyorModule"],
        m3: ["ExitModule"],
        m4: ["ExitModule"],
    }
    assert sorted(read.connections) == [(m1, m2), (m2, m3), (m2, m4)]


def test_the_wms_s_one_read_names_the_direction_of_each_connection():
    """What its strategy is given: the connections as the WMS read them, each with its source,
    its target and its direction, as a module's are (jms_kapps 52)."""
    store = CountingStore()
    store.graph += shipped_topology("tee").graph(0)

    read = modules_and_connections(store)

    m1, m2, m3, m4 = (module_iri(n) for n in range(1, 5))
    assert store.queries == 1
    assert sorted(read.directed()) == [
        DirectedConnection(m1, m2, "South"),
        DirectedConnection(m2, m3, "East"),
        DirectedConnection(m2, m4, "South"),
    ]
