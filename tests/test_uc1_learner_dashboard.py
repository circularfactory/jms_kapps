"""The learner's dashboard: KSM's boxes of the instance, and the box "Learner".

The box renders the learner's memory and nothing else. So these tests need no store: the learner's
middleware is built and never started, its routes are called through a test client, and the
learner runs against fakes of the graph, the time-series store and the commit.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest
from _html import attribute_values, reaches
from fastapi.testclient import TestClient
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import SHORTENING_NOTE, enable_dashboard

from jms_kapps.uc1.frame import UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.learner import Learner
from jms_kapps.uc1.learner_dashboard import register_box
from jms_kapps.uc1.learner_memory import LearnerMemory
from jms_kapps.uc1.learner_process import build, learn_every
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.timeseries import FrameNotFound
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

DEFAULT_BOXES = (
    "header",
    "held-data",
    "services",
    "connectors",
    "refused-writes",
    "logs",
)

# The learner's clock in these tests: 10:00:00 at the start, in a time zone of its own, so the box
# shows the times the tests set.
START = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))


class Clock:
    """A clock that stands still until a test moves it on."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _Cells(HTMLParser):
    """The rows of a fragment's tables, as the text of each cell, header cells included."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cells: list[str] | None = None
        self._text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._text = []

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cells is not None and self._text is not None:
            self._cells.append(" ".join("".join(self._text).split()))
            self._text = None
        elif tag == "tr" and self._cells:
            self.rows.append(self._cells)
            self._cells = None


def cells(fragment: str) -> list[list[str]]:
    parser = _Cells()
    parser.feed(fragment)
    return parser.rows


def operation(number: int) -> str:
    return f"{JMS_NS}TestUnscrewingOperation{number}"


class Graph:
    """The graph as the learner's one query per run reads it: each completed ScrewIntact attempt,
    with its Screw type, its reference, and the value the Screw type holds. It counts the
    queries."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.queries = 0

    def attempt(self, number: int, *, held: float | None) -> None:
        """A ScrewIntact attempt on a Screw of type `:M4`, which holds `held`, its curves at the
        reference `ref-<number>`."""
        row = {
            "operation": operation(number),
            "type": str(JMS.M4),
            "reference": f"ref-{number}",
        }
        if held is not None:
            row["held"] = held
        self.rows.append(row)

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        self.queries += 1
        return {"results": {"bindings": [dict(row) for row in self.rows]}}


class Frames:
    """The time-series store: the frame at each reference, or the error its read raises."""

    def __init__(self) -> None:
        self.at: dict[str, Frame | Exception] = {}

    def read(self, reference: str) -> Frame:
        found = self.at[reference]
        if isinstance(found, Exception):
            raise found
        return found


class Middleware:
    """The learner's middleware, as a run uses it: its graph, and its commit, which keeps each
    entry it is given, or raises `refusal`."""

    def __init__(self, graph: Graph) -> None:
        self.ogm = type("Ogm", (), {"db": graph})()
        self.committed: list = []
        self.refusal: Exception | None = None

    def commit(self, *, entry):
        middleware = self

        class _Commit:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                if exc[0] is None:
                    if middleware.refusal is not None:
                        raise middleware.refusal
                    middleware.committed.append(entry)
                return False

        return _Commit()


# The example recording `success.csv`, whose highest dynamic torque is 0.7: the placeholder
# rule gives 0.84.
SUCCESS = example_frame("success", start=0)


class _NoStore:
    """An object-graph mapper with no store behind it. The middleware is never started, so it
    registers nothing, and nothing a test calls here reaches a graph."""

    db = None


def test_the_learner_serves_the_dashboard_with_the_default_boxes_and_learner() -> None:
    middleware = build(_NoStore(), "127.0.0.1", 8000, influxdb=None)

    page = TestClient(middleware.app).get("/dashboard")

    assert page.status_code == 200
    # Each by a relative path, so that the page works at the learner's own address and under a
    # path that a proxy puts in front of it.
    asked = attribute_values(page.text, "hx-get")
    for box_id in (*DEFAULT_BOXES, "learner"):
        assert any(
            reaches("/dashboard", path, f"/dashboard/box/{box_id}") for path in asked
        ), (box_id, asked)
    assert ">Learner<" in page.text


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def memory(clock) -> LearnerMemory:
    return LearnerMemory(clock=clock)


@pytest.fixture
def box(memory):
    """The fragment of the box "Learner", as the page asks for it, from `memory`, on the
    learner's middleware."""
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(JMS.Learner1),
        service_class=str(JMS.LearnerService),
        ogm=_NoStore(),
        host="127.0.0.1",
        port=8000,
    )
    register_box(enable_dashboard(middleware), memory)
    client = TestClient(middleware.app)

    def fragment() -> str:
        response = client.get("/dashboard/box/learner")
        assert response.status_code == 200, response.text
        return response.text

    return fragment


def test_before_its_first_run_the_box_says_so_and_when_the_first_run_comes(
    memory, box
) -> None:
    memory.next_run_in(10)

    shown = cells(box())

    assert ["Last run", "none yet"] in shown
    assert ["Next run", "10:00:10, every 10 s"] in shown
    assert ["Attempts learned from", "0"] in shown
    assert "No error since the learner started." in box()


@pytest.fixture
def graph() -> Graph:
    return Graph()


@pytest.fixture
def frames() -> Frames:
    return Frames()


@pytest.fixture
def middleware(graph) -> Middleware:
    return Middleware(graph)


@pytest.fixture
def learner(middleware, frames, memory) -> Learner:
    return Learner(middleware, frames, memory)


def test_after_a_run_that_changed_a_value_the_box_shows_the_run_and_the_change_old_to_new(
    graph, frames, learner, clock, box
) -> None:
    """`:M4` held the example 1.5. Two ScrewIntact attempts of `success.csv` give 0.84."""
    for number in (1, 2):
        graph.attempt(number, held=1.5)
        frames.at[f"ref-{number}"] = SUCCESS
    clock.advance(10)

    learner.learn()

    shown = cells(box())
    assert [
        "Last run",
        "10:00:10, learned from 2 new attempts, changed 1 value",
    ] in shown
    assert ["Attempts learned from", "2, 2 of them new in the last run"] in shown
    assert ["10:00:10", "M4", "hasMaxDynamicTorque", "1.5 → 0.84"] in shown


def disassembly(*torques: float) -> Frame:
    """An attempt's frame whose Disassembly samples carry `torques`, arbitrary test values."""
    return Frame(
        times=[i * 1_000_000_000 for i in range(len(torques))],
        signals={UNSCREWING_TORQUE: list(torques)},
        phases=["Disassembly"] * len(torques),
    )


def test_later_runs_show_the_newest_change_first_and_how_many_attempts_were_new(
    graph, frames, learner, clock, box
) -> None:
    """The first run replaces the example 1.5 with 0.84. A run with nothing new changes nothing. A new
    attempt with a dynamic torque of 1.0 raises 0.84, which `:M4` now holds, to 1.2."""
    graph.attempt(1, held=1.5)
    frames.at["ref-1"] = SUCCESS
    learner.learn()

    clock.advance(10)
    learner.learn()
    shown = cells(box())
    assert [
        "Last run",
        "10:00:10, learned from 0 new attempts, changed 0 values",
    ] in shown
    assert ["Attempts learned from", "1, 0 of them new in the last run"] in shown

    graph.rows.clear()
    graph.attempt(1, held=0.84)
    graph.attempt(2, held=0.84)
    frames.at["ref-2"] = disassembly(0.5, 1.0)
    clock.advance(10)
    learner.learn()

    shown = cells(box())
    assert [
        "Last run",
        "10:00:20, learned from 1 new attempt, changed 1 value",
    ] in shown
    assert ["Attempts learned from", "2, 1 of them new in the last run"] in shown
    changes = [row for row in shown if len(row) == 4]
    assert changes == [
        ["When", "Screw type", "Threshold", "Old → new"],
        ["10:00:20", "M4", "hasMaxDynamicTorque", "0.84 → 1.2"],
        ["10:00:00", "M4", "hasMaxDynamicTorque", "1.5 → 0.84"],
    ]


def last_error_part(fragment: str) -> str:
    """The part "Last error" of the box."""
    return fragment.split("Last error", 1)[1]


def last_error(fragment: str) -> list[list[str]]:
    """The rows of the part "Last error"."""
    return cells(last_error_part(fragment))


def test_an_attempt_passed_over_shows_as_the_last_error_with_its_operation(
    graph, frames, learner, clock, box
) -> None:
    graph.attempt(1, held=1.5)
    graph.attempt(2, held=1.5)
    frames.at["ref-1"] = SUCCESS
    frames.at["ref-2"] = FrameNotFound(
        "the time-series store holds no frame never-written"
    )
    clock.advance(10)

    learner.learn()

    fragment = box()
    assert [
        "Last run",
        "10:00:10, learned from 1 new attempt, changed 1 value",
    ] in cells(fragment)
    assert ["Passed over", "1, whose curves could not be read"] in cells(fragment)
    assert last_error(fragment) == [
        ["When", "10:00:10"],
        ["Operation", "TestUnscrewingOperation2"],
        [
            "Why",
            (
                "Its curves cannot be read, so the learner passes over it: the "
                "time-series store holds no frame never-written"
            ),
        ],
    ]


def test_a_run_that_fails_at_a_read_shows_the_operation_and_why_and_the_next_run_learns(
    graph, frames, learner, clock, box
) -> None:
    graph.attempt(1, held=1.5)
    frames.at["ref-1"] = ConnectionError("InfluxDB at http://127.0.0.1:8181 refused")
    clock.advance(10)

    with pytest.raises(ConnectionError):
        learner.learn()

    fragment = box()
    assert ["Last run", "10:00:10, failed: see the last error"] in cells(fragment)
    assert last_error(fragment) == [
        ["When", "10:00:10"],
        ["Operation", "TestUnscrewingOperation1"],
        [
            "Why",
            (
                "Reading its curves from the time-series store failed: InfluxDB at "
                "http://127.0.0.1:8181 refused"
            ),
        ],
    ]

    frames.at["ref-1"] = SUCCESS
    clock.advance(10)
    learner.learn()

    fragment = box()
    assert [
        "Last run",
        "10:00:20, learned from 1 new attempt, changed 1 value",
    ] in cells(fragment)
    assert ["10:00:20", "M4", "hasMaxDynamicTorque", "1.5 → 0.84"] in cells(fragment)
    assert ["When", "10:00:10"] in last_error(fragment)


def test_a_refused_commit_shows_why_and_names_no_operation(
    graph, frames, middleware, learner, clock, box
) -> None:
    graph.attempt(1, held=1.5)
    frames.at["ref-1"] = SUCCESS
    middleware.refusal = RuntimeError("the store refused the update")
    clock.advance(10)

    with pytest.raises(RuntimeError):
        learner.learn()

    fragment = box()
    assert ["Last run", "10:00:10, failed: see the last error"] in cells(fragment)
    assert "It has changed no value yet." in fragment
    assert last_error(fragment) == [
        ["When", "10:00:10"],
        ["Operation", "—"],
        [
            "Why",
            (
                "The commit of hasMaxDynamicTorque to the Screw type M4 failed: the store "
                "refused the update"
            ),
        ],
    ]


class Untouchable:
    """A time-series store that fails any test that reaches it."""

    def __getattr__(self, name):
        pytest.fail(f"the render reached the time-series store: {name}")


def test_the_box_renders_from_memory_with_no_read_of_the_graph_or_the_frames(
    graph,
) -> None:
    ogm = type("Ogm", (), {"db": graph})()
    middleware = build(ogm, "127.0.0.1", 8000, influxdb=Untouchable())
    client = TestClient(middleware.app)
    # Building the dashboard reads the instance's name from the graph, once. The renders below
    # may read nothing more.
    reads_at_build = graph.queries

    for _ in range(3):
        assert client.get("/dashboard/box/learner").status_code == 200

    assert graph.queries == reads_at_build


def test_the_learner_tells_its_memory_when_the_next_run_comes(memory, clock) -> None:
    """Before the first run, and again after each run, which here takes 5 s by the clock."""

    def learn() -> None:
        clock.advance(5)

    async def schedule() -> None:
        task = asyncio.create_task(learn_every(0.05, learn, memory))
        await asyncio.sleep(0)
        assert memory.state().next_run == START + timedelta(seconds=0.05)
        for _ in range(200):
            await asyncio.sleep(0.005)
            if memory.state().next_run == START + timedelta(seconds=5.05):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(schedule())

    assert memory.state().next_run == START + timedelta(seconds=5.05)


def test_a_restarted_learner_that_commits_the_value_the_type_holds_shows_no_change(
    graph, frames, learner, clock, box
) -> None:
    """A learner that starts again derives 0.84 once more, which `:M4` already holds: that is no
    change, and the box lists none."""
    graph.attempt(1, held=0.84)
    frames.at["ref-1"] = SUCCESS
    clock.advance(10)

    learner.learn()

    fragment = box()
    assert [
        "Last run",
        "10:00:10, learned from 1 new attempt, changed 0 values",
    ] in cells(fragment)
    assert "It has changed no value yet." in fragment


def test_a_value_the_type_holds_that_is_no_number_does_not_stop_the_learner(
    graph, frames, learner, clock, box
) -> None:
    graph.attempt(1, held="high")
    frames.at["ref-1"] = SUCCESS
    clock.advance(10)

    learner.learn()

    assert ["10:00:10", "M4", "hasMaxDynamicTorque", "— → 0.84"] in cells(box())


def test_while_a_run_is_under_way_the_box_says_so_in_place_of_the_next_run(
    graph, middleware, clock, memory, box
) -> None:
    seen: list[list[list[str]]] = []

    class Watched:
        """Reads `SUCCESS`, and looks at the box while it reads."""

        def read(self, reference: str) -> Frame:
            seen.append(cells(box()))
            return SUCCESS

    learner = Learner(middleware, Watched(), memory)
    graph.attempt(1, held=1.5)
    memory.next_run_in(10)
    clock.advance(10)

    learner.learn()

    assert ["Next run", "under way now, every 10 s"] in seen[0]
    assert ["Next run", "10:00:10, every 10 s"] in cells(box())


def test_the_box_does_not_say_that_it_shortens_the_operation_of_the_last_error(
    graph, frames, learner, box
) -> None:
    graph.attempt(1, held=1.5)
    frames.at["ref-1"] = ConnectionError("refused")

    with pytest.raises(ConnectionError):
        learner.learn()

    assert SHORTENING_NOTE not in last_error_part(box())
    assert SHORTENING_NOTE not in box()


def test_the_learner_serves_the_pages_that_its_row_on_the_page_links() -> None:
    """The API docs, the activity page and the dashboard with its Logs tab answer."""
    client = TestClient(build(_NoStore(), "127.0.0.1", 8000, influxdb=None).app)

    for path in ("docs", "activity", "dashboard", "dashboard?tab=Logs"):
        assert client.get("/" + path).status_code == 200, path
