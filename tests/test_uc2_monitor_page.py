"""The monitor page's JavaScript, in a headless browser: the arrowheads of its drawing.

The page is the real one, served by the monitor's own routes, except its state route: a fake that
answers one state, and reads nothing. Six modules, every one live, with no box, joined by these
connections (`fc:connectsTo`), and a wire between each two that a connection joins, with the way
its connections lead:

    Module 1 → Module 2 ↔ Module 3 ← Module 4
               ↓          ↑
               Module 5   Module 6

A test reads the drawing as a person sees it: from a picture of it, along the gap between two
modules' squares, where a wire runs and where its heads stand. A head is wider than its wire.

The connections are all a wire draws: the monitor computes no route, so no hop is drawn strong and
no box has a path ahead. Each box's destination, and the switch, the interval and "Queries sent",
are tested in `test_uc2_monitor_page_reading.py`. The monitor maps destinations but not routes.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator

import pytest
from _serving import serving
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, expect

from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.vocabulary import module_iri

pytestmark = [
    pytest.mark.browser,
    # The picture is read in pixels: one pixel to each of the page's, at the width the page is
    # drawn at here, whatever device the options of pytest-playwright name.
    pytest.mark.browser_context_args(
        viewport={"width": 1280, "height": 720}, device_scale_factor=1
    ),
]

# Where each module stands in the drawing, and its connections, each to a module by number.
PLACES = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0), 5: (1, 1), 6: (2, 1)}
CONNECTIONS = {
    1: [("East", 2)],
    2: [("East", 3), ("South", 5)],
    3: [("West", 2)],
    4: [("West", 3)],
    5: [],
    6: [("North", 3)],
}


def _module(n: int) -> dict:
    return {
        "id": str(module_iri(n)),
        "label": f"Module {n}",
        "type": "FlexConveyorModule",
        "place": list(PLACES[n]),
        "live": True,
        "boxes": [],
        "holds_box": False,
        "connections": [
            {
                "direction": direction,
                "to": str(module_iri(to)),
                "to_label": f"Module {to}",
                "port": 0,
            }
            for direction, to in CONNECTIONS[n]
        ],
    }


def _wire(a: int, b: int) -> dict:
    """The wire between modules `a` and `b`, leading to each that a connection leads to."""
    return {
        "between": [str(module_iri(a)), str(module_iri(b))],
        "leads_to": [
            str(module_iri(to))
            for frm, to in ((b, a), (a, b))
            if to in (target for _, target in CONNECTIONS[frm])
        ],
    }


STATE = {
    "error": None,
    "modules": [_module(n) for n in PLACES],
    "wires": [_wire(1, 2), _wire(2, 3), _wire(3, 4), _wire(2, 5), _wire(3, 6)],
    "boxes": [],
    "handovers": [],
    "refusals": [],
    "reading": {
        "mode": "graph + streams",
        "modes": ["graph + streams", "graph only"],
        "graph_seconds": 1.0,
        "shortest_graph_seconds": 0.5,
        "longest_graph_seconds": 10.0,
    },
    "queries": [],
}


class NoMemory:
    """In place of the monitor's memory, which the fake state route stands in for."""

    def snapshot(self) -> None:
        raise AssertionError("the page's state comes from the fake state route")


@pytest.fixture
def monitor_url() -> Iterator[str]:
    app = FastAPI()

    # Before the monitor's own routes, so that this one answers the page's polls.
    @app.get("/api/state")
    def state() -> dict:
        return STATE

    mount_onto(app, memory=NoMemory())
    with serving(app) as address:
        yield f"{address}/"


def square(drawing: Locator, label: str) -> Locator:
    """The square of the module `label`: the one that names it to a person who points at it."""
    named = drawing.page.locator("title", has_text=re.compile(rf"^{label}:"))
    return drawing.locator("g").filter(has=named).locator("rect.frame")


# How the picture is read, in a page of its own: for each gap between two squares, how many pixels
# are drawn on across the gap, at each step along it. A pixel is drawn on where its colour is not
# the paper's, which the picture's top-left corner shows.
_INK_ALONG_GAPS = """async ({ png, gaps }) => {
    const image = new Image();
    image.src = `data:image/png;base64,${png}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const { data, width } = context.getImageData(0, 0, image.width, image.height);
    const inked = (x, y) => {
        const i = (y * width + x) * 4;
        return [0, 1, 2].some((c) => Math.abs(data[i + c] - data[c]) > 24);
    };
    return gaps.map(({ along, start, end, across }) => {
        const profile = [];
        for (let t = start; t < end; t++) {
            let count = 0;
            for (let s = across[0]; s < across[1]; s++) {
                if (along === "x" ? inked(t, s) : inked(s, t)) count++;
            }
            profile.push(count);
        }
        return profile;
    });
}"""

# A square's bounding box takes in its stroke; this much more stays clear of its anti-aliasing.
_CLEAR = 2


def _gap(first: dict, second: dict, origin: dict) -> tuple[dict, bool]:
    """The gap between two squares that stand side by side or one above the other, in the
    picture's pixels, and whether it runs from the second to the first."""

    def edges(box: dict, axis: str) -> tuple[float, float]:
        low = box[axis] - origin[axis]
        return low, low + box["width" if axis == "x" else "height"]

    across_axis = "y" if abs(first["y"] - second["y"]) < first["height"] / 2 else "x"
    along = "x" if across_axis == "y" else "y"
    (a_low, a_high), (b_low, b_high) = edges(first, along), edges(second, along)
    reverse = b_high <= a_low
    start, end = (b_high, a_low) if reverse else (a_high, b_low)
    # Squares that do not stand next to each other leave no gap to read, and nothing would be seen.
    assert end - start > 4 * _CLEAR, "the two squares do not stand next to each other"
    across = edges(first, across_axis)
    return {
        "along": along,
        "start": round(start) + _CLEAR,
        "end": round(end) - _CLEAR,
        "across": [round(across[0]), round(across[1])],
    }, reverse


def wires_between(
    drawing: Locator, pairs: list[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """What a person sees between the squares of each pair of modules, as `_heads` writes it:
    "-->" for a wire with a head that points at the second."""
    page = drawing.page
    origin = drawing.bounding_box()
    gaps = [
        _gap(
            square(drawing, first).bounding_box(),
            square(drawing, second).bounding_box(),
            origin,
        )
        for first, second in pairs
    ]
    png = base64.b64encode(drawing.screenshot()).decode()
    reader = page.context.new_page()
    try:
        profiles = reader.evaluate(
            _INK_ALONG_GAPS, {"png": png, "gaps": [gap for gap, _ in gaps]}
        )
    finally:
        reader.close()
    return {
        pair: _heads(profile[::-1] if reverse else profile)
        for pair, (_, reverse), profile in zip(pairs, gaps, profiles, strict=True)
    }


def _heads(profile: list[int]) -> str:
    """The wire along a gap, from the first square to the second, as text: "" where nothing is
    drawn, "--" for a wire, with a head at an end as "<" or ">", whichever way it points.

    Mid-gap the wire is drawn alone. A head is at least twice as wide, and stands in the half of
    the gap at its end: a head that points at the square there has its base towards mid-gap, and
    narrows to its tip at the square, so it is wider where it begins to stand out from the wire
    than where it ends.
    """
    if not any(profile):
        return ""
    half = len(profile) // 2
    wire = profile[half]

    def points_at_the_square(towards_the_square: list[int]) -> bool | None:
        if max(towards_the_square) < 2 * wire:
            return None
        wider = [width for width in towards_the_square if width > wire]
        return wider[0] > wider[-1]

    at_first = points_at_the_square(profile[:half][::-1])
    at_second = points_at_the_square(profile[half:])
    return (
        {None: "", True: "<", False: ">"}[at_first]
        + "--"
        + {None: "", True: ">", False: "<"}[at_second]
    )


def test_each_wire_has_a_head_where_a_connection_leads_and_two_where_they_lead_both_ways(
    page: Page, monitor_url
):
    page.goto(monitor_url)
    drawing = page.get_by_role(
        "img", name="The modules, their boxes and each box's destination"
    )
    for n in PLACES:
        expect(square(drawing, f"Module {n}")).to_be_visible()

    assert wires_between(
        drawing,
        [
            ("Module 1", "Module 2"),
            ("Module 2", "Module 3"),
            ("Module 3", "Module 4"),
            ("Module 2", "Module 5"),
            ("Module 3", "Module 6"),
            ("Module 5", "Module 6"),
        ],
    ) == {
        ("Module 1", "Module 2"): "-->",
        ("Module 2", "Module 3"): "<-->",
        ("Module 3", "Module 4"): "<--",
        ("Module 2", "Module 5"): "-->",
        ("Module 3", "Module 6"): "<--",
        # No connection joins them, so no wire is drawn between them.
        ("Module 5", "Module 6"): "",
    }


def test_each_module_is_drawn_from_above_with_a_green_frame_rollers_and_belts(
    page: Page, monitor_url
):
    """The look of a real FlexConveyor module (the module top view): a green frame, silver motor rollers
    that lie east-west, dark belt strips between them, and no light barrier drawn."""
    page.goto(monitor_url)
    drawing = page.get_by_role(
        "img", name="The modules, their boxes and each box's destination"
    )
    expect(drawing.locator("rect.frame")).to_have_count(len(PLACES))
    expect(drawing.locator("rect.roller")).to_have_count(6 * len(PLACES))
    expect(drawing.locator("rect.belt")).to_have_count(5 * len(PLACES))
    # A roller is wider than it is high, so it lies east-west; a belt strip lies between two.
    roller = drawing.locator("rect.roller").first.bounding_box()
    assert roller is not None and roller["width"] > 4 * roller["height"]
    frame = square(drawing, "Module 1")
    assert frame.evaluate("e => getComputedStyle(e).stroke") == "rgb(63, 174, 73)"
    assert drawing.locator("[class*=barrier]").count() == 0


def test_an_occupied_module_has_a_tinted_ground_and_an_offline_one_is_grey_and_dashed(
    page: Page, monitor_url
):
    """Module 1 holds a box and Module 2 is offline: the ground of the first is tinted, the frame
    of the second is grey and dashed."""
    STATE["modules"][0]["holds_box"] = True
    STATE["modules"][0]["boxes"] = ["Box1"]
    STATE["modules"][1]["live"] = False
    try:
        page.goto(monitor_url)
        drawing = page.get_by_role(
            "img", name="The modules, their boxes and each box's destination"
        )
        occupied = square(drawing, "Module 1")
        offline = square(drawing, "Module 2")
        expect(occupied).to_be_visible()
        assert (
            occupied.evaluate("e => getComputedStyle(e).fill") == "rgb(219, 233, 248)"
        )
        assert offline.evaluate("e => getComputedStyle(e).strokeDasharray") != "none"
        assert (
            offline.evaluate("e => getComputedStyle(e).stroke") == "rgb(138, 138, 138)"
        )
        expect(drawing.locator("g.box")).to_have_count(1)
        expect(drawing.locator("g.name text").first).to_be_visible()
    finally:
        STATE["modules"][0]["holds_box"] = False
        STATE["modules"][0]["boxes"] = []
        STATE["modules"][1]["live"] = True
