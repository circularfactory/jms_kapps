"""The monitor's "possible deadlock" notice.

A module that holds a box which has waited past the deadlock alarm time emits one
`deadlock_alarm` event on its handover event stream, with the modules it asks. In "graph + streams"
the monitor keeps each alarm whose box the graph still shows on that module, and shows them as one
notice that names every module involved: each module with an alarm and the modules it asks. In
"graph only" it follows no stream, so it shows none, and the page says why
(`test_uc2_monitor_deadlock_page.py`). The reads and the streams are the fakes of
`_monitor_fakes.py`, with Box 1 on Module 1, Box 2 on Module 2 and Box 3 on Module 3.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _monitor_fakes import STREAM_1, STREAM_WMS, FakeReads, FakeStreams, refuse
from _wait import eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import GRAPH_AND_STREAMS, GRAPH_ONLY, MonitorMemory
from jms_kapps.uc2.monitor.reads import BoxRow
from jms_kapps.uc2.vocabulary import WMS, box_iri, module_iri, transport_iri

M1, M2, M3, M4, M5 = (module_iri(n) for n in range(1, 6))
B1, B2, B3 = box_iri(1), box_iri(2), box_iri(3)
STREAM_2 = "http://127.0.0.1:2/handover-events"
STREAM_3 = "http://127.0.0.1:3/handover-events"


class CycleReads(FakeReads):
    """The cross, with Box 1 on Module 1, Box 2 on Module 2 and Box 3 on Module 3, each bound for
    Module 5, and Modules 1, 2 and 3 live, each with its stream. `held` says where each box is."""

    def __init__(self) -> None:
        super().__init__()
        self.held: dict[IRI, IRI] = {B1: M1, B2: M2, B3: M3}
        self.live = {M1, M2, M3, WMS}
        self.located = {M1: STREAM_1, M2: STREAM_2, M3: STREAM_3, WMS: STREAM_WMS}

    def boxes(self) -> list[BoxRow]:
        rows = [
            BoxRow(box, OperationStatus.RUNNING, holder, M5, holder)
            for box, holder in self.held.items()
        ]
        self._keep("boxes", len(rows))
        return rows


@pytest.fixture
def reads() -> CycleReads:
    return CycleReads()


@pytest.fixture
def streams() -> FakeStreams:
    streams = FakeStreams()
    streams.events[STREAM_2] = HandoverEvents(M2, token="m2")
    streams.events[STREAM_3] = HandoverEvents(M3, token="m3")
    return streams


@pytest.fixture
def memory(reads, streams) -> Iterator[MonitorMemory]:
    memory = MonitorMemory(reads, read_stream=streams)
    memory.read_graph()
    memory.discover()
    yield memory
    memory.stop()


@pytest.fixture
def client(memory) -> TestClient:
    app = FastAPI()
    mount_onto(app, memory=memory)
    return TestClient(app)


def alarm(streams: FakeStreams, url: str, box: IRI, *asked: IRI) -> None:
    """The module of the stream at `url` raises the alarm for `box`, which asks `asked`."""
    events = streams.events[url]
    names = " and ".join(module.fragment for module in asked)
    events.deadlock_alarm(
        box=box,
        transport=transport_iri(box),
        asked=list(asked),
        reason=f"{events.resource.fragment} has waited 301 s to hand {box.fragment} on, "
        f"asking {names}",
    )


def notice(client: TestClient) -> dict | None:
    return client.get("/api/state").json()["possible_deadlock"]


def alarms_shown(client: TestClient) -> int:
    shown = notice(client)
    return 0 if shown is None else len(shown["alarms"])


def test_no_alarm_shows_no_notice(client):
    assert notice(client) is None


def test_three_alarms_on_a_cycle_show_as_one_notice_that_names_every_module(
    client, streams
):
    """Module 1 waits for Module 2, Module 2 for Module 3, and Module 3 for Module 1."""
    alarm(streams, STREAM_1, B1, M2)
    alarm(streams, STREAM_2, B2, M3)
    alarm(streams, STREAM_3, B3, M1)

    eventually(lambda: alarms_shown(client) == 3)

    shown = notice(client)
    assert shown["modules"] == ["Module1", "Module2", "Module3"]
    assert [(a["module"], a["box"], a["asked"]) for a in shown["alarms"]] == [
        ("Module1", "Box1", ["Module2"]),
        ("Module2", "Box2", ["Module3"]),
        ("Module3", "Box3", ["Module1"]),
    ]
    assert shown["alarms"][0]["reason"] == (
        "Module1 has waited 301 s to hand Box1 on, asking Module2"
    )


def test_the_notice_names_the_modules_asked_beside_those_with_an_alarm(client, streams):
    alarm(streams, STREAM_1, B1, M2, M4)

    eventually(lambda: alarms_shown(client) == 1)

    assert notice(client)["modules"] == ["Module1", "Module2", "Module4"]


def test_an_alarm_whose_box_moved_is_dropped(client, reads, streams, memory):
    alarm(streams, STREAM_1, B1, M2)
    alarm(streams, STREAM_2, B2, M3)
    eventually(lambda: alarms_shown(client) == 2)

    reads.held[B1] = M4
    memory.read_graph()

    assert [a["module"] for a in notice(client)["alarms"]] == ["Module2"]
    # Dropped for good: the box back on Module 1 brings the alarm of its wait before back no more.
    reads.held[B1] = M1
    memory.read_graph()
    assert [a["module"] for a in notice(client)["alarms"]] == ["Module2"]


def test_graph_only_shows_no_notice_and_graph_and_streams_shows_it_again(
    client, streams, memory
):
    alarm(streams, STREAM_1, B1, M2)
    eventually(lambda: alarms_shown(client) == 1)

    memory.set_mode(GRAPH_ONLY)

    assert notice(client) is None
    assert memory.snapshot().alarms == ()

    memory.set_mode(GRAPH_AND_STREAMS)

    assert [a["module"] for a in notice(client)["alarms"]] == ["Module1"]


def test_an_alarm_is_no_refusal_and_a_refusal_is_no_alarm(client, streams):
    alarm(streams, STREAM_1, B1, M2)
    refuse(streams.events[STREAM_1])

    eventually(lambda: client.get("/api/state").json()["refusals"] != [])

    state = client.get("/api/state").json()
    assert [r["box"] for r in state["refusals"]] == ["Box1"]
    assert len(state["possible_deadlock"]["alarms"]) == 1
