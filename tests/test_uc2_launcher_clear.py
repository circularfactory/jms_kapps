"""The Launcher's Clear: stop every process, then empty the run's dataset graph by graph, and
say what it holds then (jms_kapps 5).

Nothing reaches a store. The clear itself, `triplestore.clear_graph_by_graph`, runs on a stand-in
of the interface's store that holds a number of statements in each graph and records every call
it is sent; a call the clear has no business sending, as a clear of the whole dataset, fails the
test. The Launcher's Clear runs through its JSON API, FastAPI's test client, over children that
only sleep and a fake graph that records each clear and what the processes were then, and answers
which services are live, as the guard of a launch reads them. (Added on 2026-10-10, jms_kapps 5,
decided by Etienne on 2026-10-05: "a clear button that kills every process and clears the whole
dataset", graph by graph.)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

import pytest
from _loopback import LOOPBACK_ENDPOINT
from _services import live_services
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_semantic_middleware.seeding import CORE_GRAPH, MES_GRAPH, SERVICE_GRAPH
from kapps_triplestore_interface import (
    IRI,
    ShaclValidationError,
    TripleStoreNotSupportedError,
    TripleStoreResponseError,
)
from rdflib import Dataset, Literal, URIRef

from jms_kapps.common.services import Service
from jms_kapps.common.triplestore import (
    ClearFailed,
    clear_graph_by_graph,
    statement_count,
)
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import module_iri

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# Ignores the platform's graceful stop, SIGTERM on POSIX and CTRL_BREAK on Windows, so that a stop
# kills it at the stop timeout, and it leaves its service behind.
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

# A named graph that another program wrote, and no reset clears.
WRITTEN = IRI("http://example.org/another-program/records")
# A named graph that no run made, as one a person loaded by hand.
BY_HAND = IRI("http://example.org/someone/notes")
# The graph in which the middleware keeps the receipts of writes that a stopped process left: a
# name that is no http(s) IRI, which the interface takes since kapps_triplestore_interface 32.
RECEIPTS = IRI("urn:x-kapps:write-receipts")

REPORT = (Path(__file__).parent / "reports" / "skip-reserve.nq").read_text(
    encoding="utf-8"
)


# -- The clear, graph by graph ------------------------------------------------------------------ #


class StandInStore:
    """A dataset as the interface's store contract reaches it, and nothing else: how many
    statements each graph holds, None the default graph, whether it holds shapes, and every call
    it is sent, in order.

    Its named graphs are listed by a query of the graphs that hold a statement; it clears one
    graph at a time, removes its shapes with an empty document, and answers a count with the
    statements it still holds. A clear may be made to fail for one graph (`refuses`), the removal
    of the shapes too (`refuses_shapes`), and the listing (`refuses_listing`). Any other call,
    such as a clear of the whole dataset, fails. It keeps a loopback endpoint as a class
    attribute, which the middleware's wipe guard reads, so that the strict lookup of other calls
    never fires for it.
    """

    dataset = "test-dataset"
    _endpoint = LOOPBACK_ENDPOINT

    def __init__(self, statements: dict[IRI | None, int], *, shapes: int = 90) -> None:
        self.statements = dict(statements)
        self.shapes = shapes
        self.calls: list[tuple] = []
        self.refuses: dict[IRI | None, Exception] = {}
        self.refuses_shapes: Exception | None = None
        self.refuses_listing: Exception | None = None
        # A graph the store holds statements in but does not list, as a store might.
        self.unlisted: set[IRI] = set()

    def graph_clear(self, graph_iri: IRI | None = None) -> bool:
        graph = None if graph_iri is None else IRI(graph_iri)
        self.calls.append(("graph_clear", graph))
        if graph in self.refuses:
            raise self.refuses[graph]
        self.statements.pop(graph, None)
        return True

    def shapes_set(self, data: str, content_type: str) -> None:
        self.calls.append(("shapes_set", data))
        assert data == "", "a clear removes the shapes, and sets none"
        if self.refuses_shapes is not None:
            raise self.refuses_shapes
        self.shapes = 0

    def query(self, query: str, *, convert_bindings: bool = True, update: bool = False):
        assert not update, "a clear sends no update"
        if "GRAPH ?graph" in query:
            self.calls.append(("query", "graphs"))
            if self.refuses_listing is not None:
                raise self.refuses_listing
            return {
                "results": {
                    "bindings": [
                        {"graph": {"type": "uri", "value": str(graph)}}
                        for graph, count in self.statements.items()
                        if graph is not None and count and graph not in self.unlisted
                    ]
                }
            }
        self.calls.append(("query", "count"))
        assert "COUNT" in query
        count = sum(self.statements.values())
        return {
            "results": {
                "bindings": [
                    {
                        "statements": {
                            "type": "literal",
                            "datatype": "http://www.w3.org/2001/XMLSchema#integer",
                            "value": str(count),
                        }
                    }
                ]
            }
        }

    def __getattr__(self, name: str):
        raise AssertionError(
            f"the clear called {name}, which it has no business calling"
        )


def a_run_s_dataset() -> StandInStore:
    """A dataset as a run leaves it, and more: the default graph, the shared ontologies, the
    shapes, a graph another program wrote, one a person loaded, and the middleware's receipts."""
    return StandInStore(
        {
            None: 412,
            CORE_GRAPH: 300,
            SERVICE_GRAPH: 120,
            MES_GRAPH: 40,
            WRITTEN: 142,
            BY_HAND: 3,
            RECEIPTS: 2,
        }
    )


def clears(store: StandInStore) -> list[IRI | str | None]:
    """The graphs `store` was asked to clear, in order, and "shapes" where it removed its
    shapes."""
    return [
        call[1] if call[0] == "graph_clear" else "shapes"
        for call in store.calls
        if call[0] in ("graph_clear", "shapes_set")
    ]


def test_every_graph_is_cleared_one_by_one_and_the_dataset_holds_0_statements():
    store = a_run_s_dataset()

    assert clear_graph_by_graph(store) == 0

    assert clears(store) == [
        None,
        CORE_GRAPH,
        SERVICE_GRAPH,
        MES_GRAPH,
        "shapes",
        WRITTEN,
        BY_HAND,
        RECEIPTS,
    ]
    assert store.statements == {}
    assert store.shapes == 0


def test_the_named_graphs_are_listed_before_anything_is_cleared_and_the_count_comes_last():
    store = a_run_s_dataset()

    clear_graph_by_graph(store)

    assert store.calls[0] == ("query", "graphs")
    assert store.calls[-1] == ("query", "count")
    assert [call[0] for call in store.calls[1:-1]] == [
        *["graph_clear"] * 4,
        "shapes_set",
        *["graph_clear"] * 3,
    ]


def test_an_empty_dataset_is_cleared_graph_by_graph_too():
    """The known graphs are cleared whether they hold anything or not, and the shapes removed: a
    clear of an empty graph clears nothing."""
    store = StandInStore({}, shapes=0)

    assert clear_graph_by_graph(store) == 0

    assert clears(store) == [None, CORE_GRAPH, SERVICE_GRAPH, MES_GRAPH, "shapes"]


def test_a_dataset_without_shapes_validation_is_cleared_and_the_clear_goes_on():
    """A dataset without SHACL validation has no shapes, and its store refuses `shapes_set` as
    not supported: the clear goes on with the graphs after it."""
    store = a_run_s_dataset()
    store.refuses_shapes = TripleStoreNotSupportedError("the dataset has no SHACL validation")

    assert clear_graph_by_graph(store) == 0

    assert clears(store)[-3:] == [WRITTEN, BY_HAND, RECEIPTS]


def test_shapes_the_store_refuses_to_remove_stop_the_clear_and_are_named_with_why():
    store = a_run_s_dataset()
    store.refuses_shapes = TripleStoreResponseError("the store is busy")

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    assert str(refused.value) == (
        "the store did not remove the shapes of test-dataset: the store is busy"
    )
    assert refused.value.nothing_cleared is False
    assert clears(store)[-1] == "shapes"
    assert WRITTEN in store.statements


def test_what_the_store_still_holds_is_counted_not_assumed():
    store = a_run_s_dataset()
    store.unlisted = {BY_HAND}

    assert clear_graph_by_graph(store) == 3


def test_a_graph_the_store_refuses_to_clear_stops_the_clear_and_is_named_with_why():
    store = a_run_s_dataset()
    store.refuses[WRITTEN] = TripleStoreResponseError(
        f"Failed to clear graph <{WRITTEN}> in dataset 'test-dataset' (500) - the "
        "store is busy"
    )

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    assert str(refused.value) == (
        f"the store did not clear the graph <{WRITTEN}> of test-dataset: "
        f"Failed to clear graph <{WRITTEN}> in dataset 'test-dataset' (500) - the "
        "store is busy"
    )
    # No clear after it is sent, and nothing is counted; the graphs before it were cleared.
    assert refused.value.nothing_cleared is False
    assert clears(store)[-1] == WRITTEN
    assert BY_HAND in store.statements
    assert ("query", "count") not in store.calls


def test_a_default_graph_the_store_refuses_to_clear_is_named_too():
    store = a_run_s_dataset()
    store.refuses[None] = TripleStoreResponseError("the store is away")

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    assert str(refused.value) == (
        "the store did not clear the default graph of test-dataset: the store is away"
    )
    assert refused.value.nothing_cleared is True
    assert clears(store) == [None]
    assert ("query", "count") not in store.calls


def test_a_clear_the_store_s_shapes_refuse_names_the_rules_not_the_whole_report():
    store = a_run_s_dataset()
    store.refuses[CORE_GRAPH] = ShaclValidationError(
        "Failed to clear graph ... (500) - " + REPORT, report=REPORT
    )

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    assert str(refused.value) == (
        f"the store did not clear the graph <{CORE_GRAPH}> of test-dataset: its shapes "
        "refused it, by FlexConveyorModulePossessionShape at Module3"
    )


def test_a_store_that_does_not_list_its_graphs_has_nothing_cleared_and_says_why():
    store = a_run_s_dataset()
    store.refuses_listing = ConnectionError("the store went away")

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    assert str(refused.value) == (
        "the store did not list the named graphs of test-dataset, so nothing was cleared: "
        "the store went away"
    )
    assert refused.value.nothing_cleared is True
    assert clears(store) == []


def test_the_middleware_s_receipts_graph_is_listed_and_cleared_as_any_other():
    """The middleware keeps the receipts of writes that a stopped process left in a graph whose
    name is no http(s) IRI, `urn:x-kapps:write-receipts`. The interface takes every absolute IRI
    as the name of a graph since kapps_triplestore_interface 32, so the clear lists it and clears
    it as any other graph. (Changed on 2026-10-11, jms_kapps 62: until then the interface refused
    the name, the listing raised, nothing was cleared, and the page said so.)"""
    store = StandInStore({RECEIPTS: 2})

    assert clear_graph_by_graph(store) == 0

    assert clears(store)[-1] == RECEIPTS
    assert store.statements == {}


class OnRdflib:
    """A store on rdflib's SPARQL engine, which is not GraphDB's, for the count's query alone. It
    runs a query without its FROM line, the scope of the written statements, since rdflib infers
    nothing. With `union`, its default graph takes in its named graphs, as GraphDB's does in a
    query without a scope; without it, the default graph is its own, as the documentation of
    GraphDB's scope of the written statements reads."""

    def __init__(self, *, union: bool) -> None:
        self.dataset = Dataset(default_union=union)

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        text = "\n".join(
            line for line in query.splitlines() if not line.startswith("FROM ")
        )
        return json.loads(self.dataset.query(text).serialize(format="json"))


@pytest.mark.parametrize("union", [False, True], ids=["own default", "union default"])
def test_the_count_takes_each_statement_once_whatever_the_default_scope_takes_in(union):
    store = OnRdflib(union=union)
    ex = "http://example.org/"
    for n in range(2):
        store.dataset.add((URIRef(f"{ex}a{n}"), URIRef(f"{ex}p"), Literal(n)))
    for n in range(3):
        store.dataset.graph(WRITTEN).add(
            (URIRef(f"{ex}b{n}"), URIRef(f"{ex}p"), Literal(n))
        )
    store.dataset.graph(BY_HAND).add((URIRef(f"{ex}c"), URIRef(f"{ex}p"), Literal(0)))

    assert statement_count(store) == 6


# -- The Launcher's Clear ------------------------------------------------------------------------ #


class FakeGraph:
    """Records each seed and each clear, and, at each clear, what the Launcher's processes were
    then, through `watching`. Answers which services are live, as the guard of a launch reads
    them, unless it is `away`. A clear answers `left` statements, or fails with `refusal`."""

    def __init__(self) -> None:
        self.seeded: list[Topology] = []
        self.cleared: list[list[tuple[str, bool]]] = []
        self.live: set[IRI] = set()
        self.away: str | None = None
        self.refusal: str | None = None
        # Whether the refusal came before any graph was cleared.
        self.refused_first = False
        self.left = 0
        self.watching: Launcher | None = None

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        self.seeded.append(topology)

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        if self.away is not None:
            raise ConnectionError(self.away)
        return live_services(self.live, workflows)

    def clear(self) -> int:
        assert self.watching is not None
        self.cleared.append(
            [
                (p.child.state, p.child.stoppable)
                for p in self.watching.state().processes
            ]
        )
        if self.refusal is not None:
            raise ClearFailed(self.refusal, nothing_cleared=self.refused_first)
        return self.left


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def launcher_with(graph) -> Iterator[Callable[..., Launcher]]:
    """Builds a Launcher on the fake graph, and stops each one it built."""
    built: list[Launcher] = []

    def build(
        *,
        module_command: Callable[[IRI], list[str]] = lambda resource: RUNS,
        stop_timeout: float = 5.0,
    ) -> Launcher:
        launcher = Launcher(
            graph=graph,
            dataset="test-dataset",
            module_command=module_command,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=stop_timeout,
        )
        graph.watching = launcher
        built.append(launcher)
        return launcher

    yield build
    for launcher in built:
        launcher.stop_all()


@pytest.fixture
def client(launcher_with) -> Iterator[TestClient]:
    with TestClient(create_app(launcher_with())) as client:
        yield client


def launched(client: TestClient) -> dict:
    """Launch the pair, as the page does, and answer the state."""
    response = client.post("/api/launch", json={"name": "pair"})
    assert response.status_code == 200, response.text
    return response.json()


def test_clear_stops_every_process_first_then_clears_and_says_the_dataset_is_empty(
    client, graph
):
    launched(client)

    response = client.post("/api/clear")

    assert response.status_code == 200, response.text
    assert response.json() == {"dataset": "test-dataset", "statements": 0}
    # One clear, and by then every process had stopped: none was left for a stop to ask.
    assert len(graph.cleared) == 1
    [processes] = graph.cleared
    assert len(processes) == 4  # two modules, the WMS and the monitor
    assert processes == [("stopped", False)] * 4


def test_after_a_clear_every_process_shows_stopped_and_nothing_shows_as_seeded(
    client, graph
):
    launched(client)

    client.post("/api/clear")

    state = client.get("/api/state").json()
    assert [p["state"] for p in state["processes"]] == ["stopped"] * 4
    assert state["topology"] is None
    assert state["topology_name"] is None
    assert state["module_strategy"] is None
    assert state["wms_strategy"] is None
    assert state["collect_history"] is None


def test_a_clear_answers_the_statements_the_store_still_holds(client, graph):
    launched(client)
    graph.left = 3

    response = client.post("/api/clear")

    assert response.status_code == 200
    assert response.json() == {"dataset": "test-dataset", "statements": 3}


def test_a_clear_is_refused_while_another_launcher_s_process_is_live_in_the_dataset(
    client, graph
):
    """The guard of a launch: this Launcher's own processes stop first, and the graph is then
    asked; a live service that is not one of them refuses the clear, and nothing is cleared."""
    state = launched(client)
    graph.live = {module_iri(9)}

    response = client.post("/api/clear")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith(
        "Module9 carries a fresh heartbeat in test-dataset, so nothing was cleared."
    )
    assert "another launcher runs there" in detail
    assert graph.cleared == []
    after = client.get("/api/state").json()
    assert [p["state"] for p in after["processes"]] == ["stopped"] * 4
    # Nothing was cleared, so the topology is still seeded.
    assert after["topology"] == state["topology"]
    assert after["topology_name"] == "pair"


def test_a_clear_leaves_out_this_launcher_s_own_killed_process(launcher_with, graph):
    """A process of this Launcher's own that a stop killed may have left its service live: the
    guard leaves it out, as it does for a launch, and the clear removes what it left."""
    m1 = module_iri(1)
    launcher = launcher_with(
        module_command=lambda resource: STUBBORN if resource == m1 else RUNS,
        stop_timeout=0.5,
    )
    with TestClient(create_app(launcher)) as client:
        launched(client)
        eventually(
            lambda: any(
                "ready" in line
                for p in client.get("/api/state").json()["processes"]
                if p["resource"] == str(m1)
                for line in p["output"]
            )
        )
        graph.live = {m1}

        response = client.post("/api/clear")

        assert response.status_code == 200, response.text
        [processes] = graph.cleared
        assert processes[0] == ("killed", False)


def test_a_clear_is_refused_when_the_graph_cannot_say_which_services_are_live(
    client, graph
):
    launched(client)
    graph.away = "the store went away"

    response = client.post("/api/clear")

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "could not read which services are live: the store went away"
    )
    assert graph.cleared == []


def test_a_graph_the_store_refuses_to_clear_is_said_with_its_reason(client, graph):
    launched(client)
    graph.refusal = (
        f"the store did not clear the graph <{WRITTEN}> of test-dataset: "
        "the store is busy"
    )

    response = client.post("/api/clear")

    assert response.status_code == 502
    assert response.json()["detail"] == graph.refusal
    # The graphs before it may be empty: nothing shows as seeded.
    assert client.get("/api/state").json()["topology"] is None


def test_a_clear_the_store_refuses_before_any_graph_is_cleared_leaves_the_topology_shown(
    client, graph
):
    launched(client)
    graph.refusal = (
        "the store did not list the named graphs of test-dataset, so nothing was "
        "cleared: the store went away"
    )
    graph.refused_first = True

    response = client.post("/api/clear")

    assert response.status_code == 502
    assert response.json()["detail"] == graph.refusal
    state = client.get("/api/state").json()
    assert state["topology_name"] == "pair"
    assert [p["state"] for p in state["processes"]] == ["stopped"] * 4


def test_a_clear_with_nothing_launched_clears_too(client, graph):
    response = client.post("/api/clear")

    assert response.status_code == 200
    assert graph.cleared == [[]]


def test_the_next_launch_after_a_clear_seeds_again(client, graph):
    launched(client)
    client.post("/api/clear")

    state = launched(client)

    pair = load_topology("pair")
    assert graph.seeded == [pair, pair]
    assert state["topology_name"] == "pair"
    assert [p["state"] for p in state["processes"]] == ["starting"] * 4
