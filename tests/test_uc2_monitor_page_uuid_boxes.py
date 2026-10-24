"""The monitor page with boxes named by a UUID, `Box-<UUID>`, in a headless browser.

The interim reading: the small disc on a box's
destination, and the box's square on the module that holds it, show the first four characters of
the box's UUID; the box's colour comes from its UUID; the Boxes table orders the boxes by name.

The page is the real one, served by the monitor's real routes over a real memory, which reads the
fakes of `_monitor_fakes.py` with the boxes of each test in place of its own.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from uuid import UUID

import pytest
from _monitor_fakes import FakeReads, FakeStreams
from _serving import serving
from fastapi import FastAPI
from kapps_semantic_middleware.vocabulary import OperationStatus
from playwright.sync_api import Locator, Page, expect

from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import MonitorMemory
from jms_kapps.uc2.monitor.reads import BoxRow
from jms_kapps.uc2.vocabulary import WMS, local_name, module_iri, uuid_box_iri

pytestmark = pytest.mark.browser

M1, M5 = module_iri(1), module_iri(5)

# Twelve UUIDs, each ending in a letter: no digits at the end of a name that a colour could come
# from.
UUIDS = [
    "5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e1a",
    "c7a14e03-2b5d-49f6-8e0a-91d3b6f2a4cb",
    "0a9f77d2-6e41-4b13-b8c5-d2e07a31f9bc",
    "f3a1c2d4-0b5e-4c6f-8a7b-9c0d1e2f3a4d",
    "19e2d3c4-b5a6-4978-8a6b-5c4d3e2f1a0e",
    "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6f",
    "3b4c5d6e-7f8a-4b9c-8d1e-2f3a4b5c6d7a",
    "4c5d6e7f-8a9b-4c0d-9e2f-3a4b5c6d7e8b",
    "5d6e7f8a-9b0c-4d1e-8f3a-4b5c6d7e8f9c",
    "6e7f8a9b-0c1d-4e2f-9a4b-5c6d7e8f9a0d",
    "7f8a9b0c-1d2e-4f3a-8b5c-6d7e8f9a0b1e",
    "8a9b0c1d-2e3f-4a4b-9c6d-7e8f9a0b1c2f",
]


class UuidBoxes(FakeReads):
    """The cross, with each box of `boxes`, named by its UUID, on Module 1 and bound for Module
    5, and one box named by a UUID back in the WMS."""

    def __init__(self, held: list[str]) -> None:
        super().__init__()
        self.held = [uuid_box_iri(UUID(found)) for found in held]

    def boxes(self) -> list[BoxRow]:
        rows = [
            BoxRow(box, OperationStatus.RUNNING, M1, M5, M1) for box in self.held
        ] + [
            BoxRow(
                uuid_box_iri(UUID("00000000-0000-4000-8000-00000000000a")),
                OperationStatus.DONE,
                M1,
                module_iri(4),
                WMS,
            )
        ]
        self._keep("boxes", len(rows))
        return rows


@pytest.fixture
def page_on(page: Page) -> Iterator[Callable[[list[str]], Page]]:
    """Opens the monitor page over the boxes it is given, each named by its UUID."""
    with contextlib.ExitStack() as stack:

        def page_on(held: list[str]) -> Page:
            memory = MonitorMemory(UuidBoxes(held), read_stream=FakeStreams())
            memory.start()
            stack.callback(memory.stop)
            app = FastAPI()
            mount_onto(app, memory=memory)
            address = stack.enter_context(serving(app))
            page.goto(f"{address}/")
            return page

        yield page_on


def drawing(page: Page) -> Locator:
    return page.get_by_role(
        "img", name="The modules, their boxes and each box's destination"
    )


def test_the_disc_and_the_square_of_a_box_show_the_first_four_characters_of_its_uuid(
    page_on,
):
    page = page_on(UUIDS[:1])
    name = local_name(uuid_box_iri(UUID(UUIDS[0])))

    disc = drawing(page).locator("g.destination")
    expect(disc).to_have_count(1)
    expect(disc.locator("text")).to_have_text("5b0e")
    expect(disc.locator("title")).to_have_text(
        f"{name} goes to Module 5, its transport's destination"
    )
    # The four characters stand inside the disc.
    label, circle = (
        disc.locator("text").bounding_box(),
        disc.locator("circle").bounding_box(),
    )
    assert label is not None and circle is not None
    assert circle["x"] <= label["x"]
    assert label["x"] + label["width"] <= circle["x"] + circle["width"]
    square = drawing(page).locator("g.box")
    expect(square.locator("text")).to_have_text("5b0e")
    expect(square.locator("title")).to_have_text(name)
    # The disc, the square's outline and the table's swatch have the box's one colour.
    fill = disc.locator("circle").get_attribute("fill")
    assert fill and square.locator("rect").get_attribute("style") == f"stroke: {fill}"
    row = page.locator("#boxes tbody tr", has_text=name)
    expect(row.locator(".swatch")).to_have_attribute(
        "style", re.compile(rf"background: {re.escape(fill)}")
    )


def test_a_box_s_colour_comes_from_its_uuid_and_stays_from_poll_to_poll(page_on):
    """Twelve boxes whose names end in a letter: their discs have several colours, each box the
    same one at each poll."""
    page = page_on(UUIDS)
    discs = drawing(page).locator("g.destination")
    expect(discs).to_have_count(12)

    def colours() -> dict[str, str]:
        return {
            str(disc.locator("title").text_content()).split()[0]: str(
                disc.locator("circle").get_attribute("fill")
            )
            for disc in discs.all()
        }

    first = colours()
    page.wait_for_timeout(2_500)  # more than two reads of the graph
    expect(discs).to_have_count(12)

    assert colours() == first
    assert len(set(first.values())) >= 3, first


def test_the_boxes_table_orders_the_boxes_by_name(page_on):
    page = page_on([UUIDS[3], UUIDS[0]])

    rows = page.locator("#boxes tbody tr")
    expect(rows).to_have_count(3)
    expect(rows.locator("td:first-child")).to_have_text(
        [
            "Box-00000000-0000-4000-8000-00000000000a",
            f"Box-{UUIDS[0]}",
            f"Box-{UUIDS[3]}",
        ]
    )
