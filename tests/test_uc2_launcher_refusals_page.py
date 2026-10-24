"""The Launcher page's checkbox "Collect the SHACL refusals in one monitor", in a headless browser.

The page is the real one, served by the Launcher's real app. What stands behind the Launcher is
fake, and nothing reaches a store: the graph calls no service live, and every process only sleeps.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from _serving import serving
from _wait import eventually
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

LABEL = "Collect the SHACL refusals in one monitor"


class Graph:
    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        pass

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def launcher_url(monkeypatch) -> Iterator[Callable[..., tuple[Launcher, str]]]:
    """Serves the page of a Launcher, launched first with `collect_refusals` as given."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launchers: list[Launcher] = []
    servers: list = []

    def open_with(*, collect_refusals: bool = False) -> tuple[Launcher, str]:
        launcher = Launcher(
            graph=Graph(),
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
        )
        launchers.append(launcher)
        launcher.launch(
            load_topology("pair"), name="pair", collect_refusals=collect_refusals
        )
        server = serving(create_app(launcher))
        address = server.__enter__()
        servers.append(server)
        return launcher, f"{address}/"

    yield open_with
    for server in servers:
        server.__exit__(None, None, None)
    for launcher in launchers:
        launcher.stop_all()


def test_the_checkbox_is_beside_the_launch_controls_with_the_label_and_off_by_default(
    page: Page, launcher_url
):
    _, url = launcher_url()
    page.goto(url)

    checkbox = page.get_by_label(LABEL)
    expect(checkbox).not_to_be_checked()
    toolbar = page.locator(
        ".toolbar", has=page.get_by_role("button", name="Launch", exact=True)
    )
    expect(toolbar.get_by_label(LABEL)).to_have_count(1)


def test_a_page_opened_on_a_launch_that_collects_shows_the_box_ticked(
    page: Page, launcher_url
):
    _, url = launcher_url(collect_refusals=True)
    page.goto(url)

    expect(page.get_by_label(LABEL)).to_be_checked()


def test_a_launch_sends_the_checkbox_and_the_state_reports_it(page: Page, launcher_url):
    launcher, url = launcher_url()
    page.goto(url)
    page.get_by_label("Topology to launch").select_option("grid")
    page.get_by_label(LABEL).check()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch", exact=True).click()

    assert launched.value.post_data_json["collect_refusals"] is True
    expect(page.locator("#running")).to_have_text("running: grid")
    assert launcher.state().collect_refusals is True


def test_a_launch_with_the_checkbox_off_sends_it_off(page: Page, launcher_url):
    launcher, url = launcher_url(collect_refusals=True)
    page.goto(url)
    # The box starts at the launch in force once the first state arrives; unchecking it
    # earlier would be undone by that start.
    expect(page.get_by_label(LABEL)).to_be_checked()
    page.get_by_label(LABEL).uncheck()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch", exact=True).click()

    assert launched.value.post_data_json["collect_refusals"] is False
    eventually(lambda: launcher.state().collect_refusals is False, timeout=30)


def test_an_edited_launch_sends_the_checkbox_as_a_query_parameter(
    page: Page, launcher_url
):
    launcher, url = launcher_url()
    page.goto(url)
    page.get_by_label("Topology to launch").select_option("pair")
    page.get_by_role("button", name="Edit").click()
    launch_edited = page.get_by_role("button", name="Launch", exact=True)
    expect(launch_edited).to_be_enabled()
    page.get_by_label(LABEL).check()

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: "/api/topology/launch" in request.url
    ) as launched:
        launch_edited.click()

    query = parse_qs(urlparse(launched.value.url).query)
    assert query["collect_refusals"] == ["true"]
    eventually(lambda: launcher.state().collect_refusals is True, timeout=30)
