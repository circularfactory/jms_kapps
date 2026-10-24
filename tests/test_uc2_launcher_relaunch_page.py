"""The Launcher page across a launch of another topology, in a headless browser, as a person who
keeps the page open uses it.

The page is the real one, served by the Launcher's real app, so its state route and its launch are
the server's own. What stands behind the Launcher is fake, and nothing reaches a store: the graph
seeds nothing and calls live the resources a test names, every process only sleeps, and the list
of the boxes on the station reads the stand-in store of `_station_boxes.py`, once. The page opens
on the cross with each of its processes live. A launch from the page then needs the graph to call
none live, as when the cross's processes have deregistered, so the new processes stay `starting`,
with no address yet, while the test looks.

The page draws each of its parts on its own: an error while drawing one part is shown in the page,
and the other parts are drawn all the same. A test breaks one part through the browser: it changes
the Launcher's answers on their way to the page, so that a `starting` process cannot be drawn.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Iterator

import pytest
from _services import live_services
from _serving import serving
from _station_boxes import ONCE, StandInStore
from _wait import eventually
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, Route, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.launcher.boxes import StationBoxes
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import MONITOR, WMS

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# Where the graph says that a live process's service is. No test follows a link to it.
NOWHERE = "http://127.0.0.1:9"

# What the page says, in the page, of a part it could not draw.
COULD_NOT_DRAW = "The page could not draw"

# The routes whose answer is the Launcher's state, which the page draws: a poll, and a launch.
STATE_ROUTES = ["**/api/state", "**/api/launch"]


class Graph:
    """Seeds nothing, and calls live each resource in `live`, which a test changes."""

    def __init__(self) -> None:
        self.live: set[IRI] = set()

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        pass

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        live = set(self.live)
        return live_services(live, workflows, dict.fromkeys(live, NOWHERE))


def resources(topology: Topology) -> set[IRI]:
    """The resource of each process of a launch of `topology`."""
    return {module.resource for module in topology.modules} | {WMS, MONITOR}


@pytest.fixture
def graph() -> Graph:
    return Graph()


@pytest.fixture
def launcher_url(graph, monkeypatch) -> Iterator[str]:
    """The page of a Launcher that runs the cross, each of its processes live, and lists the two
    boxes the stand-in store answers."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launcher = Launcher(
        graph=graph,
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=lambda line: None,
        stop_timeout=5.0,
        boxes=StationBoxes(StandInStore(), interval=ONCE),
    )
    cross = load_topology("cross")
    graph.live = resources(cross)
    # Forced: the graph calls the cross's processes live before they were launched.
    launcher.launch(cross, name="cross", force=True)
    try:
        eventually(
            lambda: all(p.child.state == "live" for p in launcher.state().processes)
        )
        with serving(create_app(launcher)) as address:
            yield f"{address}/"
    finally:
        launcher.stop_all()


def open_on_the_cross(page: Page, url: str) -> None:
    """Open the page at `url`, and wait until it has drawn the cross, its processes live."""
    page.goto(url)
    expect(page.locator("#running")).to_have_text("running: cross")
    expect(page.locator("#modules tbody tr")).to_have_count(5)
    expect(page.locator("#processes tbody tr").first).to_contain_text("live")


def launch_from_the_page(page: Page, graph: Graph, name: str) -> None:
    """Launch the shipped topology `name` from the page, as a person does. The processes that ran
    have deregistered by then, so the launch's guard finds none live, and the new ones start."""
    graph.live = set()
    page.get_by_label("Topology to launch").select_option(name)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Launch", exact=True).click()


def topology_ids(page: Page) -> list[str]:
    """The first cell of each row of the page's topology table: each module's id."""
    return page.locator("#modules tbody tr td:first-child").all_inner_texts()


def process_states(page: Page) -> dict[str, str]:
    """The state of each process in the page's table of processes, by the process's name."""
    rows = page.locator("#processes tbody tr").all()
    return {
        row.locator("td").nth(0).inner_text(): row.locator("td").nth(1).inner_text()
        for row in rows
    }


def test_an_open_page_draws_the_topology_launched_from_it_while_its_processes_start(
    page: Page, launcher_url, graph
):
    """The case of the grid launched after the cross on an open
    page, whose Topology table kept the cross. Here the page draws the grid's table and status,
    while each of the grid's processes is `starting`, with no address yet. Offline this case
    draws right on the page too: the next test makes a `starting` process fail to draw."""
    open_on_the_cross(page, launcher_url)

    launch_from_the_page(page, graph, "grid")

    grid = load_topology("grid")
    expect(page.locator("#running")).to_have_text("running: grid")
    expect(page.locator("#modules tbody tr")).to_have_count(len(grid.modules))
    assert topology_ids(page) == [module.id for module in grid.modules]
    expect(page.locator("#processes tbody tr")).to_have_count(len(grid.modules) + 2)
    states = process_states(page)
    assert set(states) == {module.name for module in grid.modules} | {
        "Warehouse Management System",
        "Monitor",
    }
    assert set(states.values()) == {"starting"}
    expect(page.locator("#status")).to_be_hidden()
    expect(page.get_by_text(COULD_NOT_DRAW)).to_have_count(0)


def no_links_while_starting(route: Route) -> None:
    """The Launcher's state, with no list of links for each `starting` process: the page cannot
    draw such a process's row."""
    response = route.fetch()
    state = response.json()
    for process in state["processes"]:
        if process["state"] == "starting":
            process["links"] = None
    route.fulfill(response=response, json=state)


def test_after_a_launch_a_part_that_cannot_be_drawn_is_shown_and_the_others_are_drawn(
    page: Page, launcher_url, graph
):
    """The grid launched after the cross, and its `starting` processes cannot be drawn: the page
    says so, in the page, and draws the grid's table, its status, the header and the boxes on the
    station all the same. Once the processes can be drawn, they are, and the error goes."""
    open_on_the_cross(page, launcher_url)
    for route in STATE_ROUTES:
        page.route(route, no_links_while_starting)
    try:
        launch_from_the_page(page, graph, "grid")

        error = page.get_by_role("alert").filter(has_text=COULD_NOT_DRAW)
        expect(error).to_contain_text(
            "The page could not draw the processes from the Launcher's latest answer: "
        )
        # The processes alone: every other part is drawn.
        assert error.inner_text().count(COULD_NOT_DRAW) == 1
        grid = load_topology("grid")
        expect(page.locator("#running")).to_have_text("running: grid")
        expect(page.locator("#modules tbody tr")).to_have_count(len(grid.modules))
        assert topology_ids(page) == [module.id for module in grid.modules]
        expect(page.locator("#where")).to_have_text("dataset test-dataset")
        expect(page.locator("#boxes tbody tr")).to_have_count(2)
        expect(page.locator("#status")).to_be_hidden()

        for route in STATE_ROUTES:
            page.unroute(route, no_links_while_starting)

        expect(error).to_have_count(0)
        expect(page.locator("#processes tbody tr")).to_have_count(len(grid.modules) + 2)
        assert set(process_states(page).values()) == {"starting"}
    finally:
        # A poll the route still takes after the server has stopped would fail the teardown.
        page.unroute_all(behavior="ignoreErrors")
