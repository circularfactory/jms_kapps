"""The block of addresses that the Launcher prints after each launch.

A process prints its own address, which stops working at the next launch, because every launch
gives each process a new port. The block names the pages through the Launcher's port instead. It
takes them from the table of the page's links, and a test compares the two for a launch of the
cross. What stands behind the Launcher is fake: the graph seeds nothing, every process sleeps.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Collection, Iterator
from urllib.parse import urljoin

import pytest
from _services import live_services
from _wait import eventually
from kapps_triplestore_interface import IRI

from jms_kapps.common.passthrough import process_id
from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import MONITOR, WMS

PAGE = "http://127.0.0.1:8090/"
RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
NOWHERE = "http://127.0.0.1:9"
HEADING = "Pages of the run, through the Launcher's port:"


class Graph:
    """Seeds nothing, and calls live each resource in `live`."""

    def __init__(self) -> None:
        self.live: set[IRI] = set()

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        pass

    def clear(self) -> int:
        return 0

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        live = set(self.live)
        return live_services(live, workflows, dict.fromkeys(live, NOWHERE))


def launcher_with(
    echoed: list[str], graph: Graph, page_address: str | None = PAGE
) -> Launcher:
    return Launcher(
        graph=graph,
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=echoed.append,
        page_address=page_address,
        stop_timeout=5.0,
    )


def blocks(echoed: list[str]) -> list[str]:
    return [text for text in echoed if HEADING in text]


def addresses(block: str) -> dict[str, str]:
    """The block's rows: the name of each page, and the address after it."""
    return {
        match[1].strip(): match[2]
        for match in (
            re.match(r"  (.+?)\s{2,}(http.*)", line) for line in block.splitlines()
        )
        if match
    }


@pytest.fixture
def graph() -> Graph:
    return Graph()


@pytest.fixture
def launched(graph: Graph) -> Iterator[tuple[Launcher, list[str]]]:
    echoed: list[str] = []
    launcher = launcher_with(echoed, graph)
    cross = load_topology("cross")
    graph.live = {m.resource for m in cross.modules} | {MONITOR, WMS}
    launcher.launch(cross, name="cross", force=True)
    yield launcher, echoed
    launcher.stop_all()


def test_a_launch_prints_one_block_between_blank_lines(launched):
    _, echoed = launched

    (block,) = blocks(echoed)
    assert block.startswith("\n" + HEADING)
    assert block.endswith("\n")
    assert set(addresses(block)) == {
        "Launcher page",
        "WMS dashboard",
        "Monitor",
        "A module's pages",
    }
    assert addresses(block)["Launcher page"] == PAGE
    assert addresses(block)["WMS dashboard"].endswith("/dashboard?tab=WMS")
    assert "/p/<id>/" in addresses(block)["A module's pages"]


def test_each_launch_prints_the_block_again(launched, graph):
    launcher, echoed = launched
    graph.live = set()

    launcher.launch(load_topology("cross"), name="cross")

    assert len(blocks(echoed)) == 2
    assert blocks(echoed)[0] == blocks(echoed)[1]


def test_each_address_of_the_block_is_the_one_the_page_links(launched):
    launcher, echoed = launched
    eventually(lambda: all(p.child.state == "live" for p in launcher.state().processes))
    state = launcher.state()
    (block,) = blocks(echoed)
    shown = addresses(block)

    assert shown["WMS dashboard"] == urljoin(PAGE, state.wms)
    assert shown["Monitor"] == urljoin(PAGE, state.monitor)
    # A module's pages: the block names the form, `p/<id>/` and the page paths, and the page
    # links the same paths below each module's own id.
    module = next(p for p in state.processes if p.kind == "module")
    form = (
        shown["A module's pages"]
        .split()[0]
        .replace("<id>", process_id(module.child.resource))
    )
    paths = shown["A module's pages"].split("then one of: ")[1].split(", ")
    assert [urljoin(PAGE, link.href) for link in module.links] == [
        urljoin(form, path) for path in paths
    ]


def test_a_launcher_without_an_address_prints_no_block(graph):
    echoed: list[str] = []
    launcher = launcher_with(echoed, graph, page_address=None)
    graph.live = {MONITOR, WMS}
    try:
        launcher.launch(load_topology("cross"), name="cross", force=True)
    finally:
        launcher.stop_all()

    assert blocks(echoed) == []
