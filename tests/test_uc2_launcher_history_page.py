"""The Launcher page's checkbox "collect history", in a headless browser, as a person uses it.

The page is the real one, served by the Launcher's real app. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records the option of each seed and calls no service
live, and every process only sleeps. (Added on 2026-10-10, jms_kapps 22, with the names Etienne
decided on 2026-10-07: `jms-uc2 --collect-history`, the checkbox "collect history", and the field
`collect_history`.)
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from _serving import serving
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]


class Graph:
    """Records the option of each seed, and calls no service live."""

    def __init__(self) -> None:
        self.collected: list[bool] = []

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        self.collected.append(collect_history)

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def launcher_url(monkeypatch) -> Iterator[Callable[..., tuple[Launcher, Graph, str]]]:
    """Serves the page of a Launcher made with `collect_history`, as `jms-uc2` makes it from its
    option, and launched first naming none, as `jms-uc2` launches it."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launchers: list[Launcher] = []
    servers: list = []

    def open_with(*, collect_history: bool = False) -> tuple[Launcher, Graph, str]:
        graph = Graph()
        launcher = Launcher(
            graph=graph,
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
            collect_history=collect_history,
        )
        launchers.append(launcher)
        launcher.launch(load_topology("pair"), name="pair")
        server = serving(create_app(launcher))
        address = server.__enter__()
        servers.append(server)
        return launcher, graph, f"{address}/"

    yield open_with
    for server in servers:
        server.__exit__(None, None, None)
    for launcher in launchers:
        launcher.stop_all()


def test_the_checkbox_is_beside_the_topology_picker_and_off_by_default(
    page: Page, launcher_url
):
    _, _, url = launcher_url()
    page.goto(url)

    checkbox = page.get_by_label("collect history")
    expect(checkbox).not_to_be_checked()
    toolbar = page.locator(".toolbar", has=page.get_by_label("Topology to launch"))
    expect(toolbar.get_by_label("collect history")).to_have_count(1)
    expect(page.locator("#history")).to_have_text("")


def test_jms_uc2_with_collect_history_starts_the_page_with_it_on(
    page: Page, launcher_url
):
    """As `jms-uc2 --collect-history` leaves the Launcher: its first launch collects history,
    and the page opens on the checkbox on, and says so beside the running topology."""
    _, _, url = launcher_url(collect_history=True)
    page.goto(url)

    expect(page.get_by_label("collect history")).to_be_checked()
    expect(page.locator("#history")).to_have_text("collects history")


def test_a_launch_sends_the_checkbox_and_the_state_reports_it(page: Page, launcher_url):
    launcher, graph, url = launcher_url()
    page.goto(url)
    page.get_by_label("Topology to launch").select_option("grid")
    page.get_by_label("collect history").check()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch this topology").click()

    assert launched.value.post_data_json["collect_history"] is True
    expect(page.locator("#running")).to_have_text("running: grid")
    expect(page.locator("#history")).to_have_text("collects history")
    assert launcher.state().collect_history is True
    assert graph.collected == [False, True]


def test_a_launch_with_the_checkbox_off_sends_it_off(page: Page, launcher_url):
    launcher, graph, url = launcher_url(collect_history=True)
    page.goto(url)
    page.get_by_label("collect history").uncheck()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch this topology").click()

    assert launched.value.post_data_json["collect_history"] is False
    expect(page.locator("#history")).to_have_text("")
    assert launcher.state().collect_history is False
    assert graph.collected == [True, False]


def test_a_launch_of_the_editor_sends_the_checkbox(page: Page, launcher_url):
    launcher, graph, url = launcher_url()
    page.goto(url)
    page.get_by_label("Topology to launch").select_option("pair")
    page.get_by_role("button", name="Edit").click()
    launch_edited = page.get_by_role("button", name="Launch this", exact=True)
    expect(launch_edited).to_be_enabled()
    page.get_by_label("collect history").check()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: "/api/topology/launch" in request.url
    ) as launched:
        launch_edited.click()

    query = parse_qs(urlparse(launched.value.url).query)
    assert query["collect_history"] == ["true"]
    expect(page.locator("#history")).to_have_text("collects history")
    assert launcher.state().collect_history is True
    assert graph.collected == [False, True]


def test_a_person_s_choice_stays_while_the_page_polls(page: Page, launcher_url):
    _, _, url = launcher_url()
    page.goto(url)
    checkbox = page.get_by_label("collect history")
    expect(checkbox).not_to_be_checked()

    checkbox.check()
    # Two polls later, the choice is the person's still.
    for _ in range(2):
        with page.expect_request(lambda request: request.url.endswith("/api/state")):
            pass

    expect(checkbox).to_be_checked()
