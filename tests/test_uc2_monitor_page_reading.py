"""The monitor page's switch, the interval of its graph read, its "Queries sent" panel and each
box's destination, in a headless browser, as a person uses them.

The page is the real one, served by the monitor's real routes over a real memory, whose background
task runs. It reads the fakes of `_monitor_fakes.py`, so nothing reaches a store: the cross, Box 1
on Module 1, its transport running toward Module 5, and Box 2 back in the WMS. Module 1 and the WMS
serve a handover event stream, and Module 1 has refused one write on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from _monitor_fakes import STREAM_1, STREAM_WMS, FakeReads, FakeStreams, refuse
from _serving import serving
from _wait import eventually
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, expect

from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import GRAPH_AND_STREAMS, GRAPH_ONLY, MonitorMemory
from jms_kapps.uc2.vocabulary import WMS, module_iri

pytestmark = pytest.mark.browser

M1 = module_iri(1)

# Records what the page copies, in place of the clipboard, which a headless browser may not
# offer.
RECORD_COPIES = """
Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text) => { window.copied = text; } },
});
"""


@pytest.fixture
def reads() -> FakeReads:
    return FakeReads()


@pytest.fixture
def streams() -> FakeStreams:
    streams = FakeStreams()
    refuse(streams.events[STREAM_1])
    return streams


@pytest.fixture
def memory(reads, streams) -> Iterator[MonitorMemory]:
    memory = MonitorMemory(reads, read_stream=streams)
    memory.start()
    yield memory
    memory.stop()


@pytest.fixture
def monitor_url(memory) -> Iterator[str]:
    app = FastAPI()
    mount_onto(app, memory=memory)
    with serving(app) as address:
        yield f"{address}/"


@pytest.fixture
def opened(page: Page, monitor_url) -> Page:
    page.add_init_script(RECORD_COPIES)
    page.goto(monitor_url)
    return page


def refusal_cards(page: Page) -> Locator:
    return page.locator("#refusals article.refusal")


def test_the_switch_stops_the_streams_and_switching_back_resumes_them(
    opened: Page, memory
):
    page = opened
    follow = page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    )
    expect(follow).to_be_checked()
    expect(refusal_cards(page)).to_have_count(1)

    follow.uncheck()

    eventually(lambda: memory.mode == GRAPH_ONLY)
    eventually(lambda: memory.following() == {})
    expect(follow).not_to_be_checked()
    expect(refusal_cards(page)).to_have_count(0)

    follow.check()

    eventually(lambda: memory.mode == GRAPH_AND_STREAMS)
    eventually(lambda: memory.following() == {M1: STREAM_1, WMS: STREAM_WMS})
    expect(refusal_cards(page)).to_have_count(1)


def test_the_streams_switch_heads_refusals_and_the_first_section_holds_only_the_interval(
    opened: Page,
):
    page = opened
    follow = page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    )

    expect(
        page.locator("#refusals-section h2 + .toolbar").get_by_role("checkbox")
    ).to_have_count(1)
    expect(follow).to_have_count(1)
    expect(page.locator("#reading").get_by_role("checkbox")).to_have_count(0)
    expect(page.locator("#reading").get_by_role("radio")).to_have_count(0)
    expect(page.locator("#reading input")).to_have_count(1)
    expect(page.locator("#deadlock").locator("xpath=..")).to_contain_text(
        "the notice follows the switch \"Follow the services' handover event streams\" at Refusals"
    )


def test_the_switch_shows_the_mode_the_monitor_runs_in_when_the_page_is_opened_directly(
    page: Page, reads, streams
):
    memory = MonitorMemory(reads, read_stream=streams, mode=GRAPH_ONLY)
    memory.start()
    try:
        app = FastAPI()
        mount_onto(app, memory=memory)
        with serving(app) as address:
            page.goto(f"{address}/")
            follow = page.get_by_role(
                "checkbox", name="Follow the services' handover event streams"
            )
            expect(follow).to_be_enabled()
            expect(follow).not_to_be_checked()
    finally:
        memory.stop()


def test_in_graph_only_the_refusals_view_says_why_it_is_empty(opened: Page):
    page = opened
    expect(refusal_cards(page)).to_have_count(1)

    page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    ).uncheck()

    refusals = page.locator("#refusals")
    expect(refusals).to_contain_text(
        "a refused write leaves no trace in the knowledge graph"
    )
    expect(refusals).to_contain_text('glossary entry "Handover event stream"')
    expect(refusals).to_contain_text(
        'turn on "Follow the services\' handover event streams" to see the refusals'
    )

    page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    ).check()

    expect(refusal_cards(page)).to_have_count(1)
    expect(refusals).not_to_contain_text("leaves no trace")


def test_a_refusal_that_comes_back_with_the_streams_is_not_highlighted_as_new(
    opened: Page,
):
    page = opened
    expect(refusal_cards(page)).to_have_count(1)
    page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    ).uncheck()
    expect(refusal_cards(page)).to_have_count(0)

    page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    ).check()

    expect(refusal_cards(page)).to_have_count(1)
    expect(refusal_cards(page)).not_to_have_class(re.compile(r"\bfresh\b"))


def test_the_interval_a_person_sets_applies(opened: Page, memory):
    page = opened
    interval = page.get_by_label("Read the knowledge graph every")
    expect(interval).to_have_value("1")

    interval.fill("2")
    interval.press("Enter")

    eventually(lambda: memory.graph_seconds == 2.0)
    expect(
        page.locator('#queries article.query[data-read="boxes"] .every')
    ).to_have_text("every 2 s")


def test_an_interval_out_of_range_is_refused_on_the_page_with_the_monitors_reason(
    opened: Page, memory
):
    page = opened
    interval = page.get_by_label("Read the knowledge graph every")

    interval.fill("20")
    interval.press("Enter")

    expect(page.locator("#reading-message")).to_contain_text("0.5 s to 10 s")
    assert memory.graph_seconds == 1.0
    # The field shows the interval that applies again, once a person leaves it.
    interval.blur()
    expect(interval).to_have_value("1")


def test_the_queries_sent_panel_shows_each_query_with_its_interval_duration_and_rows(
    opened: Page,
):
    page = opened
    queries = page.locator("#queries article.query")
    expect(queries).to_have_count(5)
    modules = page.locator('#queries article.query[data-read="modules"]')

    expect(modules.locator("pre")).to_have_text("SELECT ... # modules")
    expect(modules.locator(".every")).to_have_text("every 1 s")
    expect(modules.locator(".last")).to_contain_text("1 ms")
    expect(modules.locator(".last")).to_contain_text("5 rows")
    streams = page.locator('#queries article.query[data-read="streams"]')
    expect(streams.locator(".every")).to_have_text("every 5 s")

    page.get_by_role(
        "checkbox", name="Follow the services' handover event streams"
    ).uncheck()

    expect(streams.locator(".every")).to_have_text("not sent in graph only")
    expect(modules.locator(".every")).to_have_text("every 1 s")


def test_the_copy_button_copies_the_exact_text_of_its_query(opened: Page):
    page = opened
    boxes = page.locator('#queries article.query[data-read="boxes"]')

    boxes.get_by_role("button", name="Copy").click()

    eventually(lambda: page.evaluate("window.copied") == "SELECT ... # boxes")
    expect(boxes.get_by_role("button", name="Copied")).to_be_visible()


def square(drawing: Locator, label: str) -> Locator:
    """The square of the module `label`: the one that names it to a person who points at it."""
    named = drawing.page.locator("title", has_text=re.compile(rf"^{label}:"))
    return drawing.locator("g").filter(has=named).locator("rect.frame")


def test_a_box_on_a_module_shows_its_destination_and_no_path_to_it(opened: Page):
    """Box 1 is on Module 1, its transport running toward Module 5: a disc with its number stands
    on Module 5. Box 2 is back in the WMS, so it is on no module, and marks none. The monitor
    computes no route, so no path and no strong hop is drawn."""
    page = opened
    drawing = page.get_by_role(
        "img", name="The modules, their boxes and each box's destination"
    )
    destination = drawing.locator("g.destination")
    expect(destination).to_have_count(1)
    expect(destination.locator("text")).to_have_text("1")
    expect(destination.locator("title")).to_have_text(
        "Box1 goes to Module 5, its transport's destination"
    )

    disc = destination.locator("circle").bounding_box()
    module_5 = square(drawing, "Module 5").bounding_box()
    assert disc is not None and module_5 is not None
    centre = (disc["x"] + disc["width"] / 2, disc["y"] + disc["height"] / 2)
    assert module_5["x"] < centre[0] < module_5["x"] + module_5["width"]
    assert module_5["y"] < centre[1] < module_5["y"] + module_5["height"]
    assert drawing.locator("polyline, line.hop").count() == 0
    expect(page.locator("#boxes thead")).not_to_contain_text("Path ahead")
