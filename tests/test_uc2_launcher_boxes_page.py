"""The Launcher page's list of the boxes on the station, in a headless browser, as a person uses it
(jms_kapps 23).

The page is the real one, served by the Launcher's real app, whose background read sends the list's
one query to the stand-in store of `_station_boxes.py`: it answers Box 1 on Module 1 and a box
named by a UUID on Module 3 (Box 3 until 2026-10-12, jms_kapps 92), and keeps each query text it
receives, so a test compares what the page shows and copies with what the store received. Nothing
reaches a real store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _serving import serving
from _station_boxes import StandInStore, listing
from _wait import eventually
from playwright.sync_api import Locator, Page, expect

from jms_kapps.uc2.launcher.api import create_app

pytestmark = pytest.mark.browser

# Records what the page copies, in place of the clipboard, which a headless browser may not
# offer.
RECORD_COPIES = """
Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text) => { window.copied = text; } },
});
"""


@pytest.fixture
def store() -> StandInStore:
    return StandInStore()


@pytest.fixture
def opened(page: Page, store) -> Iterator[Page]:
    """The Launcher page, once the Launcher's first read of the boxes has reached the store."""
    with serving(create_app(listing(store))) as address:
        eventually(lambda: len(store.received) == 1)
        page.add_init_script(RECORD_COPIES)
        page.goto(f"{address}/")
        yield page


def station(page: Page) -> Locator:
    """The page's section of the boxes on the station."""
    return page.get_by_role("region", name="Boxes on the station")


def box_rows(page: Page) -> list[list[str]]:
    """The cells of each row of the list, as a person reads them."""
    rows = station(page).locator("tbody tr")
    return [row.locator("td").all_inner_texts() for row in rows.all()]


def test_the_page_lists_each_box_with_the_module_that_possesses_it(opened: Page):
    page = opened

    expect(station(page).locator("tbody tr")).to_have_count(2)
    assert box_rows(page) == [
        ["Box1", "Module1", "Transport7", "running"],
        # A box named by a UUID, whole (jms_kapps 92).
        ["Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10", "Module3", "Transport9", "queued"],
    ]


def test_the_page_shows_the_query_exactly_as_the_store_received_it(opened: Page, store):
    page = opened
    query = station(page).get_by_label("The query")

    expect(query).to_contain_text("SELECT")

    assert query.text_content() == store.received[-1]


def test_the_copy_button_copies_the_query_exactly_as_the_store_received_it(
    opened: Page, store
):
    page = opened

    station(page).get_by_role("button", name="Copy the query").click()

    eventually(lambda: page.evaluate("window.copied") == store.received[-1])
    expect(station(page).get_by_role("button", name="Copied")).to_be_visible()


def test_an_open_page_that_polls_sends_no_query(opened: Page, store):
    """Three polls of the open page, and the store has received only the Launcher's first read:
    the page's polls are answered from the Launcher's memory."""
    page = opened
    expect(station(page).locator("tbody tr")).to_have_count(2)

    for _ in range(3):
        with page.expect_request(lambda request: request.url.endswith("/api/state")):
            pass

    assert len(store.received) == 1


def test_a_read_that_fails_is_shown_with_why_and_the_list_it_read_last(
    page: Page, store
):
    """A person sees that the list is the last one read, and why no newer one came."""
    with serving(create_app(listing(store, interval=0.1))) as address:
        page.goto(f"{address}/")
        expect(station(page).locator("tbody tr")).to_have_count(2)

        store.away = "the store is away"

        expect(station(page)).to_contain_text(
            "The latest read failed: the store is away."
        )
        assert box_rows(page)[0] == ["Box1", "Module1", "Transport7", "running"]
