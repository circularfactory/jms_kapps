"""The Launcher's list of the boxes on the station, through its JSON API.

The list comes from one SPARQL query, which the Launcher sends in its background read, and the
state route answers it with the query's text exactly as the store received it. The store is the
stand-in of `_station_boxes.py`, which answers fixed rows and keeps every query text it receives,
so nothing reaches a real store. One test runs the query on rdflib's SPARQL engine instead, over a
small graph in memory. The page's own tests are in `test_uc2_launcher_boxes_page.py`, and the query
on a seeded GraphDB dataset in `test_uc2_launcher_boxes_live.py`.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

import pytest
from _station_boxes import LISTED, StandInStore, listing
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.boxes import BoxOnStation, StationBoxes
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, box_iri, module_iri, uuid_box_iri


@pytest.fixture
def store() -> StandInStore:
    return StandInStore()


def listed(client: TestClient) -> dict:
    return client.get("/api/state").json()["boxes"]


def test_the_state_lists_each_box_with_the_module_that_possesses_it_and_the_query_as_sent(
    store,
):
    """Each row the query answers, by local names, as a person reads them; and the text of the
    query, exactly as the store received it."""
    with TestClient(create_app(listing(store))) as client:
        eventually(lambda: listed(client)["read_at"] is not None)

        boxes = listed(client)

    assert boxes["boxes"] == LISTED
    assert store.received != []
    assert boxes["query"] == store.received[-1]
    assert boxes["error"] is None


def test_a_page_poll_sends_no_query(store):
    """The query goes in the Launcher's background read, so a page that polls adds no store
    request: twenty polls send none."""
    with TestClient(create_app(listing(store))) as client:
        eventually(lambda: listed(client)["read_at"] is not None)
        sent = len(store.received)

        polls = [listed(client) for _ in range(20)]

        assert len(store.received) == sent
    assert polls[-1]["boxes"] == LISTED


def test_the_query_goes_on_being_sent_while_no_page_polls(store):
    """The background read runs whether a page is open or not, and sends the same text each
    time."""
    with TestClient(create_app(listing(store, interval=0.05))):
        eventually(lambda: len(store.received) >= 3)

    assert len(set(store.received)) == 1


def test_a_read_that_fails_keeps_the_boxes_the_last_one_read_and_says_why(store):
    with TestClient(create_app(listing(store, interval=0.05))) as client:
        eventually(lambda: listed(client)["read_at"] is not None)
        read_at = listed(client)["read_at"]

        store.away = "the store is away"

        eventually(lambda: listed(client)["error"] == "the store is away")
        boxes = listed(client)

    assert boxes["boxes"] == LISTED
    assert boxes["read_at"] == read_at
    assert boxes["query"] == store.received[-1]


def test_once_the_app_stops_it_sends_no_query(store):
    """The Launcher reads only while its app serves: from its stop on, nothing reaches the
    store."""
    with TestClient(create_app(listing(store, interval=0.05))):
        eventually(lambda: len(store.received) >= 2)

    # A read under way when the app stopped may still arrive.
    time.sleep(0.2)
    sent = len(store.received)
    time.sleep(0.3)

    assert len(store.received) == sent


class InMemoryStore:
    """A store on rdflib's own SPARQL engine, which infers nothing: it answers a query over
    `graph` as SPARQL's JSON results."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    def query(self, query: str, *, convert_bindings: bool = False) -> Any:
        answer = self.graph.query(query).serialize(format="json")
        assert answer is not None
        return json.loads(answer)


def possessed(graph: Graph, box: IRI, possessor: IRI) -> None:
    """`box`, possessed by `possessor`, through the possessor's one possession state."""
    state = URIRef(f"{possessor}_possession")
    graph.add((URIRef(box), RDF.type, URIRef(FC.Box)))
    graph.add((URIRef(possessor), URIRef(CFC.hasPossessor), state))
    graph.add((URIRef(box), URIRef(CFC.hasPossessedWorkpiece), state))


def test_a_box_named_by_a_uuid_is_listed_by_its_whole_name_in_the_order_of_the_names():
    """The WMS names each box it spawns `Box-<UUID>`: the list names such a box
    whole, and orders the boxes by their IRIs, as the query's `ORDER BY ?box` does, beside a box a
    seed named by a number. No number is read from a name."""
    graph = Graph()
    graph.add((URIRef(WMS), RDF.type, URIRef(FC.WarehouseManagementSystem)))
    spawned = uuid_box_iri(UUID("f3a1c2d4-0b5e-4c6f-8a7b-9c0d1e2f3a4b"))
    for box, possessor in [(box_iri(1), module_iri(1)), (spawned, module_iri(2))]:
        possessed(graph, box, possessor)
    boxes = StationBoxes(InMemoryStore(graph))

    boxes.read()

    assert [found.box for found in boxes.last().boxes] == [
        "Box-f3a1c2d4-0b5e-4c6f-8a7b-9c0d1e2f3a4b",
        "Box1",
    ]


def test_the_query_runs_unchanged_on_another_sparql_engine_and_lists_only_the_boxes_on_modules():
    """The query is standard SPARQL: rdflib's engine, which is not GraphDB's and infers nothing,
    runs it as it is. Box 1 on Module 1 with its transport running, Box 2 back in the WMS's
    possession, and Box 3 on Module 3 with no transport: the list holds Box 1 and Box 3."""
    graph = Graph()
    graph.add((URIRef(WMS), RDF.type, URIRef(FC.WarehouseManagementSystem)))
    for box, possessor in [
        (box_iri(1), module_iri(1)),
        (box_iri(2), WMS),
        (box_iri(3), module_iri(3)),
    ]:
        possessed(graph, box, possessor)
    for box, transport, status in [
        (box_iri(1), "Transport1", OperationStatus.RUNNING),
        (box_iri(2), "Transport2", OperationStatus.DONE),
    ]:
        graph.add((URIRef(box), URIRef(FC.hasTransport), URIRef(FCI_NS + transport)))
        graph.add(
            (URIRef(FCI_NS + transport), URIRef(SVC.hasOperationStatus), Literal(status))
        )
    boxes = StationBoxes(InMemoryStore(graph))

    boxes.read()

    found = boxes.last()
    assert found.error is None
    assert found.boxes == [
        BoxOnStation(
            box="Box1", module="Module1", transport="Transport1", status="running"
        ),
        BoxOnStation(box="Box3", module="Module3", transport=None, status=None),
    ]
