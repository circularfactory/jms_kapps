"""The Launcher page's field "deadlock alarm (s)", in a headless browser, as a person uses it
(Etienne, 2026-10-11, jms_kapps 77).

The page is the real one, served by the Launcher's real app. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records each topology it is given to seed and calls
no service live, and every process only sleeps.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator

import pytest
from _serving import serving
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import module_iri

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]


class Graph:
    """Records each topology it is given to seed, and calls no service live."""

    def __init__(self) -> None:
        self.seeded: list[Topology] = []

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        self.seeded.append(topology)

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def launcher_url(monkeypatch) -> Iterator[Callable[..., tuple[Launcher, str]]]:
    """Serves the page of a Launcher with the defaults it is given, not launched yet."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launchers: list[Launcher] = []
    servers: list = []

    def open_with(**defaults: float) -> tuple[Launcher, str]:
        launcher = Launcher(
            graph=Graph(),
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
            **defaults,
        )
        launchers.append(launcher)
        server = serving(create_app(launcher))
        address = server.__enter__()
        servers.append(server)
        return launcher, f"{address}/"

    yield open_with
    for server in servers:
        server.__exit__(None, None, None)
    for launcher in launchers:
        launcher.stop_all()


def test_the_field_starts_at_300_s_and_a_launch_sends_it(page: Page, launcher_url):
    launcher, url = launcher_url()
    page.goto(url)
    field = page.get_by_label("Deadlock alarm time in seconds")
    expect(field).to_have_value("300")

    field.fill("45")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch this topology").click()

    assert launched.value.post_data_json["deadlock_alarm_seconds"] == 45
    expect(page.locator("#running")).to_have_text("running: cross")
    [module_1] = [
        p for p in launcher.state().processes if p.child.resource == str(module_iri(1))
    ]
    assert module_1.child.command.endswith("--deadlock-alarm-seconds 45.0")


def test_the_field_starts_at_the_launcher_s_own_alarm_time(page: Page, launcher_url):
    """As `jms-uc2 --deadlock-alarm-seconds 12.5` leaves it."""
    _, url = launcher_url(deadlock_alarm_seconds=12.5)
    page.goto(url)

    expect(page.get_by_label("Deadlock alarm time in seconds")).to_have_value("12.5")


@pytest.mark.parametrize("text", ["0", "-3", "soon", ""])
def test_an_alarm_time_that_is_not_a_positive_number_is_refused_readably_and_launches_nothing(
    page: Page, launcher_url, text
):
    launcher, url = launcher_url()
    page.goto(url)
    expect(page.get_by_label("Deadlock alarm time in seconds")).to_have_value("300")
    page.get_by_label("Deadlock alarm time in seconds").fill(text)
    launches: list[str] = []
    page.on(
        "request",
        lambda request: (
            launches.append(request.url)
            if request.url.endswith("/api/launch")
            else None
        ),
    )

    page.get_by_role("button", name="Launch this topology").click()

    expect(page.locator("#command-message")).to_have_text(
        "Not launched: the deadlock alarm time must be a number of seconds above 0, "
        f'not "{text}".'
    )
    assert launches == []
    assert launcher.state().topology is None
