"""The Launcher page's JavaScript, in a headless browser, as a person uses the page.

The page is the real one, served by the Launcher's real app, so its state route is the real route.
What stands behind the route is fake, and nothing reaches a store: the graph says that every
process of the pair is live at the address of one stand-in, the children are processes that only
sleep, and the modules' fault switches record each switch they are sent. The stand-in answers
every page of every process with a heading that names the path it was asked for.
"""

from __future__ import annotations

import html
import json
import sys
from collections.abc import Collection, Iterator

import pytest
from _services import live_services
from _serving import serving
from _topologies import FIRST_ITERATIONS_CROSS
from _wait import eventually
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from kapps_triplestore_interface import IRI
from playwright.sync_api import JSHandle, Locator, Page, Route, expect
from starlette.types import ASGIApp, Receive, Scope, Send

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import TYPE_KEY, load_topology, parse_topology
from jms_kapps.uc2.vocabulary import FC_NS, MONITOR, WMS

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# The path that a forwarded port puts in front of the Launcher, as VS Code's `/proxy/<port>/`
# does: the browser asks for the page under it, and the Launcher sees each request without it.
FORWARDED = "/forwarded/8090"


def stand_in() -> FastAPI:
    """Every process of the run at once: each page names the path it was asked for."""
    app = FastAPI()

    @app.get("/{path:path}", response_class=HTMLResponse)
    def page(path: str) -> str:
        return f"<!doctype html><title>stand-in</title><h1>/{html.escape(path)}</h1>"

    return app


class Graph:
    """Says that each resource of `resources` is live at `address`, and seeds nothing."""

    def __init__(self, resources: set[IRI], address: str) -> None:
        self.resources = resources
        self.address = address

    def seed(
        self, topology: object, transit_seconds: float, *, collect_history: bool = False
    ) -> None:
        pass

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return live_services(
            self.resources, workflows, dict.fromkeys(self.resources, self.address)
        )


class FaultSwitches:
    """The modules' fault switches: each switch sent, as (address, fault, armed)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    def set(self, address: str, name: str, armed: bool) -> None:
        self.sent.append((address, name, armed))


def forwarded(app: ASGIApp) -> ASGIApp:
    """`app` behind a forwarded port that puts `FORWARDED` in front of it: a request under that
    path reaches `app` without it, and any other request is refused."""

    async def under_the_path(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path: str = scope["path"]
            if not path.startswith(FORWARDED + "/"):
                await PlainTextResponse("not forwarded", 404)(scope, receive, send)
                return
            scope = {
                **scope,
                "path": path.removeprefix(FORWARDED),
                "raw_path": scope["raw_path"].removeprefix(FORWARDED.encode()),
            }
        await app(scope, receive, send)

    return under_the_path


@pytest.fixture(scope="module")
def process_address() -> Iterator[str]:
    """Where every process of the pair is live: the stand-in's address."""
    with serving(stand_in()) as address:
        yield address


@pytest.fixture
def faults() -> FaultSwitches:
    return FaultSwitches()


@pytest.fixture
def launcher(process_address, faults) -> Iterator[Launcher]:
    """A Launcher that runs the pair, every process of it live, every fault off."""
    pair = load_topology("pair")
    resources = {module.resource for module in pair.modules} | {WMS, MONITOR}
    launcher = Launcher(
        graph=Graph(resources, process_address),
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        faults=faults,
        echo=lambda line: None,
        stop_timeout=5.0,
    )
    # Forced: the fake graph calls every process live before it was launched.
    launcher.launch(pair, name="pair", force=True)
    try:
        eventually(
            lambda: all(p.child.state == "live" for p in launcher.state().processes)
        )
        yield launcher
    finally:
        launcher.stop_all()


@pytest.fixture
def launcher_url(launcher) -> Iterator[str]:
    """The Launcher page, at the root of the Launcher's own port."""
    with serving(create_app(launcher)) as address:
        yield f"{address}/"


@pytest.fixture
def idle_launcher_url(process_address, faults) -> Iterator[str]:
    """The page of a Launcher that has launched nothing."""
    launcher = Launcher(
        graph=Graph(set(), process_address),
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        faults=faults,
        echo=lambda line: None,
    )
    with serving(create_app(launcher)) as address:
        yield f"{address}/"


@pytest.fixture
def forwarded_launcher_url(launcher) -> Iterator[str]:
    """The Launcher page through a forwarded port, under `FORWARDED` and nowhere else."""
    with serving(forwarded(create_app(launcher))) as address:
        yield f"{address}{FORWARDED}/"


def process_row(page: Page, name: str) -> Locator:
    """The row of the process `name` in the table of processes."""
    return page.get_by_role("row").filter(
        has=page.get_by_role("button", name=name, exact=True)
    )


def removals(table: Locator) -> JSHandle:
    """Every node taken out of `table` from now on, by its name. A node taken out while a person
    presses it loses the click: the press and the release fall on two different elements."""
    return table.evaluate_handle(
        """(table) => {
            const removed = [];
            new MutationObserver((records) => {
                for (const record of records)
                    for (const node of record.removedNodes) removed.push(node.nodeName);
            }).observe(table, { childList: true, subtree: true });
            return removed;
        }"""
    )


def a_poll_is_drawn(page: Page) -> None:
    """Return once the page has drawn its answer to a poll it sent after this was called.

    The page sends one poll at a time, so it sends a poll only once it has drawn the answer to the
    one before: the second poll sent from now on means that the first one's answer is drawn.
    """
    for _ in range(2):
        with page.expect_request(lambda request: request.url.endswith("/api/state")):
            pass


def test_a_click_on_a_fault_switch_is_taken_while_the_page_polls(
    page: Page, launcher_url, faults, process_address
):
    """The regression test of the Launcher's lost clicks (jms_kapps merge request 34). The page polls the
    Launcher every second, and a person presses a switch while a poll's answer is drawn: the
    switch under the pointer stays the element it was, so the release completes the click, and
    each click reaches the module. No element of the table is replaced while nothing changes."""
    page.goto(launcher_url)
    table = page.get_by_role("table").filter(has=process_row(page, "Module 1"))
    switch = process_row(page, "Module 1").get_by_role("checkbox", name="skip-reserve")
    expect(switch).to_be_enabled()
    removed = removals(table)

    for clicks, armed in enumerate((True, False, True), start=1):
        switch.hover()
        page.mouse.down()
        a_poll_is_drawn(page)
        page.mouse.up()
        eventually(lambda clicks=clicks: len(faults.sent) == clicks, timeout=5)
        expect(switch).to_be_checked(checked=armed)

    assert faults.sent == [
        (process_address, "skip-reserve", True),
        (process_address, "skip-reserve", False),
        (process_address, "skip-reserve", True),
    ]
    assert removed.json_value() == []


def test_behind_a_forwarded_port_every_link_stays_on_that_port_under_its_path(
    page: Page, forwarded_launcher_url
):
    """The one-port Launcher: the page asks for everything by a relative path, and each page of
    each process opens through the Launcher's own port, so the page works under the path that a
    forwarded port puts in front of it."""
    page.goto(forwarded_launcher_url)
    module_1 = process_row(page, "Module 1")
    expect(module_1.get_by_role("link", name="dashboard")).to_be_visible()

    links = page.get_by_role("link").evaluate_all(
        "(links) => links.map((link) => link.href)"
    )
    assert len(links) > 0
    assert [
        link for link in links if not link.startswith(f"{forwarded_launcher_url}p/")
    ] == []

    for link, path, opened_at in [
        (
            module_1.get_by_role("link", name="dashboard"),
            "/dashboard",
            "p/Module1/dashboard",
        ),
        (page.get_by_role("link", name="Open the monitor"), "/", "p/Monitor1/"),
        (
            page.get_by_role("link", name="Open the WMS"),
            "/dashboard",
            "p/WarehouseManagementSystem1/dashboard?tab=WMS",
        ),
    ]:
        with page.expect_popup() as popup:
            link.click()
        expect(popup.value.get_by_role("heading")).to_have_text(path)
        assert popup.value.url == f"{forwarded_launcher_url}{opened_at}"
        popup.value.close()


def test_each_module_and_the_wms_link_their_logs_and_the_monitor_does_not(
    page: Page, launcher_url
):
    """A live module's row and the WMS's row link the dashboard's Logs tab, beside the activity
    page. The monitor serves no dashboard, so its row has no such link."""
    page.goto(launcher_url)
    module_1 = process_row(page, "Module 1")
    expect(module_1.get_by_role("link", name="logs")).to_have_attribute(
        "href", "p/Module1/dashboard?tab=Logs"
    )
    # The pair's two modules and the WMS.
    expect(page.get_by_role("link", name="logs", exact=True)).to_have_count(3)
    monitor_row = page.get_by_role("row").filter(
        has=page.get_by_role("link", name="open", exact=True)
    )
    expect(monitor_row).to_have_count(1)
    expect(monitor_row.get_by_role("link", name="logs")).to_have_count(0)


def test_behind_a_forwarded_port_a_switch_reaches_the_module(
    page: Page, forwarded_launcher_url, faults, process_address
):
    """A command goes to the Launcher under the forwarded port's path too."""
    page.goto(forwarded_launcher_url)
    switch = process_row(page, "Module 2").get_by_role(
        "checkbox", name="complete-without-releasing"
    )

    # A click, rather than `check()`: a poll answered while the switch goes to the Launcher may show
    # it off for a moment, until the Launcher's answer shows it on.
    switch.click()

    eventually(lambda: len(faults.sent) == 1, timeout=5)
    assert faults.sent == [(process_address, "complete-without-releasing", True)]
    expect(switch).to_be_checked()


def topology_rows(page: Page) -> list[list[str]]:
    """The cells of each row of the page's topology table, as a person reads them."""
    rows = page.locator("#modules tbody tr")
    return [row.locator("td").all_inner_texts() for row in rows.all()]


def test_the_topology_table_shows_each_module_its_types_and_its_connections(
    page: Page, launcher_url
):
    page.goto(launcher_url)

    expect(page.locator("#modules tbody tr")).to_have_count(2)
    assert topology_rows(page) == [
        ["Module1", "Module 1", "EntryModule", "—", "—", "Module2", "—"],
        ["Module2", "Module 2", "ExitModule", "—", "—", "—", "—"],
    ]


def test_the_topology_table_shows_a_module_list_without_labels_and_with_two_types(
    page: Page, launcher, launcher_url
):
    """A module with no label is called by its local name; one with two types shows both.
    Launched by the Launcher itself, forced, since the fake graph calls the pair's processes live
    still."""
    first = json.loads(FIRST_ITERATIONS_CROSS.read_text(encoding="utf-8"))
    first[0][TYPE_KEY].insert(0, FC_NS + "FlexConveyorModule")
    launcher.launch(parse_topology(first), name="edited", force=True)
    page.goto(launcher_url)

    expect(page.locator("#modules tbody tr")).to_have_count(5)
    two_types = "FlexConveyorModule, EntryModule"
    assert topology_rows(page) == [
        ["Module1", "Module1", two_types, "—", "—", "Module3", "—"],
        ["Module2", "Module2", "EntryModule", "—", "Module3", "—", "—"],
        [
            "Module3",
            "Module3",
            "FlexConveyorModule",
            "Module1",
            "Module4",
            "Module5",
            "Module2",
        ],
        ["Module4", "Module4", "ExitModule", "—", "—", "—", "Module3"],
        ["Module5", "Module5", "ExitModule", "Module3", "—", "—", "—"],
    ]


def elsewhere(route: Route) -> None:
    """The Launcher's state, with its links to the monitor and to the WMS's dashboard changed to
    paths that no process of the run has."""
    response = route.fetch()
    state = response.json()
    state["monitor"] = "p/Elsewhere/"
    state["wms"] = "p/Elsewhere/dashboard?tab=WMS"
    route.fulfill(response=response, json=state)


def test_the_header_opens_the_monitor_and_the_wms_where_the_state_says_and_nowhere_else(
    page: Page, launcher_url
):
    """The link to the monitor is a field of the Launcher's state, `monitor`, and the link to the
    WMS's dashboard is one too, `wms`: the page shows each as the state gives it, and builds no
    link of its own. Here the state's answer is changed on its way to the page. (Added on
    2026-10-11, jms_kapps 33.)"""
    page.route("**/api/state", elsewhere)
    try:
        page.goto(launcher_url)

        expect(page.get_by_role("link", name="Open the monitor")).to_have_attribute(
            "href", "p/Elsewhere/"
        )
        expect(page.get_by_role("link", name="Open the WMS")).to_have_attribute(
            "href", "p/Elsewhere/dashboard?tab=WMS"
        )
    finally:
        # A poll the route still takes after the server has stopped would fail the teardown.
        page.unroute_all(behavior="ignoreErrors")


def test_while_nothing_runs_the_page_offers_no_monitor_and_no_wms_to_open(
    page: Page, idle_launcher_url
):
    """A link the page hides stays hidden, whatever display the look of a button gives it."""
    page.goto(idle_launcher_url)

    expect(page.get_by_text("Nothing runs. Launch a topology below.")).to_be_visible()
    expect(page.get_by_text("Open the monitor")).to_be_hidden()
    expect(page.get_by_text("Open the WMS")).to_be_hidden()
