"""The anomaly detector's dashboard: KSM's boxes of the instance, and the boxes "Classifications"
and "Screw type values".

The boxes render the detector's memory and nothing else. So these tests need no store: the
middleware is built and never started, its routes are called through a test client, and the
memory is a stand-in that a test fills with with_origin values, as the detector's reads give them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _html import attribute_values, reaches, rows
from _uc1_example import EXAMPLE_M4_VALUES, RECORDING_PLACEMENT_VALUES
from fastapi.testclient import TestClient
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import SHORTENING_NOTE, enable_dashboard
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.anomaly_detector import RESOURCE, build
from jms_kapps.uc1.anomaly_detector_dashboard import (
    CLASSIFICATIONS_BOX,
    TAB,
    TYPE_VALUES_BOX,
    register_boxes,
)
from jms_kapps.uc1.anomaly_detector_memory import (
    APPROACH_END,
    KEPT_CLASSIFICATIONS,
    SCREW_HEAD,
    DetectorMemory,
    MeasuredPosition,
)
from jms_kapps.uc1.decision_tree import EXIT_RULE
from jms_kapps.uc1.effective_values import (
    EffectiveValuesWithOrigin,
    resolve_with_origin,
)
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, TYPE_LEVEL

AT = datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC)
SCREW = IRI("Screw1", base=JMS_NS)


class _NoStore:
    """An object-graph mapper with no store behind it. The middleware is never started, so it
    registers nothing, and nothing a test calls here reaches a graph."""

    db = None


class _NoFrames:
    """A time-series store that no test reads."""

    def read(self, reference: str):
        raise AssertionError("no frame is read")


class Clock:
    def __init__(self) -> None:
        self.now = AT

    def __call__(self) -> datetime:
        return self.now


def operation(number: int) -> IRI:
    return IRI(f"UnscrewingOperation_{number}", base=JMS_NS)


def with_origin(type_values=EXAMPLE_M4_VALUES) -> EffectiveValuesWithOrigin:
    """The values of `SCREW`, of the type `:M4` with `type_values`, placed as the recordings
    need."""
    held = RECORDING_PLACEMENT_VALUES
    return resolve_with_origin(
        SCREW,
        {prop: [value] for prop, value in held.items()},
        JMS.M4,
        {prop: [value] for prop, value in type_values.items()},
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def memory(clock) -> DetectorMemory:
    return DetectorMemory(clock=clock)


@pytest.fixture
def boxes(memory):
    """The fragment of each box, as the page asks for it, from `memory`."""
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(RESOURCE),
        service_class=str(JMS.AnomalyDetectorService),
        ogm=_NoStore(),
        host="127.0.0.1",
        port=8000,
    )
    register_boxes(enable_dashboard(middleware), memory)
    client = TestClient(middleware.app)

    def fragment(box_id: str) -> str:
        response = client.get(f"/dashboard/box/{box_id}")
        assert response.status_code == 200, response.text
        return response.text

    return fragment


def classify(memory: DetectorMemory, number: int, outcome="ScrewIntact", **values):
    found = with_origin(**values)
    memory.read(found)
    memory.classified(
        operation(number),
        found,
        outcome,
        None if outcome == "ScrewIntact" else f"ANOMALY: {outcome}",
    )


def test_the_detector_serves_its_dashboard_open_on_its_tab_with_both_boxes() -> None:
    middleware = build(
        str(RESOURCE),
        _NoStore(),  # type: ignore[arg-type]
        "127.0.0.1",
        8000,
        reader=_NoFrames(),  # type: ignore[arg-type]
    )

    page = TestClient(middleware.app).get("/dashboard")

    assert page.status_code == 200
    asked = attribute_values(page.text, "hx-get")
    for box_id in (CLASSIFICATIONS_BOX, TYPE_VALUES_BOX):
        assert any(
            reaches("/dashboard", path, f"/dashboard/box/{box_id}") for path in asked
        ), box_id
    assert f">{TAB}<" in page.text
    checked = [
        line for line in page.text.split("<input") if "checked" in line.split(">")[0]
    ]
    assert len(checked) == 1 and "anomaly-detector" in checked[0]


def test_before_the_first_classification_each_box_says_so(boxes) -> None:
    assert "It has classified no attempt yet." in boxes(CLASSIFICATIONS_BOX)
    assert "It has read no Screw type yet." in boxes(TYPE_VALUES_BOX)


def test_a_classification_shows_its_outcome_exit_values_and_what_the_tree_asked(
    memory, boxes
) -> None:
    classify(memory, 1, "MissingScrew")

    shown = boxes(CLASSIFICATIONS_BOX)
    table = rows(shown)

    assert ["UnscrewingOperation_1"] in table
    assert ["Screw1"] in table
    assert ["M4"] in table
    assert ["MissingScrew"] in table
    assert [EXIT_RULE["MissingScrew"]] in table
    values = {row[0]: row[1:] for row in table if len(row) == 3}
    assert len(values) == 10
    assert values["hasMaxDynamicTorque"] == ["1.5", ""]
    assert values["hasMinCounterTorque"][1:] == [""]
    assert values["hasMinApproachPosition"][1:] == ["asked"]
    assert values["hasMinApproachAxialForce"][1:] == ["asked"]
    assert sum(1 for row in values.values() if row[1] == "asked") == 2
    assert SHORTENING_NOTE not in shown


def test_a_classification_shows_how_it_ended_the_operation(memory, boxes) -> None:
    """An intact screw ends its operation done; a failure outcome ends it failed, and
    names it."""
    classify(memory, 1, "ScrewIntact")
    classify(memory, 2, "MissingScrew")

    intact, anomaly = (
        rows(section) for section in boxes(CLASSIFICATIONS_BOX).split("<h3>")[1:][::-1]
    )

    assert ["done"] in intact
    assert ["failed"] in anomaly
    assert ["ANOMALY: MissingScrew"] in anomaly


def test_an_attempt_that_could_not_be_classified_shows_why(memory, boxes) -> None:
    memory.not_classified(
        operation(1), SCREW, None, "the anomaly detector could not classify the attempt"
    )

    table = rows(boxes(CLASSIFICATIONS_BOX))

    assert ["not classified: the operation failed"] in table
    assert ["the anomaly detector could not classify the attempt"] in table
    assert not [row for row in table if len(row) == 3]


def test_several_classifications_are_shown_newest_first(memory, clock, boxes) -> None:
    for number in (1, 2, 3):
        classify(memory, number)
        clock.now += timedelta(seconds=5)

    shown = boxes(CLASSIFICATIONS_BOX)

    order = [shown.index(f"UnscrewingOperation_{number}<") for number in (3, 2, 1)]
    assert order == sorted(order)
    assert "10:00:10" in shown


def test_beyond_the_kept_classifications_the_box_says_how_many_it_shows(
    memory, boxes
) -> None:
    for number in range(KEPT_CLASSIFICATIONS + 2):
        classify(memory, number)

    assert (
        f"The newest {KEPT_CLASSIFICATIONS} of {KEPT_CLASSIFICATIONS + 2} classifications"
        in boxes(CLASSIFICATIONS_BOX)
    )


def test_the_screw_type_values_show_the_learners_write_at_the_next_read(
    memory, clock, boxes
) -> None:
    classify(memory, 1)
    before = {row[1]: row[2:] for row in rows(boxes(TYPE_VALUES_BOX))}
    clock.now += timedelta(seconds=10)
    classify(
        memory,
        2,
        type_values={**EXAMPLE_M4_VALUES, JMS.hasMaxDynamicTorque: 0.84},
    )

    after = {row[1]: row[2:] for row in rows(boxes(TYPE_VALUES_BOX))}

    assert len(after) == len(TYPE_LEVEL)
    assert before["hasMaxDynamicTorque"] == ["1.5", "10:00:00"]
    assert after["hasMaxDynamicTorque"] == ["0.84", "10:00:10"]


def test_the_boxes_carry_no_script_and_no_styling_of_their_own(memory, boxes) -> None:
    classify(memory, 1)

    for box_id in (CLASSIFICATIONS_BOX, TYPE_VALUES_BOX):
        shown = boxes(box_id)
        assert "<script" not in shown
        assert "style=" not in shown
        assert "<style" not in shown


def test_a_classification_shows_its_mode_and_the_positions_it_saved(
    memory, boxes
) -> None:
    found = with_origin()
    memory.classified(operation(1), found, "ScrewIntact", None)
    memory.classified(
        operation(2),
        found,
        "HeadOccluded",
        "ANOMALY: HeadOccluded",
        saves_uq=True,
        positions=(
            MeasuredPosition(APPROACH_END, -0.25, 0.1),
            MeasuredPosition(SCREW_HEAD, 0.5, 0.1),
        ),
    )

    fragment = boxes(CLASSIFICATIONS_BOX)
    table = rows(fragment)

    assert fragment.count("<th>Measurements as uncertainty quantifications</th>") == 2
    assert ["saved"] in table
    assert ["not saved"] in table
    assert "<th>Measured approach end position</th>" in fragment
    assert ["-0.25 mm, standard deviation 0.1 mm"] in table
    assert "<th>Measured screw head position</th>" in fragment
    assert ["0.5 mm, standard deviation 0.1 mm"] in table
