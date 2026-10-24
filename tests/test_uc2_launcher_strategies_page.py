"""The Launcher page's two strategy pickers, in a headless browser, as a person uses them.

The page is the real one, served by the Launcher's real app. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records each topology it is given to seed and calls
no service live, and every process only sleeps. The Launcher finds the test strategies beside
`real`, through a fake set of entry points (`_strategies`).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Iterator

import pytest
from _serving import serving
from _strategies import REAL_ENTRIES, TEST_ENTRIES, entry_points
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.strategies import ModuleStrategy, WmsStrategy, found
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import WMS

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
REGISTERED = entry_points(REAL_ENTRIES, TEST_ENTRIES)


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

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


@pytest.fixture
def launcher_url(monkeypatch) -> Iterator[Callable[..., tuple[Launcher, str]]]:
    """Serves the page of a Launcher that finds the test strategies, launched first with the
    strategies it is given, or not launched where it is given none."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launchers: list[Launcher] = []

    def open_with(**launched: str) -> tuple[Launcher, str]:
        launcher = Launcher(
            graph=Graph(),
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
            module_strategies=found(ModuleStrategy, REGISTERED),
            wms_strategies=found(WmsStrategy, REGISTERED),
        )
        launchers.append(launcher)
        if launched:
            launcher.launch(load_topology("pair"), name="pair", **launched)
        server = serving(create_app(launcher))
        address = server.__enter__()
        servers.append(server)
        return launcher, f"{address}/"

    servers: list = []
    yield open_with
    for server in servers:
        server.__exit__(None, None, None)
    for launcher in launchers:
        launcher.stop_all()


def test_the_two_pickers_list_the_strategies_by_label_with_real_behaviour_chosen(
    page: Page, launcher_url
):
    _, url = launcher_url()
    page.goto(url)

    module = page.get_by_label("Module strategy")
    wms = page.get_by_label("WMS strategy")
    expect(module.locator("option")).to_have_text(
        ["Real behaviour", "South only (synthetic)"]
    )
    expect(wms.locator("option")).to_have_text(
        ["Real behaviour", "Exit straight below (synthetic)"]
    )
    expect(module).to_have_value("real")
    expect(wms).to_have_value("real")


def test_a_launch_sends_the_chosen_names_and_the_page_shows_the_strategies_in_force(
    page: Page, launcher_url
):
    launcher, url = launcher_url()
    page.goto(url)
    page.get_by_label("Topology to launch").select_option("grid")
    page.get_by_label("Module strategy").select_option(label="South only (synthetic)")
    page.get_by_label("WMS strategy").select_option(
        label="Exit straight below (synthetic)"
    )

    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request(
        lambda request: request.url.endswith("/api/launch")
    ) as launched:
        page.get_by_role("button", name="Launch", exact=True).click()

    assert launched.value.post_data_json == {
        "name": "grid",
        "transit_time": 3,
        "module_strategy": "south",
        "wms_strategy": "exit-below",
        # The checkbox "collect history", off.
        "collect_history": False,
        # The checkbox "collect the SHACL refusals", off.
        "collect_refusals": False,
        # The field "deadlock alarm (s)", at its default.
        "deadlock_alarm_seconds": 300,
    }
    expect(page.locator("#running")).to_have_text("running: grid")
    expect(page.locator("#strategies")).to_have_text(
        "Module strategy: South only (synthetic) · WMS strategy: Exit straight below "
        "(synthetic)"
    )
    state = launcher.state()
    assert (state.module_strategy, state.wms_strategy) == ("south", "exit-below")
    [wms] = [p for p in state.processes if p.child.resource == str(WMS)]
    assert wms.child.command.endswith("--wms-strategy exit-below")


def test_the_pickers_start_at_the_strategies_in_force(page: Page, launcher_url):
    """As `jms-uc2 --module-strategy south --wms-strategy exit-below` leaves them: its first
    launch runs with them, and the page opens on them."""
    _, url = launcher_url(module_strategy="south", wms_strategy="exit-below")
    page.goto(url)

    expect(page.get_by_label("Module strategy")).to_have_value("south")
    expect(page.get_by_label("WMS strategy")).to_have_value("exit-below")
    expect(page.locator("#strategies")).to_have_text(
        "Module strategy: South only (synthetic) · WMS strategy: Exit straight below "
        "(synthetic)"
    )


def test_a_person_s_choice_stays_while_the_page_polls(page: Page, launcher_url):
    _, url = launcher_url(module_strategy="real", wms_strategy="real")
    page.goto(url)
    picker = page.get_by_label("Module strategy")
    expect(picker).to_have_value("real")

    picker.select_option("south")
    # Two polls later, the choice is the person's still.
    for _ in range(2):
        with page.expect_request(lambda request: request.url.endswith("/api/state")):
            pass

    expect(picker).to_have_value("south")
