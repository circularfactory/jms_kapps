"""The monitor page's "possible deadlock" notice, in a headless browser (Etienne, 2026-10-11,
jms_kapps 77).

The page is the real one, served by the monitor's own routes, except its state route: a fake that
answers the state a test sets, and reads nothing. What the state holds is tested in
`test_uc2_monitor_deadlock.py`.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator

import pytest
from _serving import serving
from fastapi import FastAPI
from playwright.sync_api import Page, expect

from jms_kapps.uc2.monitor.api import mount_onto

pytestmark = pytest.mark.browser

EMPTY = {
    "error": None,
    "modules": [],
    "wires": [],
    "boxes": [],
    "handovers": [],
    "refusals": [],
    "possible_deadlock": None,
    "reading": {
        "mode": "graph + streams",
        "modes": ["graph + streams", "graph only"],
        "graph_seconds": 1.0,
        "shortest_graph_seconds": 0.5,
        "longest_graph_seconds": 10.0,
    },
    "queries": [],
}


def _alarm(n: int, asked: int) -> dict:
    return {
        "id": f"m{n}:1",
        "time": "2026-10-11T12:00:00+00:00",
        "module": f"Module{n}",
        "box": f"Box{n}",
        "asked": [f"Module{asked}"],
        "reason": f"Module{n} has waited 301 s to hand Box{n} on, asking Module{asked}",
    }


CYCLE = {
    "modules": ["Module1", "Module2", "Module3"],
    "alarms": [_alarm(1, 2), _alarm(2, 3), _alarm(3, 1)],
}


class NoMemory:
    """In place of the monitor's memory, which the fake state route stands in for."""

    def snapshot(self) -> None:
        raise AssertionError("the page's state comes from the fake state route")


@pytest.fixture
def state() -> dict:
    return copy.deepcopy(EMPTY)


@pytest.fixture
def monitor_url(state) -> Iterator[str]:
    app = FastAPI()

    # Before the monitor's own routes, so that this one answers the page's polls.
    @app.get("/api/state")
    def answer() -> dict:
        return state

    mount_onto(app, memory=NoMemory())
    with serving(app) as address:
        yield f"{address}/"


def test_the_alarms_on_a_cycle_show_as_one_notice_that_names_every_module(
    page: Page, monitor_url, state
):
    state["possible_deadlock"] = CYCLE
    page.goto(monitor_url)

    notice = page.locator("#deadlock .notice")
    expect(notice).to_have_count(1)
    expect(notice).to_have_text("Possible deadlock: Module1, Module2 and Module3")
    expect(page.locator("#deadlock li")).to_have_text(
        [
            "Module1 has waited 301 s to hand Box1 on, asking Module2",
            "Module2 has waited 301 s to hand Box2 on, asking Module3",
            "Module3 has waited 301 s to hand Box3 on, asking Module1",
        ]
    )


def test_without_an_alarm_the_page_says_so(page: Page, monitor_url):
    page.goto(monitor_url)

    expect(page.locator("#deadlock")).to_have_text("No deadlock alarm.")


def test_graph_only_shows_no_notice_and_says_why(page: Page, monitor_url, state):
    state["reading"]["mode"] = "graph only"
    page.goto(monitor_url)

    expect(page.locator("#deadlock .notice")).to_have_count(0)
    expect(page.locator("#deadlock")).to_contain_text(
        'In "graph only" the monitor follows no handover event stream'
    )
    expect(page.locator("#deadlock")).to_contain_text(
        'switch to "graph + streams" to see a possible deadlock'
    )
