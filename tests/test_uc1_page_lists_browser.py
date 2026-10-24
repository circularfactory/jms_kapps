"""UC1's page's lists, Operations and Learned values, in a headless browser, as a person uses them.

The page is the real one, served by the page's real app. Behind it, the real background read
(`PageLists`) sends the lists' two queries to the stand-in store of `_uc1_lists.py`, which answers
both operations after the two presses and M4's maximum dynamic torque, and keeps each query text
it receives, so a test compares what the page shows and copies with what the store received.
Nothing reaches a real store and no process runs.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _serving import serving
from _uc1_lists import ListStore
from _wait import eventually
from kapps_triplestore_interface import IRI
from playwright.sync_api import Locator, Page, expect

from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.page import build_app
from jms_kapps.uc1.page_lists import LEARNED_VALUES, OPERATIONS, PageLists

pytestmark = pytest.mark.browser

# Records what the page copies, in place of the clipboard, which a headless browser may not
# offer.
RECORD_COPIES = """
Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text) => { window.copied = text; } },
});
"""


def _child(name: str, resource: str, address: str) -> ChildSnapshot:
    return ChildSnapshot(
        resource=IRI(resource),
        name=name,
        state="live",
        pid=1,
        command="python -m x",
        output=[],
        address=address,
        unconfirmed_for=None,
        stoppable=True,
        why_failed=None,
    )


DETECTOR = _child(
    "Anomaly detector", "http://example.org/test#AnomalyDetector1", "http://127.0.0.1:9001/"
)
PLANNER = _child("Planner", "http://example.org/test#Planner1", "http://127.0.0.1:9002/")


@pytest.fixture
def store() -> ListStore:
    return ListStore(str(DETECTOR.resource), str(PLANNER.resource), torque="1.5")


@pytest.fixture
def lists(store: ListStore) -> Iterator[PageLists]:
    reads = PageLists(store, interval=0.1)
    reads.start()
    eventually(lambda: reads.last().learned_values.read_at is not None)
    yield reads
    reads.stop()


@pytest.fixture
def opened(page: Page, lists: PageLists) -> Iterator[Page]:
    with serving(build_app(lambda: [DETECTOR, PLANNER], None, lists.last)) as address:
        page.add_init_script(RECORD_COPIES)
        page.goto(f"{address}/")
        yield page


def section(page: Page, name: str) -> Locator:
    return page.get_by_role("region", name=name)


def rows(page: Page, name: str) -> list[list[str]]:
    return [
        row.locator("td").all_inner_texts()
        for row in section(page, name).locator("tbody tr").all()
    ]


def test_operations_lists_both_operations_each_decided_by_the_anomaly_detector(opened: Page):
    page = opened
    operations = section(page, "Operations")

    expect(operations.locator("tbody tr")).to_have_count(2)
    assert rows(page, "Operations") == [
        ["UnscrewingOperation_b1", "Screw1", "Planner", "done", "ScrewIntact", "", "Anomaly detector"],
        [
            "UnscrewingOperation_a2",
            "Screw2",
            "Planner",
            "failed",
            "MissingScrew",
            "ANOMALY: <MissingScrew>",
            "Anomaly detector",
        ],
    ]
    for link in operations.locator("td.decided-by a").all():
        assert link.get_attribute("href") == "p/AnomalyDetector1/dashboard"


def test_learned_values_follow_the_learner_without_a_reload(opened: Page, store: ListStore):
    """The value at the start stays the first read's; the latest follows the store."""
    page = opened
    expect(section(page, "Learned values").locator("tbody tr")).to_have_count(1)
    assert rows(page, "Learned values") == [["M4", "1.5", "1.5"]]
    page.evaluate("window.__mark = 1")

    store.torque = "1.8"

    expect(section(page, "Learned values").locator("tbody tr td").nth(2)).to_have_text(
        "1.8", timeout=5000
    )
    assert rows(page, "Learned values") == [["M4", "1.5", "1.8"]]
    assert page.evaluate("window.__mark") == 1


@pytest.mark.parametrize(
    ("name", "query"), [("Operations", OPERATIONS), ("Learned values", LEARNED_VALUES)]
)
def test_each_query_shows_and_copies_exactly_as_the_store_received_it(
    opened: Page, store: ListStore, name: str, query: str
):
    page = opened
    shown = section(page, name).get_by_label("The query")

    expect(shown).to_contain_text("SELECT")
    assert shown.text_content() == query
    assert query in store.received

    section(page, name).get_by_role("button", name="Copy the query").click()

    eventually(lambda: page.evaluate("window.copied") == query)
    expect(section(page, name).get_by_role("button", name="Copied")).to_be_visible()


def test_an_open_page_that_polls_sends_no_query(page: Page, store: ListStore):
    """With the background read stopped, three polls of the open page reach the store not
    once: the page shows what the read last found."""
    reads = PageLists(store)
    reads.read()
    sent = len(store.received)
    with serving(build_app(lambda: [DETECTOR, PLANNER], None, reads.last)) as address:
        page.goto(f"{address}/")
        expect(section(page, "Operations").locator("tbody tr")).to_have_count(2)

        for _ in range(3):
            with page.expect_request(lambda request: request.url.endswith("/api/state")):
                pass

    assert len(store.received) == sent


def test_a_read_that_fails_is_shown_with_why_and_the_rows_it_read_last(
    opened: Page, store: ListStore
):
    page = opened
    expect(section(page, "Operations").locator("tbody tr")).to_have_count(2)

    store.away = "the store is away"

    for name in ("Operations", "Learned values"):
        expect(section(page, name)).to_contain_text(
            "The latest read failed: the store is away."
        )
    assert rows(page, "Operations")[0][0] == "UnscrewingOperation_b1"
    assert rows(page, "Learned values") == [["M4", "1.5", "1.5"]]
