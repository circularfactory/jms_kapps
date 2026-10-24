"""The page of use case 1 in a headless browser: it changes in place and never reloads.

The page is the real one, served by the page's real app. What stands behind the state route is a
list of snapshots that the test changes; nothing reaches a store and no process runs.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from _serving import serving
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.page import build_app

pytestmark = pytest.mark.browser

PLANNER = ChildSnapshot(
    resource=IRI("http://example.org/test#Planner1"),
    name="Planner",
    state="starting",
    pid=4242,
    command="python -m planner",
    output=["first line"],
    address="http://127.0.0.1:9/",
    unconfirmed_for=None,
    stoppable=True,
    why_failed=None,
)


@pytest.fixture
def children() -> list[ChildSnapshot]:
    return [PLANNER]


@pytest.fixture
def page_url(children: list[ChildSnapshot]):
    with serving(build_app(lambda: list(children))) as address:
        yield address + "/"


def row(page: Page, name: str):
    return page.locator("#processes tbody tr:not(.output)").filter(has_text=name)


def test_a_change_of_a_state_appears_without_a_reload(
    page: Page, page_url: str, children: list[ChildSnapshot]
) -> None:
    """A mark set on the window survives the change, which a reload would clear."""
    page.goto(page_url)
    expect(row(page, "Planner").locator(".state")).to_have_text("starting")
    page.evaluate("window.__mark = 1")

    children[0] = replace(PLANNER, state="live")

    expect(row(page, "Planner").locator(".state")).to_have_text("live", timeout=5000)
    assert page.evaluate("window.__mark") == 1
    assert page.locator("meta[http-equiv=refresh]").count() == 0


def test_an_open_output_stays_open_and_follows_the_process(
    page: Page, page_url: str, children: list[ChildSnapshot]
) -> None:
    page.goto(page_url)
    output = page.locator("tr.output")
    expect(output).to_be_hidden()

    row(page, "Planner").get_by_role("button", name="Planner").click()
    expect(output.locator("pre")).to_have_text("first line")

    children[0] = replace(PLANNER, output=["first line", "second line"])

    expect(output.locator("pre")).to_have_text("first line\nsecond line", timeout=5000)
    expect(output).to_be_visible()


def test_a_process_that_gets_its_address_gets_its_links(
    page: Page, page_url: str, children: list[ChildSnapshot]
) -> None:
    children[0] = replace(PLANNER, address=None)
    page.goto(page_url)
    expect(row(page, "Planner").locator("td.links a")).to_have_count(0)

    children[0] = replace(PLANNER, address="http://127.0.0.1:9/")

    expect(row(page, "Planner").locator("td.links a")).to_have_text(
        ["API docs", "activity", "dashboard", "logs"], timeout=5000
    )
