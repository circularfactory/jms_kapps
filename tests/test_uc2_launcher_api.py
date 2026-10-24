"""The Launcher page's backend: its JSON API, through FastAPI's test client.

The children are real processes that only sleep. The graph is a fake that records what the
Launcher seeds, and answers which services are live and where each is, each with every workflow it
is asked for. It counts every read, so a test can tell what a poll reads, and a test can make it
unreachable (`away`), so that every read raises. The modules' fault switches are a fake that
records every switch it is sent -- and offers no way to read one back, because the Launcher has
none either. It can be made unreachable, as a module that has gone offline is.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import warnings
from collections.abc import Callable, Collection, Iterator
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

import pytest
from _html import absolute_paths
from _services import live_services
from _topologies import FIRST_ITERATIONS_CROSS, module_entry
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.common.supervision import ChildSnapshot, Supervisor
from jms_kapps.uc2 import topology
from jms_kapps.uc2.launcher import api, backend
from jms_kapps.uc2.launcher.api import STATIC, create_app
from jms_kapps.uc2.launcher.backend import Launcher, Link, NotReachable
from jms_kapps.uc2.run_core import RunGraph
from jms_kapps.uc2.topology import (
    Module,
    Topology,
    UnknownTopology,
    load_topology,
    parse_topology,
    shipped_topology,
)
from jms_kapps.uc2.vocabulary import MONITOR, WMS, module_iri

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
CRASHES = [sys.executable, "-c", "raise SystemExit(3)"]
M1 = module_iri(1)


def module_1_runs(command: list[str]) -> Callable[[IRI], list[str]]:
    """The command line of each module: `command` for Module 1, and `RUNS` for the others."""
    return lambda resource: command if resource == M1 else RUNS


class FakeGraph:
    def __init__(self) -> None:
        self.seeded: list[Topology] = []
        self.transit_seconds: list[float] = []
        self.live: set[IRI] = set()
        self.addresses: dict[IRI, str] = {}
        self.refusal: str | None = None
        self.away: str | None = None
        self.reads = 0
        # The one service of a resource that is not live any more, each one's that a test sets.
        self.left: dict[IRI, Service] = {}

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        if self.refusal:
            raise RuntimeError(self.refusal)
        self.seeded.append(topology)
        self.transit_seconds.append(transit_seconds)

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return [self.left[resource]] if resource in self.left else []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        self._read()
        return live_services(self.live, workflows, self.addresses)

    def _read(self) -> None:
        self.reads += 1
        if self.away is not None:
            raise ConnectionError(self.away)


class FakeFaults:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.unreachable: str | None = None

    def set(self, address: str, name: str, armed: bool) -> None:
        if self.unreachable is not None:
            raise NotReachable(self.unreachable)
        self.calls.append((address, name, armed))


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def faults() -> FakeFaults:
    return FakeFaults()


@pytest.fixture
def launcher_with(graph, faults) -> Iterator[Callable[..., Launcher]]:
    """Builds a Launcher on the fake graph and fault switches, and stops each one it built.

    Each keyword replaces one default: another graph, the module command, the WMS's command,
    the monitor command, or the stop timeout.
    """
    built: list[Launcher] = []

    def build(
        *,
        graph: RunGraph = graph,
        module_command: Callable[[IRI], list[str]] = lambda resource: RUNS,
        wms_command: list[str] = RUNS,
        monitor_command: list[str] = RUNS,
        stop_timeout: float = 5.0,
    ) -> Launcher:
        launcher = Launcher(
            graph=graph,
            dataset="test-dataset",
            module_command=module_command,
            wms_command=wms_command,
            monitor_command=monitor_command,
            faults=faults,
            echo=lambda line: None,
            stop_timeout=stop_timeout,
        )
        built.append(launcher)
        return launcher

    yield build
    for launcher in built:
        launcher.stop_all()


@pytest.fixture
def launcher(launcher_with) -> Launcher:
    return launcher_with()


@pytest.fixture
def unwatched(monkeypatch) -> list[Supervisor]:
    """Every supervisor the Launcher starts, with no watch thread: the test refreshes it itself."""
    supervisors: list[Supervisor] = []
    monkeypatch.setattr(
        Supervisor, "watch", lambda self, *_, **__: supervisors.append(self)
    )
    return supervisors


@pytest.fixture
def client(launcher):
    with TestClient(create_app(launcher)) as client:
        yield client


@pytest.fixture
def cross() -> Topology:
    return load_topology("cross")


def process(client: TestClient, resource: IRI) -> dict:
    processes = client.get("/api/state").json()["processes"]
    return next(p for p in processes if p["resource"] == str(resource))


ALL_OFF = {"skip-reserve": False, "complete-without-releasing": False}


def switch(client: TestClient, module: IRI, name: str, armed: bool):
    """Arm or disarm the fault `name` of `module`, as the page does."""
    return client.post(
        "/api/modules/faults",
        json={"module": str(module), "name": name, "armed": armed},
    )


def test_the_page_is_static_html_that_loads_its_own_script(client):
    page = client.get("/")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert 'src="launcher.js"' in page.text
    assert client.get("/launcher.js").status_code == 200


@pytest.mark.parametrize("name", ["index.html", "launcher.js", "style.css"])
def test_the_page_asks_for_everything_by_a_relative_path(name):
    """The page reaches the Launcher at whatever address it was opened, such as the path that a
    forwarded port of VS Code puts in front of it."""
    assert absolute_paths((STATIC / name).read_text(encoding="utf-8")) == []


def sections(markup: str) -> list[tuple[str, str, list[str]]]:
    """Each section of the page, as its heading, its intro (the text of its first paragraph) and
    the summaries of its details blocks."""

    def text(fragment: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())

    found = []
    for section in re.findall(r"<section\b.*?</section>", markup, re.DOTALL):
        heading = text(re.search(r"<h2\b.*?</h2>", section, re.DOTALL).group(0))
        intro = text(re.search(r"<p\b.*?</p>", section, re.DOTALL).group(0))
        summaries = [
            text(s) for s in re.findall(r"<summary>(.*?)</summary>", section, re.DOTALL)
        ]
        found.append((heading, intro, summaries))
    return found


def test_each_section_has_an_intro_of_two_sentences_at_most_and_details_for_the_rest():
    found = sections((STATIC / "index.html").read_text(encoding="utf-8"))

    for heading, intro, _ in found:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", intro) if s]
        assert 1 <= len(sentences) <= 2, (heading, intro)
    summaries = {heading: summaries for heading, _, summaries in found}
    assert summaries["Processes"] == ["The two faults"]
    assert summaries["Topology"] == [
        "Build your own topology",
        "The shipped topologies",
        "The run settings",
    ]
    assert "Running now" in summaries


def test_the_page_offers_a_stop_only_where_the_backend_says_it_may_and_names_no_state_for_it():
    """The page has no script harness, so this reads its script, until it has one: a test of the
    rendered rows would replace it. The stop button and the note on
    a state come from the backend's fields. The page spells neither `stopped` nor `killed`: a
    crashed row, which reads `failed` and runs no more, gets no stop button that does nothing."""
    script = (STATIC / "launcher.js").read_text(encoding="utf-8")

    assert re.search(r'process\.stoppable\s*\?\s*el\(\s*"button"', script)
    assert "process.state_note" in script
    assert "disabled: !process.fault_switchable" in script
    for state in ("starting", "live", "stopped", "killed", "failed"):
        assert f'"{state}"' not in script


# The kinds of process, by the names the backend gives them (`backend.Kind`).
KINDS = frozenset({"module", "wms", "monitor"})


def kind_comparisons(source: str) -> list[int]:
    """The lines of `source` that compare a value with the name of a kind, or with a collection
    of such names, as `kind == "module"` or `kind in ("module", "wms")` do: each one decides
    something by the kind again."""

    def names_a_kind(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return node.value in KINDS
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return any(names_a_kind(element) for element in node.elts)
        return False

    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        and any(names_a_kind(operand) for operand in [node.left, *node.comparators])
    )


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('kind = "module"\nif kind == "module":\n    pass\n', [2]),
        ('x = kind in ("module", "wms")\ny = p.kind != other\n', [1]),
        ('offers = OFFERS["wms"]\nlinks = page(processes, "monitor", "open")\n', []),
    ],
)
def test_the_guard_finds_each_comparison_with_the_name_of_a_kind(source, found):
    assert kind_comparisons(source) == found


def test_the_kind_of_a_process_is_decided_in_one_place_and_only_read_elsewhere():
    """One place decides the kind of each process, and one table says what each kind has: its
    pages and its faults. No other code of the Launcher's backend or routes compares a value with
    the name of a kind, and the page reads no kind at all: it reads what the backend made of it,
    the links, the faults and whether they may be switched."""
    for module in ("backend.py", "api.py"):
        source = (STATIC.parent / module).read_text(encoding="utf-8")
        assert kind_comparisons(source) == [], module
    assert ".kind" not in (STATIC / "launcher.js").read_text(encoding="utf-8")


def test_launching_a_topology_seeds_it_and_starts_every_module_the_wms_and_the_monitor(
    client, graph, cross
):
    assert client.post("/api/launch", json={"name": "cross"}).status_code == 200

    assert graph.seeded == [cross]
    state = client.get("/api/state").json()
    assert state["topology"] == cross.model_dump()
    assert state["topology_name"] == "cross"
    assert [(p["name"], p["kind"], p["state"]) for p in state["processes"]] == [
        ("Module 1", "module", "starting"),
        ("Module 2", "module", "starting"),
        ("Module 3", "module", "starting"),
        ("Module 4", "module", "starting"),
        ("Module 5", "module", "starting"),
        ("Warehouse Management System", "wms", "starting"),
        ("Monitor", "monitor", "starting"),
    ]


def test_a_launch_may_only_name_a_topology_this_package_ships(client, graph):
    response = client.post("/api/launch", json={"name": "handwritten"})

    assert response.status_code == 404
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_a_shipped_topology_on_which_boxes_can_wait_forever_is_refused_before_anything_is_cleared(
    client, graph, monkeypatch
):
    """A line of three modules linked both ways, shipped as `corridor`: a launch of it is refused
    with the reason, and the dataset is neither cleared nor seeded."""
    shipped = topology._shipped()
    monkeypatch.setattr(
        topology, "_shipped", lambda: {**shipped, "corridor": json.dumps(CORRIDOR)}
    )

    response = client.post("/api/launch", json={"name": "corridor"})

    assert response.status_code == 422
    assert response.json()["detail"] == CORRIDOR_REASON
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_a_launch_cannot_name_a_file_by_its_path(client, graph, tmp_path):
    # `load_topology` reads any path it is given; the route may reach only a shipped name.
    path = tmp_path / "secret.json"
    path.write_text("[]")

    response = client.post("/api/launch", json={"name": str(path)})

    assert response.status_code == 404
    assert graph.seeded == []


def test_a_launch_seeds_the_shipped_topology_even_beside_a_file_of_its_name(
    client, graph, cross, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cross").write_text(json.dumps([module_entry("Module7")]))

    assert client.post("/api/launch", json={"name": "cross"}).status_code == 200

    assert graph.seeded == [cross]


def test_a_launch_by_name_carries_no_topology_of_its_own(client, graph, cross):
    """The body of a launch by name is a name. A whole topology in it is refused before anything
    is cleared: an edited topology goes to `/api/topology/launch`."""
    response = client.post("/api/launch", json=cross.module_list())

    assert response.status_code == 422
    assert graph.seeded == []


# -- A topology edited on the page ------------------------------------------------------------ #


def test_the_check_passes_a_topology_that_loading_accepts(client, graph, cross):
    response = client.post("/api/topology/check", json=cross.module_list())

    assert response.status_code == 200
    assert response.json() == {"passes": True, "reason": None}
    assert graph.seeded == []


# Three modules in a column, each linked both ways to its neighbour: Module 2's only two
# connections both run both ways, a pure two-way corridor, and the load check refuses it with this
# reason: the check refuses only a pure two-way corridor.
CORRIDOR_REASON = (
    "Module2 is a pure two-way corridor: its only two connections, to Module1 and to Module3, "
    "both run both ways. Two boxes that meet there head-on, one each way, could wait for each "
    "other forever, with no neighbour to step aside to. Let one of those connections run one "
    "way, or give Module2 a third connection."
)
CORRIDOR = [
    module_entry("Module1", label="Module 1", links=[("South", "Module2")]),
    module_entry(
        "Module2", label="Module 2", links=[("North", "Module1"), ("South", "Module3")]
    ),
    module_entry("Module3", label="Module 3", links=[("North", "Module2")]),
]

# Two modules that hand boxes to each other: two-way, and no pure corridor.
TWO_WAY_PAIR = [
    module_entry("Module1", label="Module 1", links=[("South", "Module2")]),
    module_entry("Module2", label="Module 2", links=[("North", "Module1")]),
]


def test_the_check_fails_a_topology_on_which_boxes_can_wait_forever_with_the_reason(
    client, graph
):
    response = client.post("/api/topology/check", json=CORRIDOR)

    assert response.status_code == 200
    assert response.json() == {"passes": False, "reason": CORRIDOR_REASON}
    assert graph.seeded == []


def test_the_check_passes_a_topology_with_two_way_connections_and_no_pure_corridor(
    client, graph
):
    response = client.post("/api/topology/check", json=TWO_WAY_PAIR)

    assert response.status_code == 200
    assert response.json() == {"passes": True, "reason": None}
    assert graph.seeded == []


# What a refusal of anything but a module list says of the format a topology has.
FORMAT = "A topology is a JSON list of modules, as the first iteration wrote them"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            [module_entry("Module1", links=[("East", "Module9")])],
            "Module1 connects to Module9, which is not in the topology",
        ),
        (
            [module_entry("Module1", label="")],
            'Module1: its label is a string that is not empty, not ""',
        ),
        ([1], "The module at index 0 is a JSON number, not a JSON object."),
        (
            {"modules": [{"id": "Module1", "label": "Module 1"}]},
            (
                'This is a JSON object with its modules under "modules", the short topology '
                f"document, which use case 2 reads no longer. {FORMAT}"
            ),
        ),
        ("cross", f"This is a JSON string, not a list of modules. {FORMAT}"),
    ],
)
def test_the_check_fails_a_topology_that_cannot_be_seeded_with_a_reason_a_person_reads(
    client, body, reason
):
    """The reason is the load check's own, without the parser's report around it, and names no
    class of the code."""
    response = client.post("/api/topology/check", json=body)

    assert response.status_code == 200
    assert response.json()["passes"] is False
    assert response.json()["reason"].startswith(reason)


# A topology no package ships: an entry module that hands boxes East to an exit module.
ONE_WAY_PAIR = [
    module_entry(
        "Module1", "EntryModule", label="Module 1", links=[("East", "Module2")]
    ),
    module_entry("Module2", "ExitModule", label="Module 2"),
]


def test_launching_an_edited_topology_seeds_it_and_starts_its_modules_the_wms_and_the_monitor(
    client, graph
):
    response = client.post("/api/topology/launch", json=ONE_WAY_PAIR)

    assert response.status_code == 200, response.text
    assert graph.seeded == [parse_topology(ONE_WAY_PAIR)]
    state = client.get("/api/state").json()
    assert state["topology"] == parse_topology(ONE_WAY_PAIR).model_dump()
    assert state["topology_name"] == "edited"
    assert [(p["name"], p["kind"]) for p in state["processes"]] == [
        ("Module 1", "module"),
        ("Module 2", "module"),
        ("Warehouse Management System", "wms"),
        ("Monitor", "monitor"),
    ]


def test_an_edited_topology_is_called_by_the_topology_it_was_edited_from(client):
    """The page names the topology its editor started from, and the page then says
    `running: tee (edited)`: the picker may still show any name, and the label says which one was
    edited."""
    response = client.post(
        "/api/topology/launch", params={"base": "tee"}, json=ONE_WAY_PAIR
    )

    assert response.status_code == 200, response.text
    assert response.json()["topology_name"] == "tee (edited)"
    assert client.get("/api/state").json()["topology_name"] == "tee (edited)"


def test_the_layout_of_a_module_list_is_its_modules_and_a_cell_for_each(client, graph):
    """The page's editor draws a topology on a grid, at the cells the one layout rule finds, and
    reads its modules from the server, which reads the module list."""
    response = client.post(
        "/api/topology/layout", json=load_topology("tee").module_list()
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "modules": load_topology("tee").model_dump()["modules"],
        "places": {
            "Module1": [0, 0],
            "Module2": [0, 1],
            "Module3": [1, 1],
            "Module4": [0, 2],
        },
    }
    assert graph.seeded == []


def test_a_module_list_that_fails_the_load_check_still_has_a_layout(client):
    """A person edits a topology that fails the check until it passes."""
    response = client.post("/api/topology/layout", json=CORRIDOR)

    assert response.status_code == 200, response.text
    assert response.json()["places"] == {
        "Module1": [0, 0],
        "Module2": [0, 1],
        "Module3": [0, 2],
    }


def test_what_is_no_module_list_has_no_layout_and_the_reason_says_why(client):
    response = client.post("/api/topology/layout", json={"modules": []})

    assert response.status_code == 422
    assert response.json()["detail"].startswith(
        'This is a JSON object with its modules under "modules"'
    )


def test_a_shipped_topology_is_given_as_its_module_list(client):
    response = client.get("/api/topologies/cross")

    assert response.status_code == 200
    assert parse_topology(response.json()) == load_topology("cross")
    assert response.json() == json.loads(
        (STATIC.parent.parent / "topologies" / "cross.json").read_text(encoding="utf-8")
    )


def test_the_merge_fault_demo_is_given_with_its_six_modules_roles_and_connections(
    client,
):
    response = client.get("/api/topologies/merge-fault-demo")

    assert response.status_code == 200
    topology = parse_topology(response.json())
    assert topology == load_topology("merge-fault-demo")
    assert [(m.id, m.label, m.types) for m in topology.modules] == [
        ("Module1", "Module 1", ["EntryModule"]),
        ("Module2", "Module 2", ["EntryModule"]),
        ("Module3", "Module 3", ["FlexConveyorModule"]),
        ("Module4", "Module 4", ["ExitModule"]),
        ("Module5", "Module 5", ["FlexConveyorModule"]),
        ("Module6", "Module 6", ["FlexConveyorModule"]),
    ]
    assert [
        (m.id, c.direction, c.to, c.port)
        for m in topology.modules
        for c in m.connections
    ] == [
        ("Module1", "South", "Module5", 0),
        ("Module2", "South", "Module3", 0),
        ("Module3", "South", "Module4", 0),
        ("Module5", "South", "Module6", 0),
        ("Module6", "East", "Module4", 0),
    ]


def test_a_module_list_written_with_the_names_the_server_gives_is_one_it_reads(client):
    """The page's editor writes a module list with the names `topology.py` gives, and carries no
    copy of its own."""
    names = client.get("/api/topology/format").json()

    def connection(direction: str, to: str) -> dict:
        return {
            names["connects_to"]: [names["modules"] + to],
            names["direction"]: [names["classes"] + direction],
            names["port"]: [0],
        }

    written = [
        {
            "id": names["modules"] + "Module1",
            names["type"]: [names["classes"] + "EntryModule"],
            names["label"]: ["Module 1"],
            names["connection"]: [connection("East", "Module2")],
        },
        {
            "id": names["modules"] + "Module2",
            names["type"]: [names["classes"] + "ExitModule"],
            names["label"]: ["Module 2"],
            names["connection"]: [],
        },
    ]

    assert parse_topology(written) == parse_topology(ONE_WAY_PAIR)


def test_a_topology_no_package_ships_is_not_given(client):
    assert client.get("/api/topologies/handwritten").status_code == 404


def test_every_caller_checks_a_shipped_name_once_and_refuses_an_unknown_one_in_its_words(
    client, monkeypatch, tmp_path
):
    """The check of shipped names is `shipped_topology`'s, and every caller uses it: a launch by
    name, the editor's start from a shipped topology, and `jms-uc2 --topology`. Each reads the
    package's topologies once, and refuses a name that the package does not ship with the check's
    own reason, which names the shipped topologies."""
    monkeypatch.chdir(tmp_path)
    reads: list[str] = []
    shipped = topology._shipped

    def read() -> dict[str, str]:
        reads.append("read")
        return shipped()

    monkeypatch.setattr(topology, "_shipped", read)
    with pytest.raises(UnknownTopology) as unknown:
        shipped_topology("ring")
    reason = str(unknown.value)
    assert reason.startswith("There is no shipped topology 'ring'. Shipped: cross, ")

    reads.clear()
    for refused in (
        client.post("/api/launch", json={"name": "ring"}),
        client.get("/api/topologies/ring"),
    ):
        assert (refused.status_code, refused.json()["detail"]) == (404, reason)
    with pytest.raises(UnknownTopology, match=re.escape(reason)):
        load_topology("ring")
    assert len(reads) == 3

    reads.clear()
    assert client.get("/api/topologies/pair").status_code == 200
    assert load_topology("pair") == shipped_topology("pair")
    assert client.post("/api/launch", json={"name": "pair"}).status_code == 200
    assert len(reads) == 4


def upload(client: TestClient, content: bytes):
    """Send `content`, the bytes of a file, to be opened as the page's upload sends a file."""
    return client.post(
        "/api/topology/file",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_a_topology_file_opens_as_its_modules_and_a_cell_for_each(client, graph):
    """The page's editor opens an uploaded file as it opens a shipped topology: the server reads
    the file, and the page reads nothing of it."""
    shipped = STATIC.parent.parent / "topologies" / "tee.json"

    response = upload(client, shipped.read_bytes())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "modules": load_topology("tee").model_dump()["modules"],
        "places": {
            "Module1": [0, 0],
            "Module2": [0, 1],
            "Module3": [1, 1],
            "Module4": [0, 2],
        },
    }
    assert graph.seeded == []


def test_the_first_iterations_cross_file_opens_as_it_is(client):
    response = upload(client, FIRST_ITERATIONS_CROSS.read_bytes())

    assert response.status_code == 200, response.text
    assert [Module.model_validate(m) for m in response.json()["modules"]] == (
        load_topology(str(FIRST_ITERATIONS_CROSS)).modules
    )


def test_a_topology_file_that_fails_the_load_check_still_opens(client):
    """A person edits a topology that fails the check until it passes."""
    response = upload(client, json.dumps(CORRIDOR).encode())

    assert response.status_code == 200, response.text
    assert response.json()["places"] == {
        "Module1": [0, 0],
        "Module2": [0, 1],
        "Module3": [0, 2],
    }


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            b"Module1 -> Module2\n",
            f"This file is no JSON: Expecting value: line 1 column 1 (char 0). {FORMAT}",
        ),
        (
            b"",
            f"This file is no JSON: Expecting value: line 1 column 1 (char 0). {FORMAT}",
        ),
        (
            b"\x89PNG\r\n",
            (
                "This file is no text in UTF-8, so no JSON: 'utf-8' codec can't decode byte "
                f"0x89 in position 0: invalid start byte. {FORMAT}"
            ),
        ),
        (
            b'{"modules": []}',
            (
                'This is a JSON object with its modules under "modules", the short topology '
                f"document, which use case 2 reads no longer. {FORMAT}"
            ),
        ),
        (
            json.dumps([module_entry("Module1", links=[("East", "Module9")])]).encode(),
            "Module1 connects to Module9, which is not in the topology",
        ),
    ],
)
def test_a_file_that_is_no_topology_does_not_open_and_the_reason_says_why(
    client, content, reason
):
    response = upload(client, content)

    assert response.status_code == 422
    assert response.json()["detail"].startswith(reason)


def test_an_edited_topology_that_fails_the_check_is_refused_with_its_reason_and_nothing_is_stopped(
    client, graph, cross
):
    """The launch checks again, whether or not the page asked the check first. The body is the
    module list and nothing else, so a result the page claims beside it makes no module list.
    Nothing is seeded, and what runs keeps running."""
    client.post("/api/launch", json={"name": "cross"})
    claimed = {"topology": CORRIDOR, "passes": True, "reason": None}

    response = client.post("/api/topology/launch", json=CORRIDOR)
    claiming = client.post("/api/topology/launch", json=claimed)

    assert response.status_code == 422
    assert response.json()["detail"] == CORRIDOR_REASON
    assert claiming.status_code == 422
    assert claiming.json()["detail"].startswith(
        "This is a JSON object, not a list of modules."
    )
    assert graph.seeded == [cross]
    state = client.get("/api/state").json()
    assert state["topology_name"] == "cross"
    assert {p["state"] for p in state["processes"]} == {"starting"}


def test_an_edited_topology_that_cannot_be_seeded_is_refused_before_anything_is_cleared(
    client, graph
):
    twice = [module_entry("Module1"), module_entry("Module1")]

    response = client.post("/api/topology/launch", json=twice)

    assert response.status_code == 422
    assert response.json()["detail"] == "a module id is used twice: Module1"
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


# A module on the monitor's resource, which the Launcher seeds and starts beside every topology.
ON_THE_MONITOR = [module_entry("Monitor1", label="Monitor 1")]
ON_THE_MONITOR_REASON = (
    "Monitor1 is the monitor's resource, which the Launcher seeds and starts beside every "
    "topology, so no module can take that id"
)


def test_the_launcher_refuses_a_module_on_the_monitors_resource_in_every_route_that_reads_one(
    client, graph, cross
):
    """The reservation moved from the topology model into the Launcher, which alone starts the
    monitor: the page still sees it in the check, the layout, a file and a launch,
    with the reason the topology model gave, and nothing is stopped or seeded."""
    client.post("/api/launch", json={"name": "cross"})

    check = client.post("/api/topology/check", json=ON_THE_MONITOR)
    layout = client.post("/api/topology/layout", json=ON_THE_MONITOR)
    opened = upload(client, json.dumps(ON_THE_MONITOR).encode())
    launched = client.post("/api/topology/launch", json=ON_THE_MONITOR)

    assert check.json() == {"passes": False, "reason": ON_THE_MONITOR_REASON}
    for refused in (layout, opened, launched):
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == ON_THE_MONITOR_REASON
    assert graph.seeded == [cross]
    assert client.get("/api/state").json()["topology_name"] == "cross"


def test_a_launch_of_a_module_on_the_monitors_resource_is_refused_before_anything_is_stopped(
    launcher, graph, cross
):
    launcher.launch(cross, name="cross")
    running = [p.child.pid for p in launcher.state().processes]

    with pytest.raises(ValueError, match="^Monitor1 is the monitor's resource"):
        launcher.launch(parse_topology(ON_THE_MONITOR), name="edited")

    assert graph.seeded == [cross]
    assert [p.child.pid for p in launcher.state().processes] == running
    assert {p.child.state for p in launcher.state().processes} == {"starting"}


def test_each_process_is_the_flat_object_the_page_reads_and_the_api_docs_show(client):
    """The Launcher's process state composes the supervisor's snapshot, and the
    page still reads the snapshot's fields beside its own in one object, which the API docs
    describe field by field."""
    client.post("/api/launch", json={"name": "cross"})
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    shown = schemas["ProcessState"]
    if "$ref" in shown:
        shown = schemas[shown["$ref"].rsplit("/", 1)[-1]]
    page = {"kind", "links", "faults", "state_note", "fault_switchable"}

    [process, *_] = client.get("/api/state").json()["processes"]

    assert set(shown["properties"]) == {f.name for f in fields(ChildSnapshot)} | page
    assert set(process) == set(shown["properties"])
    assert shown["properties"]["resource"]["type"] == "string"
    assert process["resource"] == str(module_iri(1))


def test_serializing_a_process_with_links_and_the_whole_state_emits_no_serializer_warning(
    launcher, cross
):
    """The flat object is built from fields that are already dicts once the wrapped handler
    returns: a return type that names the flat object makes Pydantic check the links a second
    time, and warn once for each link of each live process."""
    launcher.launch(cross, name="cross")
    state = launcher.state()
    linked = state.processes[0].model_copy(
        update={"links": [Link(label="docs", href="/p/docs")]}
    )
    state = state.model_copy(update={"processes": [linked]})

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        linked.model_dump()
        flat = linked.model_dump(mode="json")
        state.model_dump()
        state.model_dump(mode="json")

    assert flat["links"] == [{"label": "docs", "href": "/p/docs"}]


def test_an_edited_topology_is_refused_while_another_launchers_service_is_live_as_a_shipped_one(
    client, graph
):
    """The guard of every launch: nothing is cleared while a service no process of this Launcher
    left behind is live. The page cannot force it: the body is the module list, and nothing
    else."""
    graph.live = {module_iri(7)}

    response = client.post("/api/topology/launch", json=ONE_WAY_PAIR)

    assert response.status_code == 409
    assert (
        "Module7 carries a fresh heartbeat in test-dataset" in response.json()["detail"]
    )
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_an_edited_topology_in_the_short_format_is_refused_naming_the_format_before_anything_is_cleared(
    client, graph
):
    short = {
        "modules": [
            {
                "id": "Module1",
                "label": "Module 1",
                "type": "EntryModule",
                "connections": [],
            }
        ]
    }

    response = client.post("/api/topology/launch", json=short)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        'This is a JSON object with its modules under "modules", the short topology document, '
        f"which use case 2 reads no longer. {topology.TOPOLOGY_FORMAT}"
    )
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_the_first_iterations_cross_file_passes_the_check_and_launches_as_it_is(
    client, graph
):
    first = json.loads(FIRST_ITERATIONS_CROSS.read_text(encoding="utf-8"))

    assert client.post("/api/topology/check", json=first).json() == {
        "passes": True,
        "reason": None,
    }
    assert client.post("/api/topology/launch", json=first).status_code == 200
    state = client.get("/api/state").json()
    assert [(p["name"], p["kind"]) for p in state["processes"]][:5] == [
        ("Module1", "module"),
        ("Module2", "module"),
        ("Module3", "module"),
        ("Module4", "module"),
        ("Module5", "module"),
    ]
    assert graph.seeded == [load_topology(str(FIRST_ITERATIONS_CROSS))]


def test_an_edited_launch_the_graph_refuses_stops_everything_and_says_why(
    client, graph
):
    graph.refusal = "the store is away"

    response = client.post("/api/topology/launch", json=ONE_WAY_PAIR)

    assert response.status_code == 502
    assert response.json()["detail"] == "The launch failed: the store is away"
    assert client.get("/api/state").json()["topology"] is None


def test_a_live_process_links_its_service_and_the_monitor_its_page(
    client, graph, cross
):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1, MONITOR}
    graph.addresses = {M1: "http://127.0.0.1:4001", MONITOR: "http://127.0.0.1:4002"}

    eventually(lambda: process(client, MONITOR)["state"] == "live")
    eventually(lambda: process(client, M1)["state"] == "live")

    # Each through the Launcher's own port, under the process's own path: relative, so that they
    # reach it at whatever address the page was opened.
    assert process(client, M1)["links"] == [
        {"label": "docs", "href": "p/Module1/docs"},
        {"label": "activity", "href": "p/Module1/activity"},
        {"label": "dashboard", "href": "p/Module1/dashboard"},
        {"label": "logs", "href": "p/Module1/dashboard?tab=Logs"},
    ]
    assert client.get("/api/state").json()["monitor"] == "p/Monitor1/"
    assert process(client, module_iri(2))["links"] == []


def test_the_state_s_monitor_is_the_page_its_row_links_as_open_wherever_the_row_lists_it(
    client, graph, monkeypatch
):
    """The monitor's link is a field of the state, `monitor`: the page its row links as "open",
    found by that label and not by its place among the row's links."""
    monitor = backend._OFFERS["monitor"]
    monkeypatch.setitem(
        backend._OFFERS, "monitor", monitor._replace(pages=monitor.pages[::-1])
    )
    client.post("/api/launch", json={"name": "cross"})
    assert client.get("/api/state").json()["monitor"] is None

    graph.live = {MONITOR}
    graph.addresses = {MONITOR: "http://127.0.0.1:4002"}
    eventually(lambda: process(client, MONITOR)["state"] == "live")

    assert process(client, MONITOR)["links"] == [
        {"label": "activity", "href": "p/Monitor1/activity"},
        {"label": "open", "href": "p/Monitor1/"},
    ]
    assert client.get("/api/state").json()["monitor"] == "p/Monitor1/"


def test_the_launcher_links_the_dashboard_and_the_logs_of_each_live_module_and_none_for_the_monitor(
    client, graph
):
    """A module's process serves the middleware's state dashboard. The monitor has a page of its
    own, which its row links already, so its row links no dashboard."""
    client.post("/api/launch", json={"name": "cross"})
    graph.addresses = {
        module_iri(1): "http://127.0.0.1:4001",
        module_iri(2): "http://127.0.0.1:4002",
        module_iri(3): "http://127.0.0.1:4003",
        module_iri(4): "http://127.0.0.1:4004",
        module_iri(5): "http://127.0.0.1:4005",
        MONITOR: "http://127.0.0.1:4010",
    }
    graph.live = set(graph.addresses)
    eventually(
        lambda: all(process(client, r)["state"] == "live" for r in graph.addresses)
    )

    def dashboards(resource: IRI) -> list[str]:
        links = process(client, resource)["links"]
        return [link["href"] for link in links if link["label"] == "dashboard"]

    assert dashboards(module_iri(1)) == ["p/Module1/dashboard"]
    assert dashboards(module_iri(2)) == ["p/Module2/dashboard"]
    assert dashboards(module_iri(3)) == ["p/Module3/dashboard"]
    assert dashboards(module_iri(4)) == ["p/Module4/dashboard"]
    assert dashboards(module_iri(5)) == ["p/Module5/dashboard"]
    assert dashboards(MONITOR) == []

    def logs(resource: IRI) -> list[str]:
        links = process(client, resource)["links"]
        return [link["href"] for link in links if link["label"] == "logs"]

    for number in range(1, 6):
        assert logs(module_iri(number)) == [f"p/Module{number}/dashboard?tab=Logs"]
    assert logs(MONITOR) == []


def test_the_wms_links_its_api_docs_its_activity_and_its_dashboard_and_has_no_fault(
    client, graph
):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {WMS}
    graph.addresses = {WMS: "http://127.0.0.1:4003"}

    eventually(lambda: process(client, WMS)["state"] == "live")

    assert process(client, WMS)["links"] == [
        {"label": "docs", "href": "p/WarehouseManagementSystem1/docs"},
        {"label": "activity", "href": "p/WarehouseManagementSystem1/activity"},
        # On the WMS's own tab, where a person spawns boxes.
        {
            "label": "dashboard",
            "href": "p/WarehouseManagementSystem1/dashboard?tab=WMS",
        },
        # On the Logs tab, where a person reads the WMS's records.
        {
            "label": "logs",
            "href": "p/WarehouseManagementSystem1/dashboard?tab=Logs",
        },
    ]
    assert process(client, WMS)["faults"] is None


def test_the_faults_of_the_wms_cannot_be_armed(client, graph, faults):
    """The faults are a module's: handing a box on without asking, and completing a transport
    without releasing its box. The WMS has neither."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {WMS}
    graph.addresses = {WMS: "http://127.0.0.1:4003"}

    response = switch(client, WMS, "skip-reserve", True)

    assert response.status_code == 404
    assert faults.calls == []


def test_the_launcher_links_the_wms_s_dashboard_while_the_wms_is_live(client, graph):
    """The WMS's dashboard is where a person spawns a box. The page opens it from its header, on
    the WMS's own tab."""
    client.post("/api/launch", json={"name": "cross"})
    assert client.get("/api/state").json()["wms"] is None

    graph.live = {WMS}
    graph.addresses = {WMS: "http://127.0.0.1:4003"}
    eventually(lambda: process(client, WMS)["state"] == "live")

    assert (
        client.get("/api/state").json()["wms"]
        == "p/WarehouseManagementSystem1/dashboard?tab=WMS"
    )


def test_stopping_one_module_leaves_the_others_running(client, cross):
    client.post("/api/launch", json={"name": "cross"})

    response = client.post("/api/processes/stop", json={"resource": str(M1)})

    assert response.status_code == 200
    assert process(client, M1)["state"] == "stopped"
    assert process(client, module_iri(2))["state"] == "starting"


def test_stopping_a_process_that_does_not_exist_is_not_found(client, cross):
    client.post("/api/launch", json={"name": "cross"})

    response = client.post("/api/processes/stop", json={"resource": "urn:x"})

    assert response.status_code == 404


class Bug(KeyError, ValueError):
    """An error that no part of the Launcher raises as a refusal, as a bug raises one, of both
    the built-in classes that a route must not catch: `KeyError` and `ValueError`."""


def raises_a_bug(*args: object, **kwargs: object) -> None:
    raise Bug("a bug")


@pytest.mark.parametrize(
    ("seam", "method", "path", "body"),
    [
        (
            "launcher.stop",
            "POST",
            "/api/processes/stop",
            {"json": {"resource": str(M1)}},
        ),
        (
            "launcher.set_fault",
            "POST",
            "/api/modules/faults",
            {"json": {"module": str(M1), "name": "skip-reserve", "armed": True}},
        ),
        ("launcher.launch", "POST", "/api/launch", {"json": {"name": "pair"}}),
        ("launcher.clear", "POST", "/api/clear", {}),
        ("launcher.process", "GET", "/p/Module1/docs", {}),
        ("api.shipped_topology", "POST", "/api/launch", {"json": {"name": "pair"}}),
        ("api.shipped_topology", "GET", "/api/topologies/pair", {}),
        ("api.edited_topology", "POST", "/api/topology/check", {"json": []}),
        ("api.edited_topology", "POST", "/api/topology/launch", {"json": []}),
        ("api.parse_topology", "POST", "/api/topology/layout", {"json": []}),
        ("api.parse_topology_file", "POST", "/api/topology/file", {"content": b"[]"}),
    ],
)
def test_an_error_that_is_no_refusal_of_the_backend_goes_through_the_route_as_it_is(
    client, launcher, monkeypatch, seam, method, path, body
):
    """A route answers with an error only for a refusal of the backend's own, each of a class of
    its own: a process or a module that the command names and that does not run, a topology that
    loading refuses, a name that the package ships no topology by, and a launch or a clear that
    is refused or failed. Any other error, as a bug's `KeyError` or `ValueError`, goes through the
    route as it is, and the server answers it as the fault it is, rather than as a refusal that
    names a wrong cause, such as "no process" or a reason why a topology does not load."""
    owner, name = seam.split(".")
    monkeypatch.setattr({"launcher": launcher, "api": api}[owner], name, raises_a_bug)

    with pytest.raises(Bug):
        client.request(method, path, **body)


def test_stopping_all_stops_every_process(client, cross):
    client.post("/api/launch", json={"name": "cross"})

    client.post("/api/processes/stop-all")

    states = {p["state"] for p in client.get("/api/state").json()["processes"]}
    assert states == {"stopped"}


def test_arming_a_fault_reaches_the_module_at_its_address(client, graph, faults, cross):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")

    response = switch(client, M1, "skip-reserve", True)

    assert response.status_code == 200
    assert faults.calls == [("http://127.0.0.1:4001", "skip-reserve", True)]
    assert process(client, M1)["faults"] == {
        "skip-reserve": True,
        "complete-without-releasing": False,
    }
    assert process(client, MONITOR)["faults"] is None


def test_a_switch_goes_to_the_address_the_supervisor_holds_and_reads_no_graph(
    client, graph, faults, unwatched
):
    """The address is the one read when the module turned live, not the graph's now: a module
    run by hand on the same resource can win the graph's answer. So a switch reads nothing of the
    graph, and works while it is away."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    unwatched[-1].refresh()
    graph.addresses = {M1: "http://127.0.0.1:4999"}
    graph.away = "the store is away"
    reads = graph.reads

    response = switch(client, M1, "complete-without-releasing", True)

    assert response.status_code == 200
    assert faults.calls == [
        ("http://127.0.0.1:4001", "complete-without-releasing", True)
    ]
    assert graph.reads == reads


def test_a_switch_the_module_does_not_take_is_a_conflict_and_changes_nothing_held(
    client, graph, faults, cross
):
    """An offline module: the Launcher answers 409, and keeps what it held before."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")
    switch(client, M1, "skip-reserve", True)
    faults.unreachable = "Module 1 does not answer"

    response = switch(client, M1, "skip-reserve", False)

    assert response.status_code == 409
    assert response.json()["detail"] == "Module 1 does not answer"
    assert process(client, M1)["faults"]["skip-reserve"] is True


def test_a_fault_that_no_module_has_is_refused(client, graph, faults, cross):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")

    response = switch(client, M1, "drop-the-box", True)

    assert response.status_code == 422
    assert faults.calls == []


def test_polling_the_state_calls_neither_a_module_nor_the_graph(
    client, graph, faults, unwatched
):
    """What the page polls is answered from this process alone: a handler that fans out across
    processes on a timer goes stale exactly when one of them stops answering. The graph is read by
    the supervisor's watch, which this test runs by hand, so every read it counts is a poll's."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    unwatched[-1].refresh()
    switch(client, M1, "skip-reserve", True)
    reads = graph.reads

    polls = [client.get("/api/state").json() for _ in range(20)]

    # The one arming call, and nothing the twenty polls added.
    assert faults.calls == [("http://127.0.0.1:4001", "skip-reserve", True)]
    assert graph.reads == reads
    # What they answered is the live state and its links all the same.
    m1 = next(p for p in polls[-1]["processes"] if p["resource"] == str(M1))
    assert m1["state"] == "live"
    assert m1["links"][0] == {"label": "docs", "href": "p/Module1/docs"}
    assert m1["address"] == "http://127.0.0.1:4001"


def test_while_the_graph_is_away_the_state_answers_and_marks_what_it_cannot_confirm(
    client, graph, unwatched
):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    unwatched[-1].refresh()

    graph.away = "the store is away"
    with pytest.raises(ConnectionError):
        unwatched[-1].refresh()
    response = client.get("/api/state")

    assert response.status_code == 200
    state = response.json()
    assert state["heartbeat_error"] == "the store is away"
    m1 = next(p for p in state["processes"] if p["resource"] == str(M1))
    # The last state read, with its links, marked as unconfirmed since then.
    assert m1["state"] == "live"
    assert m1["links"][0] == {"label": "docs", "href": "p/Module1/docs"}
    assert m1["address"] == "http://127.0.0.1:4001"
    assert m1["unconfirmed_for"] >= 0


def test_a_process_whose_service_stopped_being_ready_says_why_it_failed(
    client, graph, unwatched
):
    """The state answer carries why, as the line printed of the turn says, and the note the
    page shows says it too."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    unwatched[-1].refresh()
    assert process(client, M1)["state"] == "live"

    graph.live = set()
    stale = datetime.now(UTC) - timedelta(seconds=120)
    graph.left[M1] = Service(IRI(f"{M1}_service"), M1, "http://127.0.0.1:4001", stale)
    unwatched[-1].refresh()

    m1 = process(client, M1)
    assert m1["state"] == "failed"
    assert m1["why_failed"] == "its last heartbeat is 120 s old"
    assert m1["state_note"] == "Its last heartbeat is 120 s old."


# Ignores the platform's graceful stop, SIGTERM on POSIX and CTRL_BREAK on Windows.
STUBBORN = [
    sys.executable,
    "-c",
    (
        "import signal, time\n"
        "for name in ('SIGTERM', 'SIGBREAK'):\n"
        "    if hasattr(signal, name):\n"
        "        signal.signal(getattr(signal, name), signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    ),
]


def test_a_process_killed_at_the_stop_timeout_is_shown_as_killed(launcher_with):
    """A kill skips the deregistration, so the page must not call it stopped."""
    launcher = launcher_with(module_command=module_1_runs(STUBBORN), stop_timeout=0.5)
    with TestClient(create_app(launcher)) as client:
        client.post("/api/launch", json={"name": "cross"})
        eventually(lambda: "ready" in process(client, M1)["output"])

        client.post("/api/processes/stop", json={"resource": str(M1)})
        client.post("/api/processes/stop", json={"resource": str(module_iri(2))})

        m1, m2 = process(client, M1), process(client, module_iri(2))
        notice = "Module 1 did not stop within 0.5 s, and was killed."
        assert m1["state"] == "killed"
        assert m1["output"][-1] == notice
        assert m1["stoppable"] is False
        assert "skips the deregistration" in m1["state_note"]
        assert m2["state"] == "stopped"
        assert m2["state_note"] is None


def test_the_faults_shown_are_the_ones_the_launcher_commanded(
    client, graph, faults, cross
):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")

    switch(client, M1, "skip-reserve", True)
    switch(client, M1, "complete-without-releasing", True)
    assert process(client, M1)["faults"] == {
        "skip-reserve": True,
        "complete-without-releasing": True,
    }

    switch(client, M1, "skip-reserve", False)
    assert process(client, M1)["faults"] == {
        "skip-reserve": False,
        "complete-without-releasing": True,
    }


def test_a_module_the_launcher_never_armed_shows_both_faults_off(client, graph, cross):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}

    eventually(lambda: process(client, M1)["state"] == "live")

    assert process(client, M1)["faults"] == ALL_OFF


def test_a_relaunch_forgets_the_armed_faults_because_the_processes_are_new(
    client, graph, cross
):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")
    switch(client, M1, "skip-reserve", True)
    switch(client, M1, "complete-without-releasing", True)
    graph.live.clear()  # Module 1 deregisters as the relaunch stops it.

    assert client.post("/api/launch", json={"name": "cross"}).status_code == 200

    assert process(client, M1)["faults"] == ALL_OFF


def test_stopping_a_module_forgets_its_armed_faults(client, graph, cross):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")
    switch(client, M1, "skip-reserve", True)

    client.post("/api/processes/stop", json={"resource": str(M1)})

    assert process(client, M1)["faults"] == ALL_OFF


def test_arming_a_fault_of_a_module_without_a_service_is_a_conflict(
    client, faults, cross
):
    client.post("/api/launch", json={"name": "cross"})

    response = switch(client, M1, "skip-reserve", True)

    assert response.status_code == 409
    assert faults.calls == []


def test_a_launch_the_graph_refuses_stops_everything_and_says_why(client, graph):
    client.post("/api/launch", json={"name": "cross"})
    graph.refusal = "the store is away"

    response = client.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 502
    assert "the store is away" in response.json()["detail"]
    state = client.get("/api/state").json()
    assert state["topology"] is None
    assert {p["state"] for p in state["processes"]} == {"stopped"}


def test_a_launch_refuses_while_another_launchers_service_is_live_and_says_why(
    client, graph
):
    graph.live = {module_iri(7)}

    response = client.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 409
    assert (
        "Module7 carries a fresh heartbeat in test-dataset"
        in (response.json()["detail"])
    )
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_a_launch_that_cannot_ask_the_graph_what_is_live_clears_nothing_and_says_why(
    client, graph
):
    graph.away = "the store is away"

    response = client.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 502
    assert "the store is away" in response.json()["detail"]
    assert graph.seeded == []


def test_a_relaunch_refuses_while_a_resource_of_a_cleanly_stopped_process_is_live(
    client, graph, cross
):
    """A process of this Launcher that stops when asked deregisters first. A service still live on
    its resource is therefore another process's, for example a module run on its own from its
    command line. The relaunch refuses and clears nothing. The Launcher's processes stay stopped."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}

    response = client.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 409
    assert "Module1 carries a fresh heartbeat" in response.json()["detail"]
    assert graph.seeded == [cross]
    state = client.get("/api/state").json()
    assert state["topology"] == cross.model_dump()
    assert {p["state"] for p in state["processes"]} == {"stopped"}


@pytest.fixture
def module_1_is_stubborn(launcher_with, graph) -> Iterator[TestClient]:
    """The page of a Launcher whose Module 1 ignores the graceful stop, so a stop kills it."""
    launcher = launcher_with(module_command=module_1_runs(STUBBORN), stop_timeout=0.5)
    with TestClient(create_app(launcher)) as client:
        client.post("/api/launch", json={"name": "cross"})
        graph.live = {M1}
        eventually(lambda: "ready" in process(client, M1)["output"])
        client.post("/api/processes/stop-all")
        assert process(client, M1)["state"] == "killed"
        yield client


def test_a_relaunch_goes_on_over_the_service_of_a_process_the_launcher_killed(
    module_1_is_stubborn, graph, cross
):
    """The kill skipped Module 1's deregistration, so its service is still live. It is this
    Launcher's own dead process, and the seed clears what it left."""
    response = module_1_is_stubborn.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 200, response.text
    assert graph.seeded == [cross] * 2


def test_a_relaunch_over_a_killed_process_still_refuses_another_launchers_service(
    module_1_is_stubborn, graph, cross
):
    graph.live = {M1, module_iri(7)}

    response = module_1_is_stubborn.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("Module7 carries a fresh heartbeat")
    assert "Module1" not in detail
    assert graph.seeded == [cross]


def test_a_relaunch_goes_on_over_the_service_of_a_process_that_exited_on_its_own(
    launcher_with, graph, cross
):
    """A process that exits unasked may not have deregistered: a crash skips it, as a kill does."""
    with TestClient(
        create_app(launcher_with(module_command=module_1_runs(CRASHES)))
    ) as client:
        client.post("/api/launch", json={"name": "cross"})
        graph.live = {M1}
        eventually(lambda: process(client, M1)["state"] == "failed")

        response = client.post("/api/launch", json={"name": "cross"})

        assert response.status_code == 200, response.text
        assert graph.seeded == [cross] * 2


def stop_module_1(client: TestClient, graph: FakeGraph) -> None:
    assert client.post("/api/processes/stop", json={"resource": str(M1)}).is_success


def stop_all(client: TestClient, graph: FakeGraph) -> None:
    assert client.post("/api/processes/stop-all").is_success


def launch_refused_by_another_launcher(client: TestClient, graph: FakeGraph) -> None:
    """A launch stops this Launcher's processes before it asks the graph, then refuses."""
    graph.live.add(module_iri(7))
    assert client.post("/api/launch", json={"name": "cross"}).status_code == 409
    graph.live.discard(module_iri(7))  # The other launcher is stopped.


@pytest.mark.parametrize(
    "reach", [stop_module_1, stop_all, launch_refused_by_another_launcher]
)
def test_a_crash_that_a_stop_has_reached_still_reads_failed_and_a_relaunch_goes_on_over_it(
    launcher_with, graph, cross, reach
):
    """The crash skipped Module 1's deregistration, so its service is still live. A stop that
    reaches the dead process later does not change that: it stays this Launcher's own."""
    with TestClient(
        create_app(launcher_with(module_command=module_1_runs(CRASHES)))
    ) as client:
        client.post("/api/launch", json={"name": "cross"})
        graph.live = {M1}
        eventually(lambda: process(client, M1)["state"] == "failed")

        reach(client, graph)
        assert process(client, M1)["state"] == "failed"

        response = client.post("/api/launch", json={"name": "cross"})

        assert response.status_code == 200, response.text
        assert graph.seeded == [cross] * 2


def test_a_relaunch_refuses_over_a_live_service_of_a_process_that_failed_but_ran(
    client, graph, unwatched
):
    """Module 1's heartbeat went stale while it ran, so it reads failed. The relaunch asks it to
    stop, and it deregisters as it goes. A service still live on its resource is another
    process's, for example a module run on its own from its command line."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    unwatched[-1].refresh()
    graph.live = set()
    unwatched[-1].refresh()
    assert process(client, M1)["state"] == "failed"
    graph.live = {M1}

    response = client.post("/api/launch", json={"name": "cross"})

    assert response.status_code == 409
    assert "Module1 carries a fresh heartbeat" in response.json()["detail"]
    assert process(client, M1)["state"] == "stopped"


class RegisteringGraph(FakeGraph):
    """A graph in which a process's service is live from its start until it is asked to stop.

    Each process is a few lines of Python that registers by creating a file named after its
    resource, and deregisters on SIGTERM by removing it, as a middleware deregisters when it
    stops. So the graph finds exactly the processes that are up and not yet stopped.
    """

    def __init__(self, registry: Path) -> None:
        super().__init__()
        self.registry = registry

    def command(self, resource: IRI) -> list[str]:
        marker = self.registry / quote(str(resource), safe="")
        return [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys, time\n"
                f"marker = pathlib.Path({str(marker)!r})\n"
                "def deregister(*_):\n"
                "    marker.unlink(missing_ok=True)\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, deregister)\n"
                "marker.touch()\n"
                "time.sleep(60)\n"
            ),
        ]

    def registered(self) -> set[IRI]:
        return {IRI(unquote(marker.name)) for marker in self.registry.iterdir()}

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return [
            *super().services(workflows),
            *live_services(self.registered(), workflows),
        ]


@pytest.mark.posix(
    reason="each process deregisters on SIGTERM, which a stop sends on POSIX"
)
def test_a_relaunch_asks_the_graph_only_after_the_launchers_own_services_deregistered(
    launcher_with, tmp_path, cross
):
    """Asked before the stop, the graph would find the Launcher's own seven processes live."""
    graph = RegisteringGraph(tmp_path)
    launcher = launcher_with(
        graph=graph,
        module_command=graph.command,
        wms_command=graph.command(WMS),
        monitor_command=graph.command(MONITOR),
    )
    with TestClient(create_app(launcher)) as client:
        client.post("/api/launch", json={"name": "cross"})
        eventually(lambda: len(graph.registered()) == 7)

        response = client.post("/api/launch", json={"name": "cross"})

        assert response.status_code == 200, response.text
        assert graph.seeded == [cross] * 2


def test_a_forced_launch_clears_the_dataset_although_a_service_is_live(
    launcher, graph, cross
):
    graph.live = {module_iri(7)}

    launcher.launch(cross, name="cross", force=True)

    assert graph.seeded == [cross]


def test_the_picker_is_offered_every_shipped_topology(client):
    assert client.get("/api/topologies").json() == [
        "cross",
        "grid",
        "line",
        "merge-fault-demo",
        "pair",
        "tee",
    ]


def test_the_page_gives_each_direction_a_column_in_the_order_the_backend_names_them(
    client,
):
    assert client.get("/api/directions").json() == ["North", "East", "South", "West"]


def test_a_crashed_process_may_not_be_stopped_before_or_after_a_stop_reaches_it(
    launcher_with, graph
):
    """A crash ended the process, so a stop has nothing left to ask. The page offers none."""
    with TestClient(
        create_app(launcher_with(module_command=module_1_runs(CRASHES)))
    ) as client:
        client.post("/api/launch", json={"name": "cross"})
        eventually(lambda: process(client, M1)["state"] == "failed")
        assert process(client, M1)["stoppable"] is False

        client.post("/api/processes/stop", json={"resource": str(M1)})

        m1 = process(client, M1)
        assert m1["state"] == "failed"
        assert m1["stoppable"] is False


def test_a_running_process_may_be_stopped_and_a_stopped_one_may_not(client, cross):
    client.post("/api/launch", json={"name": "cross"})
    assert process(client, M1)["stoppable"] is True

    client.post("/api/processes/stop", json={"resource": str(M1)})

    assert process(client, M1)["stoppable"] is False
    assert process(client, module_iri(2))["stoppable"] is True


def test_a_process_that_failed_but_runs_may_still_be_stopped(client, graph, unwatched):
    """Its heartbeat went stale, so it reads failed. Its process still runs, and a stop ends it."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    unwatched[-1].refresh()
    graph.live = set()
    unwatched[-1].refresh()
    assert process(client, M1)["state"] == "failed"

    assert process(client, M1)["stoppable"] is True


def test_the_fault_of_a_module_may_be_switched_once_its_live_address_is_known(
    client, graph, cross
):
    client.post("/api/launch", json={"name": "cross"})
    assert process(client, M1)["fault_switchable"] is False

    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    eventually(lambda: process(client, M1)["state"] == "live")

    assert process(client, M1)["fault_switchable"] is True
    assert process(client, module_iri(2))["fault_switchable"] is False


def test_the_fault_of_the_wms_and_the_monitor_is_never_switchable(client, graph):
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {WMS, MONITOR}
    graph.addresses = {WMS: "http://127.0.0.1:4003", MONITOR: "http://127.0.0.1:4004"}
    eventually(lambda: process(client, WMS)["state"] == "live")

    assert process(client, WMS)["fault_switchable"] is False
    assert process(client, MONITOR)["fault_switchable"] is False


def test_a_module_whose_heartbeat_went_stale_may_not_have_its_fault_switched(
    client, graph, unwatched
):
    """It reads failed and its address is forgotten, so a switch would find no module to ask."""
    client.post("/api/launch", json={"name": "cross"})
    graph.live = {M1}
    graph.addresses = {M1: "http://127.0.0.1:4001"}
    unwatched[-1].refresh()
    assert process(client, M1)["fault_switchable"] is True

    graph.live = set()
    unwatched[-1].refresh()

    assert process(client, M1)["state"] == "failed"
    assert process(client, M1)["fault_switchable"] is False


# -- The transit time of a launch ------------------------------------------------- #


def test_the_state_gives_the_default_of_the_pages_transit_time_field(client):
    assert client.get("/api/state").json()["default_transit_time"] == 3.0


def test_the_format_route_names_the_key_of_a_module_s_transit_time(client):
    names = client.get("/api/topology/format").json()

    assert names["transit_time"] == topology.TRANSIT_TIME_KEY


def test_a_launch_without_a_transit_time_seeds_the_launchers_default(client, graph):
    assert client.post("/api/launch", json={"name": "cross"}).status_code == 200

    assert graph.transit_seconds == [3.0]


@pytest.mark.parametrize("seconds", [0, 0.0, 0.5, 12, "0", "1.5"])
def test_a_launch_takes_the_transit_time_of_the_field_and_0_is_allowed(
    client, graph, seconds
):
    response = client.post(
        "/api/launch", json={"name": "cross", "transit_time": seconds}
    )

    assert response.status_code == 200
    assert graph.transit_seconds == [float(seconds)]


@pytest.mark.parametrize(
    "seconds", [-1, -0.001, "-2", "fast", "", True, [1], {}, "nan"]
)
def test_a_launch_with_a_transit_time_that_is_negative_or_no_number_is_refused_and_launches_nothing(
    client, graph, seconds
):
    response = client.post(
        "/api/launch", json={"name": "cross", "transit_time": seconds}
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith(
        "The transit time must be a number of seconds, 0 or more, not "
    )
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_an_edited_launch_takes_the_transit_time_of_the_field_too(client, graph):
    response = client.post("/api/topology/launch?transit_time=0.5", json=ONE_WAY_PAIR)

    assert response.status_code == 200
    assert graph.transit_seconds == [0.5]


@pytest.mark.parametrize("seconds", ["-1", "text", ""])
def test_an_edited_launch_with_a_transit_time_that_is_negative_or_no_number_is_refused(
    client, graph, seconds
):
    response = client.post(
        f"/api/topology/launch?transit_time={seconds}", json=ONE_WAY_PAIR
    )

    assert response.status_code == 422
    assert "The transit time must be a number of seconds" in response.json()["detail"]
    assert graph.seeded == []
    assert client.get("/api/state").json()["processes"] == []


def test_a_module_s_own_transit_time_comes_through_the_layout_and_the_editor_s_names(
    client,
):
    names = client.get("/api/topology/format").json()
    module_list = [
        {
            "id": names["modules"] + "Module1",
            names["type"]: [names["classes"] + "FlexConveyorModule"],
            names["transit_time"]: [2],
            names["connection"]: [],
        }
    ]

    layout = client.post("/api/topology/layout", json=module_list).json()

    assert [m["transit_seconds"] for m in layout["modules"]] == [2.0]


def pair_with_transit_time(seconds: object) -> list[dict]:
    return [
        module_entry(
            "Module1",
            "EntryModule",
            transit_time=seconds,
            links=[("East", "Module2")],
        ),
        module_entry("Module2", "ExitModule"),
    ]


def test_the_check_and_an_edited_launch_accept_a_module_s_own_transit_time_of_0(
    client, graph
):
    module_list = pair_with_transit_time(0)

    assert client.post("/api/topology/check", json=module_list).json() == {
        "passes": True,
        "reason": None,
    }
    assert client.post("/api/topology/launch", json=module_list).status_code == 200
    assert [m.transit_seconds for m in graph.seeded[0].modules] == [0.0, None]


@pytest.mark.parametrize("seconds", [-1, "fast", "", True])
def test_the_check_and_an_edited_launch_refuse_a_module_s_own_transit_time_that_is_negative_or_no_number(
    client, graph, seconds
):
    module_list = pair_with_transit_time(seconds)

    checked = client.post("/api/topology/check", json=module_list).json()
    launched = client.post("/api/topology/launch", json=module_list)

    assert checked["passes"] is False
    assert "its transit time" in checked["reason"]
    assert launched.status_code == 422
    assert graph.seeded == []


# -- A topology with no module -------------------------------------------------- #

NO_MODULE = "a topology needs at least one module"


def test_the_check_fails_an_empty_module_list_with_the_reason(client, graph):
    response = client.post("/api/topology/check", json=[])

    assert response.status_code == 200
    assert response.json() == {"passes": False, "reason": NO_MODULE}
    assert graph.seeded == []


def test_an_empty_module_list_is_launched_through_the_api_as_a_refusal_and_nothing_is_stopped(
    client, graph, cross
):
    """Nothing is seeded, and what runs keeps running."""
    client.post("/api/launch", json={"name": "cross"})

    response = client.post("/api/topology/launch", json=[])

    assert response.status_code == 422
    assert response.json()["detail"] == NO_MODULE
    assert graph.seeded == [cross]
    state = client.get("/api/state").json()
    assert state["topology_name"] == "cross"
    assert {p["state"] for p in state["processes"]} == {"starting"}


def test_a_topology_file_with_an_empty_module_list_still_opens_as_an_empty_grid(client):
    response = upload(client, b"[]")

    assert response.status_code == 200, response.text
    assert response.json()["modules"] == []
    assert response.json()["places"] == {}


def test_a_topology_of_one_module_passes_the_check_and_launches(client, graph):
    one = [module_entry("Module1", label="Module 1")]

    checked = client.post("/api/topology/check", json=one)
    launched = client.post("/api/topology/launch", json=one)

    assert checked.json() == {"passes": True, "reason": None}
    assert launched.status_code == 200, launched.text
    assert graph.seeded == [parse_topology(one)]
