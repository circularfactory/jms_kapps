"""The Launcher page's topology section, in a headless browser, as a person uses it: the grid that
draws the chosen topology, edit mode with its discard question, the one Launch button, the picker's
entry for a running topology that is not shipped, and the panel of what runs.

The page is the real one, served by the Launcher's real app, so the shipped topologies, the
layout, the load check and the launch are the server's own. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records each topology it is given to seed and calls
no service live, and every process only sleeps.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Iterator

import pytest
from _serving import serving
from _wait import eventually
from kapps_triplestore_interface import IRI
from playwright.sync_api import Dialog, Locator, Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import (
    Topology,
    load_topology,
    parse_topology,
    topology_names,
)

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

PASSES = "The load check passes."


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
def graph() -> Graph:
    return Graph()


@pytest.fixture
def launcher(graph, monkeypatch) -> Iterator[Launcher]:
    """A Launcher that runs nothing yet, on no GraphDB the page names."""
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    launcher = Launcher(
        graph=graph,
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=lambda line: None,
        stop_timeout=5.0,
    )
    try:
        yield launcher
    finally:
        launcher.stop_all()


@pytest.fixture
def launcher_url(launcher) -> Iterator[str]:
    with serving(create_app(launcher)) as address:
        yield f"{address}/"


def picker(page: Page) -> Locator:
    return page.get_by_label("Topology to launch")


def grid_modules(page: Page) -> dict[str, str]:
    """Each module the grid draws, by its id, with the cell it draws it on."""
    return page.locator("#editor-grid [data-module]").evaluate_all(
        "nodes => Object.fromEntries(nodes.map(n => [n.dataset.module, n.dataset.cell]))"
    )


def drawn(page: Page, topology: Topology) -> None:
    """Wait until the grid draws `topology`, each module on the cell the layout gives it."""
    places = {id: f"{x},{y}" for id, (x, y) in topology.places().items()}
    eventually(lambda: grid_modules(page) == places, timeout=5)


def button(page: Page, name: str) -> Locator:
    return page.get_by_role("button", name=name, exact=True)


def open_page(page: Page, url: str) -> None:
    page.goto(url)
    drawn(page, load_topology(topology_names()[0]))


def press_edit(page: Page) -> None:
    """Press Edit, and wait until the grid is in edit mode and its load check has answered."""
    button(page, "Edit").click()
    expect(button(page, "Close")).to_be_visible()
    expect(page.locator("#editor-check")).not_to_have_text("The load check runs…")


def change_the_edit(page: Page) -> None:
    """Add a module on the first empty cell right of the first row, and wait for its check."""
    with page.expect_response(lambda r: r.url.endswith("/api/topology/check")):
        page.locator('#editor-grid [data-cell="-1,0"]').click()
    expect(page.locator("#editor-check")).not_to_have_text("The load check runs…")


def answer_dialogs(page: Page, accept: bool) -> list[str]:
    """Answer every dialog the page opens from now on, and return what each one asked."""
    asked: list[str] = []

    def answer(dialog: Dialog) -> None:
        asked.append(dialog.message)
        if accept:
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", answer)
    return asked


def launch(page: Page) -> None:
    """Press Launch and accept its confirmation."""
    page.once("dialog", lambda dialog: dialog.accept())
    button(page, "Launch").click()


# -- Outside edit mode ---------------------------------------------------------------------- #


def test_the_grid_draws_the_chosen_topology_read_only_and_again_at_each_change_of_the_picker(
    page: Page, launcher_url
):
    open_page(page, launcher_url)

    for name in ("tee", "pair", "grid"):
        picker(page).select_option(name)
        drawn(page, load_topology(name))
        # Read-only: nothing on the grid is a button, and no empty cell offers a module.
        expect(page.locator("#editor-grid button")).to_have_count(0)
        expect(page.locator("#editor-grid .cell.empty")).to_have_count(0)
    expect(page.locator("#editor")).to_be_hidden()


def test_the_read_only_grid_shows_each_connection_by_its_glyph(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("tee")
    drawn(page, load_topology("tee"))

    expect(page.locator('#editor-grid [data-edge="Module1 Module2"]')).to_have_text("↓")
    expect(page.locator('#editor-grid [data-edge="Module2 Module3"]')).to_have_text("→")


def test_launch_outside_edit_mode_launches_the_chosen_topology(
    page: Page, launcher_url, graph
):
    open_page(page, launcher_url)
    picker(page).select_option("tee")
    drawn(page, load_topology("tee"))
    asked = answer_dialogs(page, accept=True)

    button(page, "Launch").click()

    expect(page.locator("#running")).to_have_text("running: tee")
    assert asked == [
        'Launching "tee" clears dataset test-dataset and restarts every process. Go on?'
    ]
    assert graph.seeded == [load_topology("tee")]
    expect(button(page, "Launch this topology")).to_have_count(0)
    expect(button(page, "Launch this")).to_have_count(0)


# -- Edit mode ------------------------------------------------------------------------------ #


def test_edit_makes_the_grid_editable_and_shows_download_and_close_in_place_of_edit(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    drawn(page, load_topology("pair"))
    expect(button(page, "Download")).to_be_hidden()
    expect(button(page, "Close")).to_be_hidden()

    button(page, "Edit").click()

    expect(button(page, "Edit")).to_be_hidden()
    expect(button(page, "Download")).to_be_visible()
    expect(button(page, "Close")).to_be_visible()
    expect(page.locator("#editor-base")).to_have_text('Edited from "pair"')
    expect(page.locator("#editor-grid .cell.empty").first).to_be_visible()
    drawn(page, load_topology("pair"))

    button(page, "Close").click()

    expect(button(page, "Edit")).to_be_visible()
    expect(button(page, "Download")).to_be_hidden()
    expect(page.locator("#editor-grid button")).to_have_count(0)


def test_launch_in_edit_mode_launches_the_edit_and_ends_edit_mode(
    page: Page, launcher_url, graph
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    with page.expect_request(lambda r: r.url.endswith("/api/topology/check")) as sent:
        page.locator('#editor-grid [data-cell="-1,0"]').click()
    expect(page.locator("#editor-check")).to_have_text(PASSES)
    asked = answer_dialogs(page, accept=True)

    button(page, "Launch").click()

    expect(page.locator("#running")).to_have_text("running: pair (edited)")
    assert asked == [
        (
            'Launching the topology edited from "pair" clears dataset test-dataset and '
            "restarts every process. Go on?"
        )
    ]
    assert graph.seeded == [parse_topology(sent.value.post_data_json)]
    expect(button(page, "Edit")).to_be_visible()
    expect(page.locator("#editor")).to_be_hidden()


def test_launch_in_edit_mode_waits_for_the_load_check_to_pass(page: Page, launcher_url):
    open_page(page, launcher_url)
    button(page, "Empty grid").click()

    expect(page.locator("#editor-check")).to_contain_text("at least one module")
    expect(button(page, "Launch")).to_be_disabled()
    expect(page.locator("#editor-base")).to_have_text("Edited from an empty grid")


# -- The discard question --------------------------------------------------------------------- #


def test_without_a_change_the_picker_replaces_the_edit_and_nothing_asks(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    asked = answer_dialogs(page, accept=False)

    picker(page).select_option("tee")

    expect(page.locator("#editor-base")).to_have_text('Edited from "tee"')
    drawn(page, load_topology("tee"))
    assert asked == []


def test_a_changed_edit_asks_before_the_picker_replaces_it_and_cancel_keeps_it(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    edited = grid_modules(page)
    asked = answer_dialogs(page, accept=False)

    picker(page).select_option("tee")

    eventually(lambda: len(asked) == 1, timeout=5)
    assert asked == ['Discard your changes to the topology edited from "pair"?']
    expect(picker(page)).to_have_value("pair")
    expect(page.locator("#editor-base")).to_have_text('Edited from "pair"')
    assert grid_modules(page) == edited


def test_a_changed_edit_that_is_discarded_gives_way_to_the_chosen_topology(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    asked = answer_dialogs(page, accept=True)

    picker(page).select_option("tee")

    expect(page.locator("#editor-base")).to_have_text('Edited from "tee"')
    drawn(page, load_topology("tee"))
    assert asked == ['Discard your changes to the topology edited from "pair"?']


def test_empty_grid_in_edit_mode_asks_and_then_starts_over_from_an_empty_grid(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    asked = answer_dialogs(page, accept=True)

    button(page, "Empty grid").click()

    expect(page.locator("#editor-base")).to_have_text("Edited from an empty grid")
    expect(page.locator("#editor-grid [data-module]")).to_have_count(0)
    assert asked == ['Discard your changes to the topology edited from "pair"?']


def test_close_asks_for_a_changed_edit_and_then_shows_the_chosen_topology(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    refused = answer_dialogs(page, accept=False)

    button(page, "Close").click()

    eventually(lambda: len(refused) == 1, timeout=5)
    assert refused == ['Discard your changes to the topology edited from "pair"?']
    expect(button(page, "Close")).to_be_visible()
    expect(page.locator("#editor-base")).to_have_text('Edited from "pair"')
    expect(page.locator("#editor-grid .cell.empty").first).to_be_visible()


def test_close_after_the_discard_is_accepted_shows_the_chosen_topology(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    asked = answer_dialogs(page, accept=True)

    button(page, "Close").click()

    expect(button(page, "Edit")).to_be_visible()
    drawn(page, load_topology("pair"))
    expect(page.locator("#editor-grid button")).to_have_count(0)
    assert asked == ['Discard your changes to the topology edited from "pair"?']


# -- The running topology in the picker ------------------------------------------------------ #

RUNNING_ENTRY = "pair (edited), running now"


def launch_an_edit_of_pair(page: Page, url: str) -> None:
    open_page(page, url)
    picker(page).select_option("pair")
    press_edit(page)
    change_the_edit(page)
    expect(page.locator("#editor-check")).to_have_text(PASSES)
    launch(page)
    expect(page.locator("#running")).to_have_text("running: pair (edited)")


def test_after_a_launch_of_an_edit_the_picker_holds_and_selects_it_and_the_grid_draws_it(
    page: Page, launcher_url, graph
):
    launch_an_edit_of_pair(page, launcher_url)

    expect(picker(page).locator("option:checked")).to_have_text(RUNNING_ENTRY)
    expect(picker(page).locator("option", has_text="running now")).to_have_count(1)
    drawn(page, graph.seeded[-1])
    expect(page.locator("#editor-grid button")).to_have_count(0)


def test_edit_continues_from_the_running_topology_and_relaunches_it_by_its_base(
    page: Page, launcher_url, graph
):
    launch_an_edit_of_pair(page, launcher_url)
    running = graph.seeded[-1]

    button(page, "Edit").click()

    expect(page.locator("#editor-base")).to_have_text('Edited from "pair (edited)"')
    drawn(page, running)
    expect(page.locator("#editor-check")).to_have_text(PASSES)
    launch(page)
    eventually(lambda: len(graph.seeded) == 2, timeout=5)
    assert graph.seeded[-1] == running
    expect(page.locator("#running")).to_have_text("running: pair (edited)")


def test_launch_of_the_running_entry_outside_edit_mode_relaunches_it_under_its_name(
    page: Page, launcher_url, graph
):
    launch_an_edit_of_pair(page, launcher_url)
    running = graph.seeded[-1]

    launch(page)

    eventually(lambda: len(graph.seeded) == 2, timeout=5)
    assert graph.seeded[-1] == running
    expect(page.locator("#running")).to_have_text("running: pair (edited)")


def test_the_entry_goes_once_a_shipped_topology_runs(page: Page, launcher_url):
    launch_an_edit_of_pair(page, launcher_url)

    picker(page).select_option("tee")
    launch(page)

    expect(page.locator("#running")).to_have_text("running: tee")
    expect(picker(page).locator("option", has_text="running now")).to_have_count(0)
    expect(picker(page)).to_have_value("tee")


def test_when_the_entry_goes_during_an_edit_close_falls_back_to_the_shipped_base(
    page: Page, launcher_url, launcher
):
    launch_an_edit_of_pair(page, launcher_url)
    press_edit(page)
    expect(page.locator("#editor-base")).to_have_text('Edited from "pair (edited)"')

    # Another launch, from elsewhere, puts a shipped topology in its place.
    launcher.launch(load_topology("grid"), name="grid")
    expect(page.locator("#running")).to_have_text("running: grid")
    expect(picker(page).locator("option", has_text="running now")).to_have_count(0)
    expect(page.locator("#editor-base")).to_have_text('Edited from "pair (edited)"')

    button(page, "Close").click()

    expect(picker(page)).to_have_value("pair")
    drawn(page, load_topology("pair"))


def test_a_file_given_at_the_start_has_its_entry_in_the_picker(
    page: Page, launcher, graph
):
    loop = load_topology("tee")
    launcher.launch(loop, name="/somewhere/my-loop.json")
    with serving(create_app(launcher)) as address:
        page.goto(f"{address}/")
        expect(page.locator("#running")).to_have_text(
            "running: /somewhere/my-loop.json"
        )
        expect(picker(page).locator("option", has_text="running now")).to_have_text(
            "/somewhere/my-loop.json, running now"
        )
        picker(page).select_option(label="/somewhere/my-loop.json, running now")
        drawn(page, loop)

        launch(page)
        expect(page.locator("#running")).to_have_text("running: my-loop (edited)")


# -- Running now ------------------------------------------------------------------------------ #


def test_the_running_now_panel_holds_the_running_line_the_strategies_and_the_module_table(
    page: Page, launcher_url
):
    open_page(page, launcher_url)
    picker(page).select_option("pair")
    launch(page)

    panel = page.locator("#running-now")
    expect(panel.get_by_role("heading")).to_contain_text("Running now")
    expect(panel.locator("#running")).to_have_text("running: pair")
    expect(panel.locator("#strategies")).to_contain_text("Module strategy")
    expect(panel.locator("#history")).to_have_count(1)
    expect(panel.locator("#modules tbody tr")).to_have_count(2)
