"""The screwing resource's dashboard: KSM's boxes of the instance, and the box "Attempts".

The box renders the screwing resource's memory and nothing else. So these tests need no store:
the middleware is built and never started, its routes are called through a test client, and the
memory is a stand-in that a test fills.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _html import attribute_values, reaches, rows
from fastapi.testclient import TestClient
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import SHORTENING_NOTE, enable_dashboard
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.screwing_resource import RESOURCE, build
from jms_kapps.uc1.screwing_resource_dashboard import BOX, TAB, register_box
from jms_kapps.uc1.screwing_resource_memory import (
    KEPT_ATTEMPTS,
    AttemptRecord,
    ScrewingMemory,
)
from jms_kapps.uc1.seed import M4_VALUES
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

AT = datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC)
SCREW = IRI("Screw1", base=JMS_NS)
REFERENCE = (
    "influxdb:///jms-kapps/unscrewing?frame=f1"
    "&start=2026-10-17T10:00:00.000000000Z&stop=2026-10-17T10:00:07.000000000Z"
)
ENDPOINT = "http://127.0.0.1:8102/workflows/classify/execute"


class _NoStore:
    """An object-graph mapper with no store behind it. The middleware is never started, so it
    registers nothing, and nothing a test calls here reaches a graph."""

    db = None


def operation(number: int) -> IRI:
    return IRI(f"UnscrewingOperation_{number}", base=JMS_NS)


def done(number: int, seconds: int = 0) -> AttemptRecord:
    """An attempt that the detector ended `done` with ScrewIntact."""
    return AttemptRecord(
        at=AT + timedelta(seconds=seconds),
        operation=operation(number),
        screw=SCREW,
        screw_type=JMS.M4,
        reference=REFERENCE,
        detector=ENDPOINT,
        status=OperationStatus.DONE,
        outcome=f"{JMS_NS}ScrewIntact",
        type_values=M4_VALUES,
    )


@pytest.fixture
def memory() -> ScrewingMemory:
    return ScrewingMemory()


@pytest.fixture
def box(memory):
    """The fragment of the box "Attempts", as the page asks for it, from `memory`."""
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(RESOURCE),
        service_class=str(JMS.ScrewingResourceService),
        ogm=_NoStore(),
        host="127.0.0.1",
        port=8000,
    )
    register_box(enable_dashboard(middleware), memory)
    client = TestClient(middleware.app)

    def fragment() -> str:
        response = client.get(f"/dashboard/box/{BOX}")
        assert response.status_code == 200, response.text
        return response.text

    return fragment


def test_the_screwing_resource_serves_its_dashboard_open_on_the_attempts_tab() -> None:
    middleware = build(
        str(RESOURCE),
        _NoStore(),  # type: ignore[arg-type]
        "127.0.0.1",
        8000,
        writer=object(),  # type: ignore[arg-type]
        attempt=lambda screw, values: None,  # type: ignore[arg-type,return-value]
    )

    page = TestClient(middleware.app).get("/dashboard")

    assert page.status_code == 200
    asked = attribute_values(page.text, "hx-get")
    assert any(reaches("/dashboard", path, f"/dashboard/box/{BOX}") for path in asked)
    assert f">{TAB}<" in page.text
    # The page opens on the tab: its radio is the one checked.
    checked = [
        line for line in page.text.split("<input") if "checked" in line.split(">")[0]
    ]
    assert len(checked) == 1 and "screwing-resource" in checked[0]


def test_before_the_first_attempt_the_box_says_so(box) -> None:
    assert "It has made no attempt yet." in box()


def test_one_attempt_shows_each_field_of_its_row(memory, box) -> None:
    memory.attempted(done(1))

    shown = box()

    assert rows(shown) == [
        [
            "10:00:00",
            "UnscrewingOperation_1",
            "Screw1",
            "M4",
            "f1",
            "0.5 0.5 0.1 30 3 1 1.5",
            "127.0.0.1:8102",
            "done, ScrewIntact",
            "—",
        ]
    ]
    assert SHORTENING_NOTE not in shown
    # Each shortened value is there in full when the pointer rests on it.
    for full in (str(operation(1)), str(SCREW), str(JMS.M4), ENDPOINT):
        assert f'title="{full}"' in shown
    assert 'title="MinApproachAxialForce 0.5, MinCounterTorque 0.5,' in shown
    assert "MaxDynamicTorque 1.5" in shown
    assert 'title="influxdb:///jms-kapps/unscrewing?frame=f1&amp;start=' in shown


def test_several_attempts_are_shown_newest_first_with_why_each_failed(
    memory, box
) -> None:
    memory.attempted(done(1))
    memory.attempted(
        AttemptRecord(
            at=AT + timedelta(seconds=5),
            operation=operation(2),
            screw=SCREW,
            screw_type=JMS.M4,
            status=OperationStatus.FAILED,
            why="the unscrewing attempt failed: no recording for Screw1",
        )
    )
    memory.attempted(
        AttemptRecord(
            at=AT + timedelta(seconds=9),
            operation=operation(3),
            screw=SCREW,
            screw_type=JMS.M4,
            reference=REFERENCE,
            why="the detector was not reached: the knowledge graph names no live service",
        )
    )

    shown = rows(box())

    assert [row[1] for row in shown] == [
        "UnscrewingOperation_3",
        "UnscrewingOperation_2",
        "UnscrewingOperation_1",
    ]
    assert shown[0][4:] == [
        "f1",
        "—",
        "—",
        "not reported",
        "the detector was not reached: the knowledge graph names no live service",
    ]
    assert shown[1][4:] == [
        "—",
        "—",
        "—",
        "failed",
        "the unscrewing attempt failed: no recording for Screw1",
    ]


def test_an_anomaly_shows_failed_with_its_outcome_and_why(memory, box) -> None:
    memory.attempted(
        AttemptRecord(
            at=AT,
            operation=operation(1),
            screw=SCREW,
            screw_type=JMS.M4,
            reference=REFERENCE,
            detector=ENDPOINT,
            status=OperationStatus.FAILED,
            outcome=f"{JMS_NS}MissingScrew",
            why="ANOMALY: MissingScrew",
        )
    )

    (row,) = rows(box())

    assert row[-2:] == ["failed, MissingScrew", "ANOMALY: MissingScrew"]


def test_beyond_the_kept_attempts_the_box_says_how_many_it_shows(memory, box) -> None:
    for number in range(KEPT_ATTEMPTS + 3):
        memory.attempted(done(number, seconds=number))

    shown = box()

    assert len(rows(shown)) == KEPT_ATTEMPTS
    assert f"The newest {KEPT_ATTEMPTS} of {KEPT_ATTEMPTS + 3} attempts" in shown


def test_the_box_carries_no_script_and_no_styling_of_its_own(memory, box) -> None:
    memory.attempted(done(1))

    shown = box()

    assert "<script" not in shown
    assert "style=" not in shown
    assert "<style" not in shown
