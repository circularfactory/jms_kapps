"""The Launcher page's Clear, in a headless browser, as a person uses it.

The page is the real one, served by the Launcher's real app. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records each clear, answers the statements it leaves
or fails as told, and calls no service live unless told to; every process only sleeps.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Collection, Iterator

import pytest
from _services import live_services
from _serving import serving
from kapps_triplestore_interface import IRI
from playwright.sync_api import Dialog, Page, Request, expect

from jms_kapps.common.services import Service
from jms_kapps.common.triplestore import ClearFailed
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import module_iri

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

STORE = "http://store.example:7200"

QUESTION = f"Clear stops every process and empties dataset test-dataset on {STORE}. Go on?"


class Graph:
    """Records each clear, and answers `left` statements, or fails with `refusal`; a clear waits
    while `held` is clear. Calls each resource of `live` live, and none by default."""

    def __init__(self) -> None:
        self.cleared = 0
        self.left = 0
        self.refusal: str | None = None
        self.live: set[IRI] = set()
        self.held = threading.Event()
        self.held.set()

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        pass

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return live_services(self.live, workflows)

    def clear(self) -> int:
        self.held.wait(timeout=30)
        self.cleared += 1
        if self.refusal is not None:
            raise ClearFailed(self.refusal)
        return self.left


@pytest.fixture
def graph() -> Graph:
    return Graph()


@pytest.fixture
def launcher(graph, monkeypatch) -> Iterator[Launcher]:
    """A Launcher that runs the pair on `test-dataset` of the store at `STORE`."""
    monkeypatch.setenv("TRIPLESTORE_URL", STORE)
    launcher = Launcher(
        graph=graph,
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=lambda line: None,
        stop_timeout=5.0,
    )
    launcher.launch(load_topology("pair"), name="pair")
    try:
        yield launcher
    finally:
        graph.held.set()
        launcher.stop_all()


@pytest.fixture
def url(launcher) -> Iterator[str]:
    with serving(create_app(launcher)) as address:
        yield f"{address}/"


def opened(page: Page, url: str) -> None:
    """Open the page, and wait until it shows the running topology."""
    page.goto(url)
    expect(page.locator("#running")).to_have_text("running: pair")


def press_clear(page: Page, answer: str = "accept") -> list[str]:
    """Press Clear, and answer its question with `answer`, `accept` or `dismiss`. Returns each
    question the page asked."""
    asked: list[str] = []

    def answered(dialog: Dialog) -> None:
        asked.append(dialog.message)
        getattr(dialog, answer)()

    page.once("dialog", answered)
    page.get_by_role("button", name="Clear", exact=True).click()
    return asked


def test_the_clear_button_is_beside_stop_all(page: Page, url):
    opened(page, url)

    expect(page.locator("header button")).to_have_text(["Stop all", "Clear"])


def test_clear_asks_first_and_a_no_leaves_everything_as_it_was(
    page: Page, url, graph, launcher
):
    opened(page, url)
    sent: list[Request] = []
    page.on(
        "request",
        lambda request: sent.append(request) if "/api/clear" in request.url else None,
    )

    asked = press_clear(page, "dismiss")

    assert asked == [QUESTION]
    # Two polls later, still nothing was sent, cleared or stopped.
    for _ in range(2):
        with page.expect_request(lambda request: request.url.endswith("/api/state")):
            pass
    assert sent == []
    assert graph.cleared == 0
    assert all(p.child.stoppable for p in launcher.state().processes)
    expect(page.locator("#running")).to_have_text("running: pair")


def test_clear_after_a_yes_says_the_dataset_is_empty_and_every_process_stopped(
    page: Page, url, graph
):
    opened(page, url)

    asked = press_clear(page)

    assert asked == [QUESTION]
    message = page.locator("#command-message")
    expect(message).to_have_text("test-dataset is empty: 0 statements")
    expect(message).to_have_class(re.compile(r"\bok\b"))
    assert graph.cleared == 1
    expect(page.locator("#processes tbody tr")).to_have_count(4)
    for row in page.locator("#processes tbody tr").all():
        expect(row).to_have_class("stopped")
    expect(page.locator("#running")).to_have_text("")
    expect(page.locator("#modules tbody")).to_have_text("Nothing is seeded.")


def test_while_it_clears_the_page_says_so_and_takes_no_second_clear_or_launch(
    page: Page, url, graph
):
    opened(page, url)
    graph.held.clear()

    press_clear(page)

    message = page.locator("#command-message")
    expect(message).to_have_text(
        "Clearing: stopping every process, then emptying test-dataset graph by graph…"
    )
    clear = page.get_by_role("button", name="Clear", exact=True)
    expect(clear).to_be_disabled()
    expect(page.get_by_role("button", name="Launch", exact=True)).to_be_disabled()

    graph.held.set()

    expect(message).to_have_text("test-dataset is empty: 0 statements")
    expect(clear).to_be_enabled()
    expect(page.get_by_role("button", name="Launch", exact=True)).to_be_enabled()


def test_statements_the_store_still_holds_are_said_as_an_error(page: Page, url, graph):
    opened(page, url)
    graph.left = 3

    press_clear(page)

    message = page.locator("#command-message")
    expect(message).to_have_text("test-dataset is not empty: 3 statements")
    expect(message).to_have_class(re.compile(r"\berror\b"))


def test_a_clear_the_guard_refuses_shows_why_in_the_page(page: Page, url, graph):
    """Another launcher's process is live in the dataset: the page says which, and that
    nothing was cleared, in its own words."""
    opened(page, url)
    graph.live = {module_iri(9)}

    press_clear(page)

    message = page.locator("#command-message")
    expect(message).to_have_text(
        re.compile(
            r"^Not cleared: Module9 carries a fresh heartbeat in test-dataset, so "
            r"nothing was cleared\. No process of this launcher serves it: another launcher "
            r"runs there"
        )
    )
    expect(message).to_have_class(re.compile(r"\berror\b"))
    assert graph.cleared == 0


def test_a_graph_the_store_refuses_to_clear_shows_why_in_the_page(
    page: Page, url, graph
):
    opened(page, url)
    graph.refusal = (
        "the triple store did not clear the graph <http://example.org/notes> of "
        "test-dataset: the store is busy"
    )

    press_clear(page)

    message = page.locator("#command-message")
    expect(message).to_have_text(f"Not cleared: {graph.refusal}")
    expect(message).to_have_class(re.compile(r"\berror\b"))


def test_without_the_store_s_address_the_question_names_the_dataset(
    page: Page, url, monkeypatch
):
    monkeypatch.delenv("TRIPLESTORE_URL")
    opened(page, url)

    asked = press_clear(page, "dismiss")

    assert asked == ["Clear stops every process and empties dataset test-dataset. Go on?"]
