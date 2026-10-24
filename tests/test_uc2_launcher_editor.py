"""The Launcher page's topology editor, in a headless browser, as a person clicks it.

The page is the real one, served by the Launcher's real app, so the shipped topologies, the
layout, the load check and the launch are the server's own. What stands behind the Launcher is
fake, and nothing reaches a store: the graph records each topology it is given to seed and calls
no service live, and every process only sleeps.

Each change in the editor sends the module list it now holds to the load check, so a test reads
what the editor holds from that request: the module list, the one topology format. After each
change, the page shows under the grid what the check answered to that request. A test saves the
file that Download writes, and chooses the file that Upload opens, as a person does.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

import pytest
from _serving import serving
from _topologies import FIRST_ITERATIONS_CROSS, module_entry
from _wait import eventually
from kapps_triplestore_interface import IRI
from playwright.sync_api import Dialog, Locator, Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import STATIC, create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import (
    CONNECTION_KEY,
    LABEL_KEY,
    TOPOLOGY_FORMAT,
    TRANSIT_TIME_KEY,
    TYPE_KEY,
    Topology,
    load_topology,
    parse_topology,
)
from jms_kapps.uc2.vocabulary import FC_NS, FCI_NS

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# What the page says under the grid while the load check passes.
PASSES = "The load check passes."
# What it says of a topology with no module: an empty grid fails the load check.
NO_MODULE = "The load check refuses this topology: a topology needs at least one module"


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


def module_list_after(page: Page, change: Callable[[], object]) -> list[dict]:
    """Make `change` in the editor, and return the module list the page then sent to the load
    check: what the editor holds after it. Fails unless the page then shows under the grid what
    the check answered."""
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/api/topology/check")
        )
    ) as answered:
        change()
    answer = answered.value.json()
    expect(check_result(page)).to_have_text(
        PASSES
        if answer["passes"]
        else f"The load check refuses this topology: {answer['reason']}"
    )
    return answered.value.request.post_data_json


def edit(page: Page, url: str, name: str) -> list[dict]:
    """Open the page at `url`, pick the shipped topology `name`, and open the editor from it.
    Returns the module list the editor holds then."""
    page.goto(url)
    page.get_by_label("Topology to launch").select_option(name)
    return module_list_after(
        page, page.get_by_role("button", name="Edit", exact=True).click
    )


def cell(page: Page, x: int, y: int) -> Locator:
    """The cell of the editor's grid at column `x` and row `y`, where the modules' cells begin at
    0, 0. The grid has an empty row and column around them."""
    return page.locator(f'#editor-grid [data-cell="{x},{y}"]')


def module(page: Page, id: str) -> Locator:
    """The cell of the module `id` on the editor's grid."""
    return page.locator(f'#editor-grid [data-module="{id}"]')


def edge(page: Page, west_or_north: str, east_or_south: str) -> Locator:
    """The edge between two neighbouring modules on the editor's grid."""
    return page.locator(f'#editor-grid [data-edge="{west_or_north} {east_or_south}"]')


def check_result(page: Page) -> Locator:
    """What the page says under the grid of the load check."""
    return page.locator("#editor-check")


def launch_this(page: Page) -> Locator:
    return page.get_by_role("button", name="Launch", exact=True)


def read_back(
    module_list: list[dict],
) -> list[tuple[str, list[str], list[tuple[str, str]]]]:
    """The modules of `module_list`, each as its id, its types and its connections, as the
    server's reader reads them."""
    return [
        (m.id, m.types, [(c.direction, c.to) for c in m.connections])
        for m in parse_topology(module_list).modules
    ]


def test_edit_draws_the_chosen_shipped_topology_where_the_layout_puts_it_and_changes_nothing(
    page: Page, launcher_url
):
    sent = edit(page, launcher_url, "tee")

    assert sent == load_topology("tee").module_list()
    for id, place in {
        "Module1": "0,0",
        "Module2": "0,1",
        "Module3": "1,1",
        "Module4": "0,2",
    }.items():
        expect(module(page, id)).to_have_attribute("data-cell", place)
    expect(edge(page, "Module1", "Module2")).to_have_text("↓")
    expect(edge(page, "Module2", "Module3")).to_have_text("→")
    expect(edge(page, "Module2", "Module4")).to_have_text("↓")
    expect(page.locator("#editor-base")).to_have_text('Edited from "tee"')
    expect(check_result(page)).to_have_text(PASSES)


def test_a_click_on_an_empty_cell_adds_a_module_named_in_order(
    page: Page, launcher_url
):
    edit(page, launcher_url, "pair")

    sent = module_list_after(page, cell(page, 1, 1).click)

    assert sent[2] == {
        "id": FCI_NS + "Module3",
        TYPE_KEY: [FC_NS + "FlexConveyorModule"],
        LABEL_KEY: ["Module 3"],
        CONNECTION_KEY: [],
    }
    expect(module(page, "Module3")).to_have_attribute("data-cell", "1,1")
    expect(module(page, "Module3")).to_have_text("Module 3")
    # Neighbours, with no connection yet.
    expect(edge(page, "Module2", "Module3")).to_have_text("·")

    # Left of every module: the grid grows to the West, and each module keeps its neighbours.
    sent = module_list_after(page, cell(page, -1, 0).click)

    assert [m["id"] for m in sent] == [
        FCI_NS + "Module1",
        FCI_NS + "Module2",
        FCI_NS + "Module3",
        FCI_NS + "Module4",
    ]
    expect(module(page, "Module4")).to_have_attribute("data-cell", "0,0")
    expect(module(page, "Module1")).to_have_attribute("data-cell", "1,0")
    expect(module(page, "Module3")).to_have_attribute("data-cell", "2,1")
    expect(edge(page, "Module1", "Module2")).to_have_text("↓")

    # The module added last is selected, and taking it out takes its column with it.
    module_list_after(page, page.get_by_role("button", name="Remove", exact=True).click)

    expect(module(page, "Module1")).to_have_attribute("data-cell", "0,0")
    expect(module(page, "Module3")).to_have_attribute("data-cell", "1,1")


def test_a_click_on_a_module_selects_it_and_remove_takes_it_out_with_every_connection_to_it(
    page: Page, launcher_url
):
    edit(page, launcher_url, "tee")

    module(page, "Module2").click()

    expect(module(page, "Module2")).to_have_attribute("aria-pressed", "true")
    expect(module(page, "Module1")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#editor-selected")).to_contain_text("Module 2")

    sent = module_list_after(
        page, page.get_by_role("button", name="Remove", exact=True).click
    )

    assert read_back(sent) == [
        ("Module1", ["EntryModule"], []),
        ("Module3", ["ExitModule"], []),
        ("Module4", ["ExitModule"], []),
    ]
    expect(module(page, "Module2")).to_have_count(0)
    expect(cell(page, 0, 1)).to_have_attribute("aria-label", "Add a module here")
    expect(page.locator("#editor-selected")).not_to_contain_text("Module 2")


def test_a_second_click_on_the_selected_module_lets_it_go(page: Page, launcher_url):
    edit(page, launcher_url, "pair")

    module(page, "Module1").click()
    module(page, "Module1").click()

    expect(module(page, "Module1")).to_have_attribute("aria-pressed", "false")
    expect(page.get_by_role("button", name="Remove", exact=True)).to_have_count(0)


def test_a_click_between_two_neighbours_cycles_their_connection(
    page: Page, launcher_url
):
    """None, one way, the other way, both ways, and none again. The pair starts one way, South."""
    edit(page, launcher_url, "pair")
    link = edge(page, "Module1", "Module2")
    expect(link).to_have_text("↓")

    for glyph, connections in [
        ("↑", [[], [("North", "Module1")]]),
        ("⇅", [[("South", "Module2")], [("North", "Module1")]]),
        ("·", [[], []]),
        ("↓", [[("South", "Module2")], []]),
    ]:
        sent = module_list_after(page, link.click)

        assert [links for _, _, links in read_back(sent)] == connections
        expect(link).to_have_text(glyph)


def test_the_selected_module_is_made_an_entry_an_exit_both_or_neither(
    page: Page, launcher_url
):
    edit(page, launcher_url, "pair")
    module(page, "Module2").click()
    entry = page.get_by_role("checkbox", name="entry", exact=True)
    exit = page.get_by_role("checkbox", name="exit", exact=True)
    expect(entry).not_to_be_checked()
    expect(exit).to_be_checked()

    for box, types, roles in [
        (entry, ["EntryModule", "ExitModule"], "entry · exit"),
        (exit, ["EntryModule"], "entry"),
        (entry, ["FlexConveyorModule"], ""),
        (exit, ["ExitModule"], "exit"),
    ]:
        sent = module_list_after(page, box.click)

        assert read_back(sent)[1][1] == types
        expect(module(page, "Module2").locator(".roles")).to_have_text(roles)
    assert sent[1][TYPE_KEY] == [FC_NS + "ExitModule"]


def test_after_every_change_the_load_check_says_under_the_grid_whether_it_passes_and_why_not(
    page: Page, launcher_url
):
    page.goto(launcher_url)

    sent = module_list_after(page, page.get_by_role("button", name="Empty grid").click)

    assert sent == []
    expect(page.locator("#editor-base")).to_have_text("Edited from an empty grid")
    expect(check_result(page)).to_have_text(NO_MODULE)
    module_list_after(page, cell(page, 0, 0).click)
    expect(check_result(page)).to_have_text(PASSES)
    module_list_after(page, cell(page, 1, 0).click)
    module_list_after(page, cell(page, 2, 0).click)
    left, right = edge(page, "Module1", "Module2"), edge(page, "Module2", "Module3")
    module_list_after(page, left.click)
    expect(check_result(page)).to_have_text(PASSES)
    expect(launch_this(page)).to_be_enabled()
    module_list_after(page, left.click)

    # Both ways between Module 1 and Module 2: two-way, and no pure corridor. The check refuses only
    # a pure two-way corridor.
    module_list_after(page, left.click)
    expect(check_result(page)).to_have_text(PASSES)
    module_list_after(page, right.click)
    module_list_after(page, right.click)

    # Both ways on both sides of Module 2, which has no other connection.
    module_list_after(page, right.click)

    expect(check_result(page)).to_have_text(
        "The load check refuses this topology: Module2 is a pure two-way corridor: its only two "
        "connections, to Module1 and to Module3, both run both ways. Two boxes that meet there "
        "head-on, one each way, could wait for each other forever, with no neighbour to step "
        "aside to. Let one of those connections run one way, or give Module2 a third "
        "connection."
    )
    expect(launch_this(page)).to_be_disabled()

    module_list_after(page, right.click)

    expect(check_result(page)).to_have_text(PASSES)
    expect(launch_this(page)).to_be_enabled()


def test_an_empty_grid_opens_with_the_load_check_failed_and_cannot_launch_until_a_module_is_added(
    page: Page, launcher_url, graph
):
    page.goto(launcher_url)

    sent = module_list_after(page, page.get_by_role("button", name="Empty grid").click)

    assert sent == []
    expect(page.locator("#editor-base")).to_have_text("Edited from an empty grid")
    expect(check_result(page)).to_have_text(NO_MODULE)
    expect(launch_this(page)).to_be_disabled()
    assert graph.seeded == []

    module_list_after(page, cell(page, 0, 0).click)

    expect(check_result(page)).to_have_text(PASSES)
    expect(launch_this(page)).to_be_enabled()


def test_launch_this_asks_first_and_launches_the_edited_topology_called_by_its_base(
    page: Page, launcher_url, graph
):
    edit(page, launcher_url, "pair")
    sent = module_list_after(page, cell(page, 1, 1).click)
    asked: list[str] = []

    def dismiss(dialog: Dialog) -> None:
        asked.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", dismiss)
    launch_this(page).click()

    eventually(lambda: len(asked) == 1, timeout=5)
    assert asked == [
        (
            'Launching the topology edited from "pair" clears dataset test-dataset and '
            "restarts every process. Go on?"
        )
    ]
    assert graph.seeded == []

    page.once("dialog", lambda dialog: dialog.accept())
    launch_this(page).click()

    expect(page.locator("#running")).to_have_text("running: pair (edited)")
    assert graph.seeded == [parse_topology(sent)]
    expect(page.locator("#command-message")).to_contain_text("Launched.")
    # The picker holds the topology launched, as its entry for what runs, and the grid draws it.
    expect(
        page.get_by_label("Topology to launch").locator("option:checked")
    ).to_have_text("pair (edited), running now")
    expect(page.locator("#editor-grid [data-module]")).to_have_count(3)


def downloaded(page: Page) -> tuple[str, bytes]:
    """Press Download, and return the name and the bytes of the file the page saves."""
    with page.expect_download() as saved:
        page.get_by_role("button", name="Download", exact=True).click()
    return saved.value.suggested_filename, Path(saved.value.path()).read_bytes()


def test_download_saves_what_the_editor_holds_as_a_module_list_named_after_its_base(
    page: Page, launcher_url
):
    edit(page, launcher_url, "pair")

    name, content = downloaded(page)

    assert name == "pair.json"
    assert json.loads(content) == load_topology("pair").module_list()

    module_list_after(page, cell(page, 1, 1).click)
    module_list_after(page, edge(page, "Module2", "Module3").click)
    sent = module_list_after(page, page.get_by_role("checkbox", name="exit").click)

    name, content = downloaded(page)

    assert name == "pair.json"
    assert json.loads(content) == sent
    assert read_back(json.loads(content)) == [
        ("Module1", ["EntryModule"], [("South", "Module2")]),
        ("Module2", ["ExitModule"], [("East", "Module3")]),
        ("Module3", ["ExitModule"], []),
    ]


def choose(page: Page, name: str, content: bytes) -> None:
    """Press Upload and choose a file called `name` that holds `content`."""
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Upload", exact=True).click()
    chooser.value.set_files(
        {"name": name, "mimeType": "application/octet-stream", "buffer": content}
    )


def uploaded(page: Page, name: str, content: bytes) -> list[dict]:
    """Upload a file called `name` that holds `content`. Returns the module list the editor then
    holds, which it sends to the load check."""
    return module_list_after(page, lambda: choose(page, name, content))


def grid(page: Page) -> list[list[str]]:
    """What the editor's grid shows: each module with its cell, its name and its roles, and each
    edge between two neighbours with its arrow."""
    return page.locator(
        "#editor-grid [data-module], #editor-grid [data-edge]"
    ).evaluate_all(
        "nodes => nodes.map((node) => "
        "[node.dataset.module || node.dataset.edge, node.dataset.cell || '', node.textContent])"
    )


def test_a_topology_built_in_the_editor_comes_back_from_its_file_as_the_same_grid(
    page: Page, launcher_url
):
    """Download, then upload, gives the same grid, the same module list, and the same name."""
    page.goto(launcher_url)
    module_list_after(page, page.get_by_role("button", name="Empty grid").click)
    for x, y in [(0, 0), (1, 0), (1, 1), (0, 1)]:
        module_list_after(page, cell(page, x, y).click)
    module_list_after(page, page.get_by_role("checkbox", name="exit").click)
    module(page, "Module1").click()
    module_list_after(page, page.get_by_role("checkbox", name="entry").click)
    module_list_after(page, edge(page, "Module1", "Module2").click)
    module_list_after(page, edge(page, "Module2", "Module3").click)
    for _ in range(3):
        module_list_after(page, edge(page, "Module4", "Module3").click)
    built = grid(page)
    name, content = downloaded(page)
    page.reload()

    sent = uploaded(page, name, content)

    assert name == "empty grid.json"
    assert sent == json.loads(content)
    assert grid(page) == built
    expect(page.locator("#editor-base")).to_have_text(
        'Edited from the file "empty grid.json"'
    )
    expect(check_result(page)).to_have_text(PASSES)
    assert downloaded(page) == (name, content)


def test_a_topology_with_a_part_not_connected_to_the_first_module_opens_again_from_its_file(
    page: Page, launcher_url
):
    """The format carries no cells, so a part that is not connected to the first module comes
    back on the rows below it, laid out whole: the file opens again, with the same module list,
    and every connection between neighbours."""
    page.goto(launcher_url)
    module_list_after(page, page.get_by_role("button", name="Empty grid").click)
    for x, y in [(0, 0), (1, 1), (1, 0)]:
        module_list_after(page, cell(page, x, y).click)
    module_list_after(page, edge(page, "Module3", "Module2").click)
    expect(edge(page, "Module3", "Module2")).to_have_text("↓")
    name, content = downloaded(page)
    page.reload()

    sent = uploaded(page, name, content)

    assert sent == json.loads(content)
    for id, place in {"Module1": "0,0", "Module3": "0,1", "Module2": "0,2"}.items():
        expect(module(page, id)).to_have_attribute("data-cell", place)
    expect(edge(page, "Module3", "Module2")).to_have_text("↓")
    expect(page.locator("#command-message")).to_be_hidden()


SHIPPED = STATIC.parent.parent / "topologies"


def test_a_shipped_topology_file_uploads_and_shows_its_grid(page: Page, launcher_url):
    page.goto(launcher_url)

    sent = uploaded(page, "tee.json", (SHIPPED / "tee.json").read_bytes())

    assert sent == load_topology("tee").module_list()
    for id, place in {
        "Module1": "0,0",
        "Module2": "0,1",
        "Module3": "1,1",
        "Module4": "0,2",
    }.items():
        expect(module(page, id)).to_have_attribute("data-cell", place)
    expect(edge(page, "Module2", "Module3")).to_have_text("→")
    expect(page.locator("#editor-base")).to_have_text('Edited from the file "tee.json"')
    expect(check_result(page)).to_have_text(PASSES)
    expect(launch_this(page)).to_be_enabled()


def test_the_first_iterations_cross_file_uploads_as_it_is(page: Page, launcher_url):
    """Its modules have no label, so the grid names each by its id, and a download writes none."""
    page.goto(launcher_url)

    sent = uploaded(
        page, FIRST_ITERATIONS_CROSS.name, FIRST_ITERATIONS_CROSS.read_bytes()
    )

    assert parse_topology(sent) == load_topology(str(FIRST_ITERATIONS_CROSS))
    for id, place in {
        "Module1": "1,0",
        "Module2": "0,1",
        "Module3": "1,1",
        "Module4": "2,1",
        "Module5": "1,2",
    }.items():
        expect(module(page, id)).to_have_attribute("data-cell", place)
        expect(module(page, id).locator(".name")).to_have_text(id)
    expect(edge(page, "Module1", "Module3")).to_have_text("⇅")
    expect(edge(page, "Module3", "Module4")).to_have_text("⇄")
    expect(check_result(page)).to_have_text(PASSES)
    name, content = downloaded(page)
    assert name == "modules_preview_cross.json"
    assert json.loads(content) == sent
    assert all(LABEL_KEY not in entry for entry in json.loads(content))


def test_the_labels_and_ports_of_a_file_survive_an_upload_and_a_download(
    page: Page, launcher_url
):
    """The editor changes no label and no port, so what a file says of them it writes back, also
    after an edit."""
    by_hand = [
        module_entry(
            "Module1", "EntryModule", label="Infeed", links=[("East", "Module2", 2)]
        ),
        module_entry("Module2", "ExitModule", links=[("West", "Module1", 1)]),
    ]
    page.goto(launcher_url)
    uploaded(page, "by-hand.json", json.dumps(by_hand).encode())
    expect(module(page, "Module1").locator(".name")).to_have_text("Infeed")

    module_list_after(page, cell(page, 2, 0).click)
    name, content = downloaded(page)

    assert name == "by-hand.json"
    assert read_back(json.loads(content))[:2] == [
        ("Module1", ["EntryModule"], [("East", "Module2")]),
        ("Module2", ["ExitModule"], [("West", "Module1")]),
    ]
    written = parse_topology(json.loads(content)).modules
    assert [m.label for m in written] == ["Infeed", None, "Module 3"]
    assert [[c.port for c in m.connections] for m in written] == [[2], [1], []]


def refused(page: Page, name: str, content: bytes) -> Locator:
    """Upload a file called `name` that holds `content`, which the page refuses. Returns what the
    page says."""
    choose(page, name, content)
    message = page.locator("#command-message")
    expect(message).to_contain_text(f'Cannot open "{name}": ')
    return message


# Two modules that the layout cannot put next to each other in both of their directions: Module3
# is South of Module1, so it is not East of Module2.
SKEWED = [
    module_entry(
        "Module1", "EntryModule", links=[("East", "Module2"), ("South", "Module3")]
    ),
    module_entry("Module2", links=[("East", "Module3")]),
    module_entry("Module3", "ExitModule"),
]


def test_a_file_that_is_no_topology_is_refused_in_the_page_and_the_grid_stays_as_it_was(
    page: Page, launcher_url
):
    page.goto(launcher_url)
    refused(page, "notes.txt", b"Module1 -> Module2\n")
    expect(page.locator("#editor")).to_be_hidden()

    edit(page, launcher_url, "pair")
    shown = grid(page)

    for name, content, reason in [
        (
            "notes.txt",
            b"Module1 -> Module2\n",
            "This file is no JSON: Expecting value: line 1 column 1 (char 0). "
            + TOPOLOGY_FORMAT,
        ),
        (
            "short.json",
            json.dumps(
                {"modules": [{"id": "Module1", "type": "EntryModule"}]}
            ).encode(),
            'This is a JSON object with its modules under "modules", the short topology '
            "document, which use case 2 reads no longer. " + TOPOLOGY_FORMAT,
        ),
        (
            "dangling.json",
            json.dumps([module_entry("Module1", links=[("East", "Module9")])]).encode(),
            "Module1 connects to Module9, which is not in the topology",
        ),
        (
            "skewed.json",
            json.dumps(SKEWED).encode(),
            (
                "Module2 connects East to Module3, which the layout does not put next to it in "
                "that direction, so the grid cannot show it."
            ),
        ),
    ]:
        message = refused(page, name, content)

        expect(message).to_have_text(f'Cannot open "{name}": {reason}')
        assert grid(page) == shown
        expect(page.locator("#editor-base")).to_have_text('Edited from "pair"')
        expect(check_result(page)).to_have_text(PASSES)
        expect(launch_this(page)).to_be_enabled()


def test_an_uploaded_topology_that_fails_the_load_check_shows_its_reason_and_cannot_launch(
    page: Page, launcher_url, graph
):
    corridor = [
        module_entry("Module1", label="Module 1", links=[("South", "Module2")]),
        module_entry(
            "Module2",
            label="Module 2",
            links=[("North", "Module1"), ("South", "Module3")],
        ),
        module_entry("Module3", label="Module 3", links=[("North", "Module2")]),
    ]
    edit(page, launcher_url, "pair")

    uploaded(page, "corridor.json", json.dumps(corridor).encode())

    expect(edge(page, "Module1", "Module2")).to_have_text("⇅")
    expect(edge(page, "Module2", "Module3")).to_have_text("⇅")
    expect(check_result(page)).to_have_text(
        "The load check refuses this topology: Module2 is a pure two-way corridor: its only two "
        "connections, to Module1 and to Module3, both run both ways. Two boxes that meet there "
        "head-on, one each way, could wait for each other forever, with no neighbour to step "
        "aside to. Let one of those connections run one way, or give Module2 a third "
        "connection."
    )
    expect(launch_this(page)).to_be_disabled()
    assert graph.seeded == []


def test_an_uploaded_file_with_an_empty_module_list_opens_as_an_empty_grid_that_cannot_launch(
    page: Page, launcher_url, graph
):
    edit(page, launcher_url, "pair")

    sent = uploaded(page, "nothing.json", b"[]")

    assert sent == []
    assert grid(page) == []
    expect(page.locator("#editor-base")).to_have_text(
        'Edited from the file "nothing.json"'
    )
    expect(check_result(page)).to_have_text(NO_MODULE)
    expect(launch_this(page)).to_be_disabled()
    assert graph.seeded == []


def test_an_uploaded_topology_launches_called_by_its_file(
    page: Page, launcher_url, graph
):
    loop = [
        module_entry("Module1", "EntryModule", label="In", links=[("East", "Module2")]),
        module_entry("Module2", "ExitModule", label="Out"),
    ]
    page.goto(launcher_url)
    sent = uploaded(page, "my-loop.json", json.dumps(loop).encode())
    asked: list[str] = []

    def accept(dialog: Dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    launch_this(page).click()

    expect(page.locator("#running")).to_have_text("running: my-loop (edited)")
    assert asked == [
        (
            'Launching the topology edited from the file "my-loop.json" clears dataset '
            "test-dataset and restarts every process. Go on?"
        )
    ]
    assert graph.seeded == [parse_topology(loop)] == [parse_topology(sent)]


def test_a_topology_edited_from_an_empty_grid_runs_as_such(page: Page, launcher_url):
    page.goto(launcher_url)
    module_list_after(page, page.get_by_role("button", name="Empty grid").click)
    module_list_after(page, cell(page, 0, 0).click)

    page.once("dialog", lambda dialog: dialog.accept())
    launch_this(page).click()

    expect(page.locator("#running")).to_have_text("running: empty grid (edited)")


# -- The transit time of a module ------------------------------------------------ #


def transit_field(page: Page) -> Locator:
    """The transit time field of the selected module."""
    return page.locator("#editor-selected").get_by_label(
        "Transit time in seconds", exact=True
    )


def give_transit_time(page: Page, id: str, text: str) -> list[dict]:
    """Select the module `id` if it is not selected, type `text` into its transit time field, and
    return the module list the page then sent to the load check."""
    if module(page, id).get_attribute("aria-pressed") != "true":
        module(page, id).click()
    return module_list_after(page, lambda: transit_field(page).fill(text))


def own_transit_times(module_list: list[dict]) -> list[float | None]:
    """The transit time of each module of `module_list`, as the server's reader reads it."""
    return [m.transit_seconds for m in parse_topology(module_list).modules]


def test_a_selected_module_has_a_transit_time_field_that_is_empty_unless_the_module_has_one(
    page: Page, launcher_url
):
    sent = edit(page, launcher_url, "pair")
    assert all(TRANSIT_TIME_KEY not in entry for entry in sent)
    module(page, "Module1").click()
    expect(transit_field(page)).to_have_value("")

    # A value in a file shows in its module's field, and only there.
    page.reload()
    with_own = [
        module_entry(
            "Module1", "EntryModule", transit_time=2.5, links=[("East", "Module2")]
        ),
        module_entry("Module2", "ExitModule"),
    ]
    uploaded(page, "own.json", json.dumps(with_own).encode())
    module(page, "Module1").click()
    expect(transit_field(page)).to_have_value("2.5")
    module(page, "Module2").click()
    expect(transit_field(page)).to_have_value("")


def test_a_transit_time_typed_into_the_field_goes_into_the_module_list_and_an_empty_field_takes_it_out(
    page: Page, launcher_url
):
    edit(page, launcher_url, "pair")

    sent = give_transit_time(page, "Module1", "2")

    assert own_transit_times(sent) == [2.0, None]
    assert sent[0][TRANSIT_TIME_KEY] == [2]

    sent = give_transit_time(page, "Module1", "0")

    assert own_transit_times(sent) == [0.0, None]
    expect(check_result(page)).to_have_text(PASSES)

    sent = give_transit_time(page, "Module1", "")

    assert own_transit_times(sent) == [None, None]
    assert all(TRANSIT_TIME_KEY not in entry for entry in sent)
    # The value stays with its module when another one is selected and this one again.
    give_transit_time(page, "Module2", "1.5")
    module(page, "Module1").click()
    expect(transit_field(page)).to_have_value("")
    module(page, "Module2").click()
    expect(transit_field(page)).to_have_value("1.5")


def test_a_launch_of_an_edited_topology_gives_each_module_its_own_transit_time(
    page: Page, launcher_url, graph
):
    edit(page, launcher_url, "pair")
    give_transit_time(page, "Module1", "2")
    sent = give_transit_time(page, "Module2", "0.5")

    page.once("dialog", lambda dialog: dialog.accept())
    launch_this(page).click()

    expect(page.locator("#running")).to_have_text("running: pair (edited)")
    assert graph.seeded == [parse_topology(sent)]
    assert [m.transit_seconds for m in graph.seeded[0].modules] == [2.0, 0.5]


def test_download_carries_each_module_s_transit_time_and_upload_restores_it(
    page: Page, launcher_url
):
    edit(page, launcher_url, "pair")
    give_transit_time(page, "Module1", "0")
    sent = give_transit_time(page, "Module2", "3.25")
    name, content = downloaded(page)
    page.reload()

    assert json.loads(content) == sent
    assert own_transit_times(json.loads(content)) == [0.0, 3.25]

    restored = uploaded(page, name, content)

    assert restored == sent
    module(page, "Module1").click()
    expect(transit_field(page)).to_have_value("0")
    module(page, "Module2").click()
    expect(transit_field(page)).to_have_value("3.25")


@pytest.mark.parametrize("text", ["fast", "-1", "-0.5", "1,5", "NaN"])
def test_a_transit_time_that_is_no_number_or_is_negative_is_refused_in_the_field_and_by_the_server(
    page: Page, launcher_url, graph, text
):
    edit(page, launcher_url, "pair")

    give_transit_time(page, "Module1", text)

    expect(page.locator("#editor-selected [role=alert]")).to_have_text(
        f'The transit time must be a number of seconds, 0 or more, not "{text}".'
    )
    expect(transit_field(page)).to_have_attribute("aria-invalid", "true")
    # The server's load check refuses what the field holds, too.
    expect(check_result(page)).to_contain_text("The load check refuses this topology: ")
    expect(check_result(page)).to_contain_text("its transit time")
    expect(launch_this(page)).to_be_disabled()
    assert graph.seeded == []

    # A number brings the launch back, and 0 is a number.
    give_transit_time(page, "Module1", "0")

    expect(page.locator("#editor-selected [role=alert]")).to_have_text("")
    expect(transit_field(page)).to_have_attribute("aria-invalid", "false")
    expect(check_result(page)).to_have_text(PASSES)
    expect(launch_this(page)).to_be_enabled()
