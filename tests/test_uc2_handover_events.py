"""A participant's handover event stream: the events it keeps, and the SSE route that serves them.

The graph holds only where a stream lives. The events stay in the participant's memory, the last
500 of them, and `GET /handover-events` serves them as server-sent events. Each connection delivers
what is pending, waits at most a short while for more, and ends, so an open reader never holds a
stopping participant. A reader resumes with the id of the last event it read.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kapps_ogm import Update
from rdflib import Literal
from rdflib.namespace import RDF, SH, XSD

from jms_kapps.uc2.handover_events import (
    STREAM_ROUTE,
    HandoverEvents,
    mount_handover_events,
    stream_locator,
)
from jms_kapps.uc2.vocabulary import FC, box_iri, module_iri, transport_iri

M1, M2 = module_iri(1), module_iri(2)
B1 = box_iri(1)
T1 = transport_iri(B1)
POSSESSOR = "https://w3id.org/circularfactory/Core#hasPossessor"
REPORT = (
    f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"
    f'_:report <{SH.conforms}> "false"^^<{XSD.boolean}> .\n'
    f"_:report <{SH.result}> _:result .\n"
    f"_:result <{SH.focusNode}> <{M2}> .\n"
    f"_:result <{SH.resultPath}> <{POSSESSOR}> .\n"
    f"_:result <{SH.sourceShape}> <{FC.FlexConveyorModulePossessionShape}> .\n"
)

# How long a connection waits for more in these tests: short, so that each test ends quickly.
WAIT_SECONDS = 0.3


def refused(events: HandoverEvents, *, box=B1, report: str = REPORT):
    return events.commit_refused(
        box=box,
        peer=M2,
        transport=T1,
        reason="the triple store refused the handover",
        report=report,
    )


def test_a_refused_commit_is_one_event_with_full_iris_and_the_report_verbatim():
    events = HandoverEvents(M1, token="t")

    event = refused(events)

    assert event.model_dump() == {
        "id": "t:1",
        "time": event.time,
        "resource": str(M1),
        "kind": "commit_refused",
        "box": str(B1),
        "peer": str(M2),
        "transport": str(T1),
        "reason": "the triple store refused the handover",
        "refusal": {
            "results": [
                {
                    "shape": str(FC.FlexConveyorModulePossessionShape),
                    "focus_node": str(M2),
                    "path": f"<{POSSESSOR}>",
                }
            ],
            "report": REPORT,
        },
    }
    assert event.time.endswith("+00:00")


def test_a_report_that_names_no_rule_has_no_result_and_still_travels_whole():
    events = HandoverEvents(M1, token="t")

    event = refused(events, report="not RDF")

    assert event.refusal is not None
    assert (event.refusal.results, event.refusal.report) == ([], "not RDF")


def test_every_rule_of_the_report_is_a_result_of_the_event():
    """Fault scenario 2 names two rules; the event names both, and a path only where one is."""
    other = REPORT + (
        f"_:report <{SH.result}> _:second .\n"
        f"_:second <{SH.focusNode}> <{M2}> .\n"
        f"_:second <{SH.sourceShape}> <{FC.BoxShape}> .\n"
    )

    event = refused(HandoverEvents(M1, token="t"), report=other)

    assert event.refusal is not None
    assert [(r.shape, r.focus_node, r.path) for r in event.refusal.results] == [
        (str(FC.BoxShape), str(M2), None),
        (str(FC.FlexConveyorModulePossessionShape), str(M2), f"<{POSSESSOR}>"),
    ]
    assert event.refusal.report == other


def test_each_process_start_has_its_own_token():
    assert HandoverEvents(M1).token != HandoverEvents(M1).token


def test_the_ids_count_up_and_only_the_last_500_events_are_kept():
    events = HandoverEvents(M1, token="t")

    for n in range(502):
        refused(events, box=box_iri(n + 1))

    kept = events.after(None)
    assert len(kept) == 500
    assert (kept[0].id, kept[-1].id) == ("t:3", "t:502")


def test_a_reader_resumes_after_the_last_event_it_read():
    events = HandoverEvents(M1, token="t")
    for _ in range(3):
        refused(events)

    assert [e.id for e in events.after("t:1")] == ["t:2", "t:3"]
    assert events.after("t:3") == []


@pytest.mark.parametrize("last", ["old:2", "t:x", "nonsense"])
def test_a_reader_whose_last_id_is_from_another_start_gets_every_event_kept(last):
    """A new token means the participant restarted: what the reader read is gone."""
    events = HandoverEvents(M1, token="t")
    for _ in range(2):
        refused(events)

    assert [e.id for e in events.after(last)] == ["t:1", "t:2"]


@pytest.fixture
def events():
    return HandoverEvents(M1, token="t")


@pytest.fixture
def client(events):
    app = FastAPI()
    mount_handover_events(app, events, wait_seconds=WAIT_SECONDS)
    return TestClient(app)


def read(response) -> list[tuple[str, dict]]:
    """The `(id, data)` of each event of an SSE body."""
    found = []
    for block in response.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line
        )
        if "data" in fields:
            found.append((fields["id"], json.loads(fields["data"])))
    return found


def test_the_route_serves_the_pending_events_as_server_sent_events(client, events):
    refused(events)
    refused(events)

    response = client.get(STREAM_ROUTE)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert [(i, data["id"], data["kind"]) for i, data in read(response)] == [
        ("t:1", "t:1", "commit_refused"),
        ("t:2", "t:2", "commit_refused"),
    ]


def test_the_route_resumes_after_the_last_event_id(client, events):
    for _ in range(3):
        refused(events)

    response = client.get(STREAM_ROUTE, headers={"Last-Event-ID": "t:2"})

    assert [i for i, _ in read(response)] == ["t:3"]


def test_a_connection_with_nothing_pending_ends_after_the_wait(client):
    started = time.monotonic()

    response = client.get(STREAM_ROUTE)

    assert read(response) == []
    assert WAIT_SECONDS <= time.monotonic() - started < WAIT_SECONDS + 1.0


def test_a_connection_delivers_an_event_that_comes_while_it_waits(client, events):
    import threading

    threading.Timer(WAIT_SECONDS / 3, lambda: refused(events)).start()

    response = client.get(STREAM_ROUTE)

    assert [i for i, _ in read(response)] == ["t:1"]


def test_the_stream_locator_is_written_as_an_xsd_anyuri_literal():
    # The range of `fc:hasHandoverEventStream` is xsd:anyURI. A plain string would be stored as
    # xsd:string, and an IRI as a node.
    (change,) = stream_locator(M1, "http://127.0.0.1:8101/")

    assert isinstance(change, Update)
    assert change.instance_iri == M1
    (value,) = change.data[str(FC.hasHandoverEventStream)]
    assert isinstance(value, Literal)
    assert value.datatype == XSD.anyURI
    assert str(value) == f"http://127.0.0.1:8101{STREAM_ROUTE}"
