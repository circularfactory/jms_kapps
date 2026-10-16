"""The monitor's graph-only mode (jms_kapps 24): a switch between "graph + streams" and "graph
only", the interval of its background graph read, and the queries it sends.

In "graph only" the monitor sees what a real dashboard that sees only the knowledge graph sees: it
follows no participant's handover event stream, and so it shows no refusal. Every other view comes
from its reads of the graph in both modes. The reads and the streams are the fakes of
`_monitor_fakes.py`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from _monitor_fakes import STREAM_1, STREAM_WMS, FakeReads, FakeStreams, refuse
from _wait import eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI
from rdflib import URIRef

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import GRAPH_AND_STREAMS, GRAPH_ONLY, MonitorMemory
from jms_kapps.uc2.monitor.reads import GraphReads
from jms_kapps.uc2.vocabulary import FC, WMS, module_iri

M1 = module_iri(1)


@pytest.fixture
def reads() -> FakeReads:
    return FakeReads()


@pytest.fixture
def streams() -> FakeStreams:
    return FakeStreams()


@pytest.fixture
def memory(reads, streams) -> Iterator[MonitorMemory]:
    memory = MonitorMemory(reads, read_stream=streams)
    yield memory
    memory.stop()


def followers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("monitor-follow-")]


# -- The switch ------------------------------------------------------------------------------- #


def test_graph_only_lets_go_of_every_stream_and_graph_and_streams_follows_them_again(
    memory,
):
    memory.read_graph()
    memory.discover()
    assert memory.following() == {M1: STREAM_1, WMS: STREAM_WMS}

    memory.set_mode(GRAPH_ONLY)

    assert memory.mode == GRAPH_ONLY
    assert memory.following() == {}
    eventually(lambda: not followers(), timeout=5)

    memory.set_mode(GRAPH_AND_STREAMS)
    memory.discover()

    assert memory.following() == {M1: STREAM_1, WMS: STREAM_WMS}


def test_in_graph_only_a_discovery_sends_no_query_and_follows_nothing(reads, memory):
    """Where the streams are is in the graph, but in "graph only" the monitor has no use for it."""
    memory.read_graph()
    memory.set_mode(GRAPH_ONLY)

    memory.discover()

    assert "streams" not in reads.made
    assert memory.following() == {}


def test_a_switch_to_graph_only_while_the_streams_are_read_follows_none_of_them(
    reads, memory
):
    memory.read_graph()
    reading, switched = threading.Event(), threading.Event()
    located = reads.streams

    def slow_streams() -> dict:
        reading.set()
        switched.wait(5)
        return located()

    reads.streams = slow_streams  # type: ignore[method-assign]
    discovery = threading.Thread(target=memory.discover)
    discovery.start()
    reading.wait(5)

    memory.set_mode(GRAPH_ONLY)
    switched.set()
    discovery.join(5)

    assert memory.following() == {}
    assert not followers()


def test_an_event_a_follower_reads_after_the_switch_to_graph_only_is_not_kept(reads):
    """A follower may be in the middle of a read when the switch comes: what it reads then is
    dropped, so no refusal arrives in "graph only"."""
    events = HandoverEvents(M1, token="m1")
    refuse(events)
    reading, switched = threading.Event(), threading.Event()

    def read_stream(url: str, last: str | None) -> Iterator[tuple[str, str]]:
        reading.set()
        switched.wait(5)
        for event in events.after(last):
            yield event.id, event.model_dump_json()

    memory = MonitorMemory(reads, read_stream=read_stream)
    try:
        memory.read_graph()
        memory.discover()
        reading.wait(5)

        memory.set_mode(GRAPH_ONLY)
        switched.set()
        eventually(lambda: not followers(), timeout=5)
        memory.set_mode(GRAPH_AND_STREAMS)

        assert memory.snapshot().refusals == ()
    finally:
        memory.stop()


def test_graph_only_shows_no_refusal_and_graph_and_streams_shows_them_again(
    streams, memory
):
    """A refused write leaves no trace in the graph, so a dashboard that sees only the graph sees
    no refusal. Those the monitor kept come back with the streams."""
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1], 2)
    eventually(lambda: len(memory.snapshot().refusals) == 2)

    memory.set_mode(GRAPH_ONLY)

    assert memory.snapshot().refusals == ()

    memory.set_mode(GRAPH_AND_STREAMS)

    assert [kept.event.id for kept in memory.snapshot().refusals] == ["m1:2", "m1:1"]


def test_graph_and_streams_reads_on_after_the_last_event_read_before_graph_only(
    streams, memory
):
    """What the participants refused meanwhile arrives when the streams are followed again: a
    participant keeps its newest events."""
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1])
    eventually(lambda: len(memory.snapshot().refusals) == 1)
    memory.set_mode(GRAPH_ONLY)
    eventually(lambda: not followers(), timeout=5)
    refuse(streams.events[STREAM_1])

    memory.set_mode(GRAPH_AND_STREAMS)
    memory.discover()

    eventually(lambda: len(memory.snapshot().refusals) == 2)
    assert (STREAM_1, "m1:1") in streams.reads


def test_the_running_monitor_switches_while_its_reads_are_under_way(reads, streams):
    """The background task reads the graph in both modes; it lets go of the streams in "graph
    only", and follows them again at once in "graph + streams", not at its next discovery."""
    memory = MonitorMemory(
        reads, read_stream=streams, graph_seconds=0.02, discover_seconds=60
    )
    memory.start()
    try:
        eventually(lambda: memory.following() == {M1: STREAM_1, WMS: STREAM_WMS})

        memory.set_mode(GRAPH_ONLY)
        eventually(lambda: not followers(), timeout=5)
        stream_reads, made = len(streams.reads), len(reads.made)
        eventually(lambda: reads.made[made:].count("boxes") >= 5)

        assert len(streams.reads) == stream_reads
        assert "streams" not in reads.made[made:]

        memory.set_mode(GRAPH_AND_STREAMS)

        eventually(
            lambda: memory.following() == {M1: STREAM_1, WMS: STREAM_WMS}, timeout=2
        )
        eventually(lambda: len(streams.reads) > stream_reads, timeout=2)
    finally:
        memory.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("monitor-")]


def test_the_streams_are_found_at_their_own_interval_whatever_the_graphs(
    reads, streams
):
    """The query for the streams goes as often as "Queries sent" says, every 5 s, also where the
    graph is read less often. (Added on 2026-10-10, jms_kapps 24: the review found that a
    discovery waited for the next read of the graph, so at a graph read of 10 s the streams were
    found every 10 s, while the page said every 5 s.)"""
    memory = MonitorMemory(
        reads, read_stream=streams, graph_seconds=10, discover_seconds=0.2
    )
    memory.start()
    try:
        eventually(lambda: reads.made.count("streams") >= 4, timeout=2)

        assert reads.made.count("boxes") == 1
    finally:
        memory.stop()


# -- The interval of the graph read ----------------------------------------------------------- #


def test_the_graph_is_read_every_second_until_a_page_sets_another_interval(memory):
    assert memory.graph_seconds == 1.0


def test_a_new_interval_applies_at_once_while_the_monitor_runs(reads, streams):
    """From 10 s to 0.5 s: the monitor does not wait out the 10 s it was waiting."""
    memory = MonitorMemory(reads, read_stream=streams, graph_seconds=10)
    memory.start()
    try:
        eventually(lambda: reads.made.count("boxes") == 1)
        time.sleep(0.3)
        assert reads.made.count("boxes") == 1

        memory.set_graph_seconds(0.5)

        assert memory.graph_seconds == 0.5
        # At once, then every half second.
        eventually(lambda: reads.made.count("boxes") >= 4, timeout=2.5)
        reached = reads.made.count("boxes")
        time.sleep(1.2)
        assert 1 <= reads.made.count("boxes") - reached <= 3
    finally:
        memory.stop()


@pytest.mark.parametrize("seconds", [0.4, 10.5, 0, -1, float("nan"), float("inf")])
def test_an_interval_outside_half_a_second_to_ten_seconds_is_refused(memory, seconds):
    with pytest.raises(ValueError, match=r"0\.5 s to 10 s"):
        memory.set_graph_seconds(seconds)

    assert memory.graph_seconds == 1.0


@pytest.mark.parametrize("seconds", [0.5, 2, 10])
def test_an_interval_from_half_a_second_to_ten_seconds_is_taken(memory, seconds):
    memory.set_graph_seconds(seconds)

    assert memory.graph_seconds == seconds


# -- The queries sent ------------------------------------------------------------------------- #


class FakeClient:
    """A GraphDB client that keeps every query it receives, and answers each with the rows
    `answer` gives for its text, or raises `fails`."""

    def __init__(self, answer: Callable[[str], list[dict]] = lambda text: []) -> None:
        self.answer = answer
        self.received: list[str] = []
        self.fails: Exception | None = None

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        self.received.append(query)
        if self.fails is not None:
            raise self.fails
        return {"results": {"bindings": self.answer(query)}}


def test_each_read_keeps_the_exact_text_it_sent_and_how_many_rows_came_back():
    client = FakeClient(
        lambda text: (
            [{"wms": URIRef(WMS)}] if str(FC.WarehouseManagementSystem) in text else []
        )
    )
    reads = GraphReads(client)  # type: ignore[arg-type]

    reads.modules()
    reads.live_resources()
    reads.boxes()
    reads.wms()
    reads.streams()

    sent = reads.sent()
    assert [query.read for query in sent] == [
        "modules",
        "live_resources",
        "boxes",
        "wms",
        "streams",
    ]
    assert [query.text for query in sent] == client.received
    assert [query.rows for query in sent] == [0, 0, 0, 1, 0]
    assert all(query.duration >= 0 and query.error is None for query in sent)


def test_a_read_sent_again_keeps_the_text_it_sent_last():
    """The query for the live participants names the oldest heartbeat that counts, so its text
    changes from read to read."""
    client = FakeClient()
    reads = GraphReads(client)  # type: ignore[arg-type]

    reads.live_resources()
    time.sleep(0.01)
    reads.live_resources()

    (sent,) = reads.sent()
    assert client.received[0] != client.received[1]
    assert sent.text == client.received[1]


def test_a_query_that_fails_keeps_its_text_and_says_why():
    client = FakeClient()
    client.fails = ConnectionError("the store is down")
    reads = GraphReads(client)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError):
        reads.boxes()

    (sent,) = reads.sent()
    assert (sent.read, sent.text, sent.rows) == ("boxes", client.received[0], None)
    assert sent.error == "the store is down"


def test_the_wms_is_whatever_the_graph_types_as_a_warehouse_management_system():
    elsewhere = IRI("Warehouse", base="http://example.org/plant#")
    client = FakeClient(lambda text: [{"wms": URIRef(WMS)}, {"wms": URIRef(elsewhere)}])
    reads = GraphReads(client)  # type: ignore[arg-type]

    assert reads.wms() == {WMS, elsewhere}
    assert f"<{FC.WarehouseManagementSystem}>" in client.received[0]


# -- The page's surface ----------------------------------------------------------------------- #


@pytest.fixture
def client(memory) -> TestClient:
    app = FastAPI()
    mount_onto(app, memory=memory)
    return TestClient(app)


def test_the_state_says_how_the_monitor_reads(client, memory):
    memory.read_graph()

    assert client.get("/api/state").json()["reading"] == {
        "mode": "graph + streams",
        "modes": ["graph + streams", "graph only"],
        "graph_seconds": 1.0,
        "shortest_graph_seconds": 0.5,
        "longest_graph_seconds": 10.0,
    }


def test_the_switch_stops_the_streams_and_switching_back_resumes_them(
    client, streams, memory
):
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1])
    eventually(lambda: client.get("/api/state").json()["refusals"])

    answer = client.put("/api/mode", json={"mode": "graph only"})

    assert answer.status_code == 200
    assert answer.json()["mode"] == "graph only"
    assert memory.following() == {}
    state = client.get("/api/state").json()
    assert (state["reading"]["mode"], state["refusals"]) == ("graph only", [])

    client.put("/api/mode", json={"mode": "graph + streams"})
    memory.discover()

    assert memory.following() == {M1: STREAM_1, WMS: STREAM_WMS}
    assert len(client.get("/api/state").json()["refusals"]) == 1


def test_a_mode_that_is_neither_is_refused_with_a_reason(client, memory):
    answer = client.put("/api/mode", json={"mode": "streams only"})

    assert answer.status_code == 422
    assert "graph only" in answer.text
    assert memory.mode == GRAPH_AND_STREAMS


def test_the_interval_a_page_sets_applies(client, memory):
    answer = client.put("/api/graph-read-interval", json={"seconds": 2})

    assert answer.status_code == 200
    assert answer.json()["graph_seconds"] == 2.0
    assert memory.graph_seconds == 2.0


@pytest.mark.parametrize("seconds", [0.1, 11, "soon"])
def test_an_interval_out_of_range_is_refused_with_a_reason(client, memory, seconds):
    answer = client.put("/api/graph-read-interval", json={"seconds": seconds})

    assert answer.status_code == 422
    assert memory.graph_seconds == 1.0


def test_an_interval_out_of_range_says_which_are_allowed(client):
    answer = client.put("/api/graph-read-interval", json={"seconds": 11})

    assert "0.5 s to 10 s" in answer.json()["detail"]


def test_the_state_shows_each_query_sent_with_its_text_interval_duration_and_rows(
    client, memory
):
    memory.read_graph()
    memory.discover()

    queries = client.get("/api/state").json()["queries"]

    assert [
        (q["read"], q["text"], q["every_seconds"], q["rows"], q["error"])
        for q in queries
    ] == [
        ("modules", "SELECT ... # modules", 1.0, 5, None),
        ("live_resources", "SELECT ... # live_resources", 1.0, 3, None),
        ("boxes", "SELECT ... # boxes", 1.0, 2, None),
        ("wms", "SELECT ... # wms", 1.0, 1, None),
        ("streams", "SELECT ... # streams", 5.0, 2, None),
    ]
    assert all(q["duration_seconds"] == 0.001 and q["sent"] for q in queries)


def test_in_graph_only_the_streams_query_is_not_sent_and_the_others_follow_the_interval(
    client, memory
):
    memory.read_graph()
    memory.discover()
    client.put("/api/graph-read-interval", json={"seconds": 0.5})

    client.put("/api/mode", json={"mode": "graph only"})

    every = {
        q["read"]: q["every_seconds"]
        for q in client.get("/api/state").json()["queries"]
    }
    assert every == {
        "modules": 0.5,
        "live_resources": 0.5,
        "boxes": 0.5,
        "wms": 0.5,
        "streams": None,
    }


def test_a_page_poll_sends_no_query(client, reads, memory):
    memory.read_graph()
    memory.discover()
    made = len(reads.made)

    for _ in range(5):
        client.get("/api/state")

    assert len(reads.made) == made
