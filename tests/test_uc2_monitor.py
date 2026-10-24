"""The monitor's backend: what it reads in the background, what its JSON API shows, and that it
only reads.

The reads and the handover event streams are the fakes of `_monitor_fakes.py`. The monitor's
routes are grafted onto an app that already has a root route, as a middleware's app has.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from _html import absolute_paths
from _monitor_fakes import (
    M1,
    M2,
    M3,
    STREAM_1,
    STREAM_WMS,
    FakeReads,
    FakeStreams,
    refuse,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

import jms_kapps.uc2.monitor
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import KEPT_HANDOVERS, KEPT_REFUSALS, MonitorMemory
from jms_kapps.uc2.monitor.reads import BoxRow, ConnectionRow, ModuleRow
from jms_kapps.uc2.vocabulary import (
    FC,
    FCI_NS,
    WMS,
    box_iri,
    module_iri,
    uuid_box_iri,
)


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


@pytest.fixture
def app(memory) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def welcome() -> str:
        return "Welcome to semantic middleware!"

    mount_onto(app, memory=memory)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def eventually(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "not within the timeout"
        time.sleep(0.01)


# -- The page and its view of the graph ------------------------------------------------------- #


def test_the_page_replaces_the_middleware_root_and_loads_its_own_script(client):
    page = client.get("/")

    assert page.headers["content-type"].startswith("text/html")
    assert 'src="static/monitor.js"' in page.text
    assert client.get("/static/monitor.js").status_code == 200


@pytest.mark.parametrize("name", ["index.html", "monitor.js", "style.css"])
def test_the_page_asks_for_everything_by_a_relative_path(name):
    """So that it works at the monitor's own address and through the Launcher, under
    `/p/<id>/`."""
    static = Path(jms_kapps.uc2.monitor.__file__).parent / "static"
    assert absolute_paths((static / name).read_text(encoding="utf-8")) == []


def test_every_module_is_placed_and_shown_live_or_offline(client, memory):
    """The shipped cross names Modules 1 and 2 entry modules and Modules 4 and 5 exit modules."""
    memory.read_graph()

    modules = client.get("/api/state").json()["modules"]

    assert [
        (m["id"], m["label"], m["type"], m["place"], m["live"]) for m in modules
    ] == [
        (str(module_iri(1)), "Module 1", "EntryModule", [1, 0], True),
        (str(module_iri(2)), "Module 2", "EntryModule", [0, 1], False),
        (str(module_iri(3)), "Module 3", "FlexConveyorModule", [1, 1], True),
        (str(module_iri(4)), "Module 4", "ExitModule", [2, 1], False),
        (str(module_iri(5)), "Module 5", "ExitModule", [1, 2], False),
    ]
    assert modules[2]["connections"][0] == {
        "direction": "North",
        "to": str(module_iri(1)),
        "to_label": "Module 1",
        "port": 0,
    }


def test_each_wire_says_where_its_connections_lead(client, memory):
    """On the cross every connection runs both ways. A wire says nothing of the way boxes go:
    the monitor computes no route."""
    memory.read_graph()

    wires = client.get("/api/state").json()["wires"]

    m3, m4, m5 = str(M3), str(module_iri(4)), str(module_iri(5))
    assert wires == [
        {"between": [str(M1), m3], "leads_to": [str(M1), m3]},
        {"between": [str(M2), m3], "leads_to": [str(M2), m3]},
        {"between": [m3, m4], "leads_to": [m3, m4]},
        {"between": [m3, m5], "leads_to": [m3, m5]},
    ]


def test_a_connection_leads_one_way_and_one_to_no_module_is_no_wire(
    client, reads, memory
):
    """Module 3, beyond the exit, is a dead end. A connection to something that is no module is
    no wire."""
    elsewhere = IRI("Nowhere", base=FCI_NS)
    reads.rows = [
        ModuleRow(M1, "Module 1", "EntryModule", (ConnectionRow("East", M2, 0),)),
        ModuleRow(
            M2,
            "Module 2",
            "ExitModule",
            (ConnectionRow("East", M3, 0), ConnectionRow("South", elsewhere, 0)),
        ),
        ModuleRow(M3, "Module 3", "FlexConveyorModule", ()),
    ]
    memory.read_graph()

    wires = client.get("/api/state").json()["wires"]

    assert wires == [
        {"between": [str(M1), str(M2)], "leads_to": [str(M2)]},
        {"between": [str(M2), str(M3)], "leads_to": [str(M3)]},
    ]


def test_a_box_of_a_running_transport_is_on_its_module_and_one_of_a_done_transport_is_not(
    client, memory
):
    memory.read_graph()

    state = client.get("/api/state").json()

    assert {m["label"]: m["boxes"] for m in state["modules"]}["Module 1"] == ["Box1"]
    assert sum(len(m["boxes"]) for m in state["modules"]) == 1
    assert state["boxes"] == [
        {
            "id": "Box1",
            "status": "running",
            "origin": "Module1",
            "destination": "Module5",
            "holder": "Module1",
            # Its destination by the module's id, where the drawing marks it. The monitor
            # computes no path to it.
            "destination_id": str(module_iri(5)),
        },
        {
            "id": "Box2",
            "status": "done",
            "origin": "Module1",
            "destination": "Module4",
            "holder": "WarehouseManagementSystem1",
            "destination_id": str(module_iri(4)),
        },
    ]


def test_the_boxes_are_listed_by_their_name(client, reads, memory):
    """The interim reading: a box is named by a
    UUID, which has no order a person counts in, so the Boxes table orders the boxes by name, as
    text sorts them, a box named by a number among them."""
    elsewhere = IRI("Crate", base="http://example.org/plant#")
    late = uuid_box_iri(UUID("f3a1c2d4-0b5e-4c6f-8a7b-9c0d1e2f3a4b"))
    early = uuid_box_iri(UUID("0a9f77d2-6e41-4b13-b8c5-d2e07a31f9b4"))
    reads.boxes = lambda: [  # type: ignore[method-assign]
        BoxRow(box, OperationStatus.DONE, M1, module_iri(4), WMS)
        for box in (box_iri(10), late, elsewhere, box_iri(2), early)
    ]
    memory.read_graph()

    boxes = client.get("/api/state").json()["boxes"]

    assert [box["id"] for box in boxes] == [
        "Box-0a9f77d2-6e41-4b13-b8c5-d2e07a31f9b4",
        "Box-f3a1c2d4-0b5e-4c6f-8a7b-9c0d1e2f3a4b",
        "Box10",
        "Box2",
        "Crate",
    ]


def test_a_module_outside_the_instances_namespace_is_compared_by_its_whole_iri(
    client, reads, memory
):
    """No local name is turned back into an IRI: a module in another namespace is live, and holds
    its box, by its own IRI."""
    elsewhere = IRI("Module1", base="http://example.org/plant#")
    reads.rows = [ModuleRow(elsewhere, "Elsewhere", "FlexConveyorModule", ())]
    reads.live = {elsewhere}
    reads.boxes = lambda: [
        BoxRow(box_iri(7), OperationStatus.RUNNING, None, None, elsewhere)
    ]  # type: ignore[method-assign]
    memory.read_graph()

    (module,) = client.get("/api/state").json()["modules"]

    assert (module["id"], module["live"], module["boxes"]) == (
        str(elsewhere),
        True,
        ["Box7"],
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            ModuleRow(
                M1,
                "Module 1",
                "EntryModule",
                (ConnectionRow("South", IRI("Nowhere", base=FCI_NS), 0),),
            )
        ],
        [ModuleRow(M1, None, "FlexConveyorModule", ())],
    ],
    ids=[
        "no module yet",
        "a connection to no module",
        "a module without a label",
    ],
)
def test_a_graph_that_is_only_partly_populated_shows_what_is_there(
    client, reads, memory, rows
):
    reads.rows = rows
    memory.read_graph()

    response = client.get("/api/state")

    assert response.status_code == 200
    modules = response.json()["modules"]
    assert [m["id"] for m in modules] == [str(row.resource) for row in rows]
    assert all(m["label"] for m in modules)


def test_a_graph_read_that_fails_keeps_what_was_read_and_says_why(
    client, reads, memory
):
    memory.read_graph()

    def failing() -> list[ModuleRow]:
        raise ConnectionError("the store is down")

    reads.modules = failing  # type: ignore[method-assign]
    memory.read_graph()
    state = client.get("/api/state").json()

    assert len(state["modules"]) == 5
    assert "the store is down" in state["error"]


# -- The refusals, from the participants' handover event streams ----------------------------- #


def test_the_streams_of_the_live_participants_are_followed(reads, streams, memory):
    reads.located[M2] = "http://127.0.0.1:2/handover-events"  # Module 2 is offline.
    memory.read_graph()

    memory.discover()

    assert memory.following() == {M1: STREAM_1, WMS: STREAM_WMS}


def test_a_participant_that_goes_offline_is_no_longer_followed(reads, memory):
    memory.read_graph()
    memory.discover()
    reads.live = {WMS}
    memory.read_graph()

    memory.discover()

    assert memory.following() == {WMS: STREAM_WMS}


def test_a_refusal_on_a_stream_appears_in_the_refusals_with_its_report(
    client, streams, memory
):
    memory.read_graph()
    memory.discover()
    report = "_:r <http://www.w3.org/ns/shacl#conforms> false ."

    refuse(streams.events[STREAM_1], report=report)

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["giver"] == "Module1"
    assert shown["receiver"] == "Module2"
    assert shown["box"] == "Box1"
    assert shown["report"] == report


def test_the_refusals_name_every_rule_of_the_report(client, streams, memory):
    report = (
        "_:r <http://www.w3.org/ns/shacl#result> _:a .\n"
        f"_:a <http://www.w3.org/ns/shacl#focusNode> <{box_iri(1)}> .\n"
        f"_:a <http://www.w3.org/ns/shacl#sourceShape> <{FC.DoneTransportBoxReleasedShape}> .\n"
        "_:r <http://www.w3.org/ns/shacl#result> _:b .\n"
        f"_:b <http://www.w3.org/ns/shacl#focusNode> <{box_iri(1)}> .\n"
        f"_:b <http://www.w3.org/ns/shacl#sourceShape> <{FC.BoxOnModuleHasTransportShape}> .\n"
    )
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report=report)

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["rules"] == [
        "BoxOnModuleHasTransportShape at Box1",
        "DoneTransportBoxReleasedShape at Box1",
    ]


def test_a_refusal_carries_the_results_its_participant_emitted(client, streams, memory):
    """Each rule as the event names it, with its path and every IRI whole, so that a reader of
    the monitor compares what the participant sent."""
    report = (
        "_:r <http://www.w3.org/ns/shacl#result> _:a .\n"
        f"_:a <http://www.w3.org/ns/shacl#focusNode> <{M2}> .\n"
        f"_:a <http://www.w3.org/ns/shacl#resultPath> <{FC.hasConnection}> .\n"
        f"_:a <http://www.w3.org/ns/shacl#sourceShape> <{FC.FlexConveyorModulePossessionShape}> .\n"
    )
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report=report)

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["results"] == [
        {
            "shape": str(FC.FlexConveyorModulePossessionShape),
            "focus_node": str(M2),
            "path": f"<{FC.hasConnection}>",
        }
    ]


# A report as GraphDB sent it when Module 1, with `skip-reserve` armed, handed Box2 to Module 3
# while Module 3 held another box.
SKIP_RESERVE_REPORT = (
    Path(__file__).parent / "reports" / "skip-reserve.nq"
).read_text()


def test_a_refusal_shows_each_result_of_its_report_for_a_person_to_read(
    client, streams, memory
):
    """The message first, and every term short, by its local name, with the whole IRI beside it
    for a tooltip, as the middleware's reader gives them."""
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report=SKIP_RESERVE_REPORT)

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["reason"] == "the triple store refused the handover"
    assert shown["report_note"] is None
    assert shown["readable_results"] == [
        {
            "message": "A FlexConveyorModule may possess at most one Box at a time.",
            "severity": {
                "short": "Violation",
                "full": "http://www.w3.org/ns/shacl#Violation",
            },
            "focus": {"short": "Module3", "full": str(M3)},
            "path": {
                "short": "(hasPossessor/^hasPossessedWorkpiece)",
                "full": "(<http://w3id.org/circularfactory/Core#hasPossessor>"
                "/^<http://w3id.org/circularfactory/Core#hasPossessedWorkpiece>)",
            },
            "value": None,
            "shape": {
                "short": "FlexConveyorModulePossessionShape",
                "full": str(FC.FlexConveyorModulePossessionShape),
            },
            "constraint": {
                "short": "MaxCountConstraintComponent",
                "full": "http://www.w3.org/ns/shacl#MaxCountConstraintComponent",
            },
        }
    ]
    assert shown["report"] == SKIP_RESERVE_REPORT


def test_a_refusal_of_a_transport_done_shows_both_rules_with_the_box_as_their_value(
    client, streams, memory
):
    """The recorded report of `complete-without-releasing`: each rule is an `sh:or`, so neither
    names a path."""
    report = (
        Path(__file__).parent / "reports" / "complete-without-releasing.nq"
    ).read_text()
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report=report)

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["report_note"] is None
    assert [
        (r["message"], r["shape"]["short"], r["path"], r["value"])
        for r in shown["readable_results"]
    ] == [
        (
            (
                "A Box possessed by a FlexConveyorModule must have a "
                "BoxTransportOperation that is queued, running or failed."
            ),
            "BoxOnModuleHasTransportShape",
            None,
            {"short": "Box1", "full": str(box_iri(1))},
        ),
        (
            (
                "The Box of a done BoxTransportOperation must not be possessed by a "
                "FlexConveyorModule."
            ),
            "DoneTransportBoxReleasedShape",
            None,
            {"short": "Box1", "full": str(box_iri(1))},
        ),
    ]


def test_a_refusal_that_carries_no_report_says_so(client, streams, memory):
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report="")

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["readable_results"] == []
    assert "no report" in shown["report_note"]


def test_a_report_that_is_no_rdf_is_shown_raw_with_a_note_that_says_so(
    client, streams, memory
):
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], report="Unknown dataset: jms-kapps")

    eventually(lambda: client.get("/api/state").json()["refusals"])
    (shown,) = client.get("/api/state").json()["refusals"]
    assert shown["readable_results"] == []
    assert "could not read this report" in shown["report_note"]
    assert shown["report"] == "Unknown dataset: jms-kapps"


def test_only_the_newest_refusals_are_kept_newest_first(client, streams, memory):
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1], KEPT_REFUSALS + 3)

    eventually(
        lambda: len(client.get("/api/state").json()["refusals"]) == KEPT_REFUSALS
    )
    eventually(
        lambda: (
            client.get("/api/state").json()["refusals"][0]["id"]
            == f"m1:{KEPT_REFUSALS + 3}"
        )
    )
    assert client.get("/api/state").json()["refusals"][-1]["id"] == "m1:4"


def test_a_reader_resumes_after_the_last_event_it_read(streams, memory):
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1], 2)

    eventually(lambda: "m1:2" in {kept.event.id for kept in memory.snapshot().refusals})
    eventually(lambda: (STREAM_1, "m1:2") in streams.reads)


def test_a_stream_that_cannot_be_read_is_tried_again(streams, memory):
    streams.down.add(STREAM_1)
    memory.read_graph()
    memory.discover()
    eventually(lambda: any(url == STREAM_1 for url, _ in streams.reads))
    streams.down.clear()

    refuse(streams.events[STREAM_1])

    eventually(lambda: memory.snapshot().refusals, timeout=5.0)


def test_a_stream_that_breaks_in_any_way_is_tried_again(streams, memory):
    """A follower survives whatever a read raises, so the participant is not lost."""
    streams.breaks_with = RuntimeError("a stream broken in a way nobody foresaw")
    memory.read_graph()
    memory.discover()

    refuse(streams.events[STREAM_1])

    eventually(lambda: memory.snapshot().refusals, timeout=5.0)


def test_a_participant_followed_again_resumes_where_its_reader_stopped(
    reads, streams, memory
):
    """Module 1 goes offline and comes back: the monitor reads on after the last event it read,
    and shows no refusal twice."""
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1], 2)
    eventually(lambda: len(memory.snapshot().refusals) == 2)
    reads.live = {WMS}
    memory.read_graph()
    memory.discover()
    reads.live = {M1, WMS}
    memory.read_graph()

    memory.discover()

    eventually(lambda: (STREAM_1, "m1:2") in streams.reads[-3:])
    assert [kept.event.id for kept in memory.snapshot().refusals] == [
        "m1:2",
        "m1:1",
    ]


def test_the_refusals_are_ordered_by_their_time_newest_first(memory):
    late = HandoverEvents(M1, token="late")
    early = HandoverEvents(WMS, token="early")
    refuse(early)
    refuse(late)

    memory.take(late.after(None)[0])
    memory.take(early.after(None)[0])

    assert [kept.event.id for kept in memory.snapshot().refusals] == [
        "late:1",
        "early:1",
    ]


# -- The fill and the handovers --------------------------------------------------------------- #


def possessed_by(possessor: dict[str, IRI]) -> list[BoxRow]:
    """Box 1, bound for Module 5, possessed by the module `possessor["now"]` names."""
    return [
        BoxRow(box_iri(1), OperationStatus.RUNNING, M1, module_iri(5), possessor["now"])
    ]


def test_the_state_says_which_module_holds_a_box_whether_it_is_live_or_not(
    client, reads, memory
):
    """What the page's fill rests on. The page fills a live module that holds a box, and an
    offline one keeps its dashed grey."""
    reads.boxes = lambda: [  # type: ignore[method-assign]
        BoxRow(box_iri(1), OperationStatus.RUNNING, M1, module_iri(5), M1),
        BoxRow(box_iri(2), OperationStatus.RUNNING, M2, module_iri(4), M2),
        BoxRow(box_iri(3), OperationStatus.DONE, M1, module_iri(4), WMS),
    ]
    memory.read_graph()

    modules = client.get("/api/state").json()["modules"]

    assert {m["label"]: (m["live"], m["holds_box"]) for m in modules} == {
        "Module 1": (True, True),
        "Module 2": (False, True),
        "Module 3": (True, False),
        "Module 4": (False, False),
        "Module 5": (False, False),
    }


def test_the_monitor_lists_each_handover_it_sees_once_newest_first(
    client, reads, memory
):
    """Box 1 goes from Module 1 over Module 3 to Module 5. The monitor reads twice while it is on
    Module 3, and one of its reads fails in between: no handover is listed twice, and none is
    lost."""
    possessor = {"now": M1}
    reads.boxes = lambda: possessed_by(possessor)  # type: ignore[method-assign]
    read_modules = reads.modules

    def failing() -> list[ModuleRow]:
        raise ConnectionError("the store is down")

    memory.read_graph()
    possessor["now"] = M3
    memory.read_graph()
    memory.read_graph()
    reads.modules = failing  # type: ignore[method-assign]
    memory.read_graph()
    reads.modules = read_modules  # type: ignore[method-assign]
    possessor["now"] = module_iri(5)
    memory.read_graph()

    handovers = client.get("/api/state").json()["handovers"]

    assert [(h["box"], h["from"], h["to"]) for h in handovers] == [
        ("Box1", "Module3", "Module5"),
        ("Box1", "Module1", "Module3"),
    ]
    assert handovers[0]["seen"] >= handovers[1]["seen"]


def test_a_box_spawned_between_two_reads_shows_its_handover_from_the_wms_the_graph_names(
    client, reads, memory
):
    """The WMS is found by its class in the graph, in the same read, not by the IRI the seed
    gives it."""
    reads.warehouses = {IRI("Warehouse", base="http://example.org/plant#")}
    reads.boxes = list  # type: ignore[method-assign]
    memory.read_graph()
    reads.boxes = lambda: possessed_by({"now": M1})  # type: ignore[method-assign]
    memory.read_graph()

    handovers = client.get("/api/state").json()["handovers"]

    assert [(h["box"], h["from"], h["to"]) for h in handovers] == [
        ("Box1", "Warehouse", "Module1")
    ]


def test_the_first_read_lists_no_handover_since_there_is_nothing_to_compare_it_with(
    reads, memory
):
    """A monitor that starts while a box is on its origin did not see the box handed there."""
    reads.boxes = lambda: possessed_by({"now": M1})  # type: ignore[method-assign]

    memory.read_graph()

    assert memory.snapshot().handovers == ()


def test_only_the_newest_handovers_are_kept_newest_first(reads, memory):
    possessor = {"now": M1}
    reads.boxes = lambda: possessed_by(possessor)  # type: ignore[method-assign]
    memory.read_graph()
    for n in range(1, KEPT_HANDOVERS + 4):
        possessor["now"] = M3 if n % 2 else M1
        memory.read_graph()

    handovers = memory.snapshot().handovers

    # The last read, the KEPT_HANDOVERS + 3rd, took the box to Module 3.
    assert len(handovers) == KEPT_HANDOVERS
    assert (handovers[0].giver, handovers[0].receiver) == (M1, M3)
    assert (handovers[1].giver, handovers[1].receiver) == (M3, M1)


# -- What an open tab costs ------------------------------------------------------------------- #


def test_a_page_poll_reads_only_memory(client, reads, streams, memory, monkeypatch):
    """With every tab closed, the reads of the graph and of the streams are the same as with a
    tab open: a page poll reads neither, and parses no report either."""
    memory.read_graph()
    memory.discover()
    refuse(streams.events[STREAM_1])
    eventually(lambda: memory.snapshot().refusals)
    memory.stop()
    graph_reads, stream_reads = reads.reads, len(streams.reads)

    def no_parse(report: str) -> list:
        raise AssertionError("a page poll parsed a report")

    monkeypatch.setattr(
        "kapps_semantic_middleware.validation_report.validation_results", no_parse
    )
    monkeypatch.setattr(
        "kapps_semantic_middleware.validation_report.readable_results", no_parse
    )
    for _ in range(20):
        assert client.get("/api/state").status_code == 200

    assert (reads.reads, len(streams.reads)) == (graph_reads, stream_reads)


def test_the_background_task_reads_the_graph_and_finds_the_streams_with_no_tab_open(
    reads, streams
):
    memory = MonitorMemory(
        reads, read_stream=streams, graph_seconds=0.05, discover_seconds=0.1
    )
    memory.start()
    try:
        eventually(lambda: reads.reads >= 10)
        eventually(lambda: memory.following() == {M1: STREAM_1, WMS: STREAM_WMS})
    finally:
        memory.stop()
    stopped = reads.reads
    time.sleep(0.2)
    assert reads.reads == stopped
    assert not [t for t in threading.enumerate() if t.name.startswith("monitor-")]


# -- What the monitor writes ------------------------------------------------------------------ #


def test_the_only_routes_that_change_anything_set_how_the_monitor_reads(app):
    """Its mode and the interval of its graph read, which the monitor holds in memory; nothing it
    holds of the graph. The switch and the interval are settings on the page."""
    added = [r for r in app.routes if getattr(r, "path", "").startswith("/api")]

    assert added
    assert {r.path for r in added if r.methods != {"GET"}} == {
        "/api/mode",
        "/api/graph-read-interval",
    }
    assert all(r.methods == {"PUT"} for r in added if r.methods != {"GET"})


# The GraphDB client's and the OGM's writing methods. None of them may appear in the monitor.
WRITES = re.compile(
    r"\b(triples?_(add|delete|update)|graph_add|clear_graph|clear_dataset"
    r"|INSERT|DELETE)\b|\.(create|commit|delete)\("
)


def test_the_monitor_has_no_path_that_writes_to_the_graph():
    """Its own middleware registers it and sends its heartbeat; the monitor's code writes
    nothing."""
    package = Path(jms_kapps.uc2.monitor.__file__).parent
    sources = sorted(package.rglob("*.py"))

    assert sources
    for source in sources:
        for number, line in enumerate(source.read_text().splitlines(), start=1):
            assert not WRITES.search(line), f"{source.name}:{number}: {line.strip()}"


def test_no_subclass_walk_decides_what_the_monitor_shows():
    """A module is whatever the graph types as one of the use case's three module classes, named
    in the monitor's reads; the monitor walks no `rdfs:subClassOf` to find out."""
    walk = re.compile(r"RDFS\.subClassOf|subClassOf[>}]*\*")
    package = Path(jms_kapps.uc2.monitor.__file__).parent
    for source in sorted(package.rglob("*.py")):
        assert not walk.search(source.read_text()), source.name
