"""Use case 2's SHACL-SPARQL rule lives in a file of its own, and only a caller that asks for it
loads it.

The shapes that every launch seeds are SHACL Core, which the store validates incrementally. The
one rule in SHACL-SPARQL has a SPARQL target, which the store evaluates over every transport at
every commit, so a launch and the measurement harness must not load it. The shape tests,
the audit and the SHACL-SPARQL demo do. Without a store: a stand-in records every request of the
seed.
"""

from __future__ import annotations

import inspect

import pytest
from _loopback import LOOPBACK_ENDPOINT
from rdflib import Graph
from rdflib.namespace import RDF, SH

from jms_kapps.common.triplestore import read_asset
from jms_kapps.evaluation.seed import seed_with_prefill
from jms_kapps.uc2 import seed
from jms_kapps.uc2.launcher.backend import TripleStoreGraph as LauncherGraph
from jms_kapps.uc2.run_core import TripleStoreGraph
from jms_kapps.uc2.topology import load_topology

CORE = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_shapes.ttl")
SPARQL = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_sparql_shapes.ttl")

# The terms of SHACL-SPARQL: a SPARQL target, a SPARQL constraint, and what they carry.
SPARQL_PREDICATES = {SH.sparql, SH.select, SH.ask, SH.declare, SH.prefixes, SH.target}
SPARQL_TYPES = {
    SH.SPARQLTarget,
    SH.SPARQLConstraint,
    SH.SPARQLRule,
    SH.SPARQLSelectExecutable,
}


class Recorder:
    """A dataset as a seed sees it: it keeps nothing and records the shapes it is sent."""

    dataset = "recorder"
    _endpoint = LOOPBACK_ENDPOINT

    def __init__(self) -> None:
        self.shapes: list[str] = []

    def graph_clear(self, graph_iri=None) -> bool:
        return True

    def graph_add(self, content, **kwargs) -> bool:
        return True

    def shapes_set(self, data, content_type) -> None:
        self.shapes.append(data)

    def query(self, query, update=False, **kwargs):
        return True if update else {"boolean": False, "results": {"bindings": []}}

    def prefixes_get(self) -> dict[str, str]:
        from kapps_triplestore_interface import TripleStoreNotSupportedError

        raise TripleStoreNotSupportedError("no prefix registry")


def test_the_core_shapes_file_holds_no_sparql_construct():
    graph = Graph().parse(data=CORE, format="turtle")

    assert not SPARQL_PREDICATES & set(graph.predicates())
    assert not SPARQL_TYPES & set(graph.objects(None, RDF.type))
    assert any(graph.subjects(RDF.type, SH.NodeShape))


def test_the_sparql_shapes_file_holds_the_reachable_destination_rule():
    graph = Graph().parse(data=SPARQL, format="turtle")

    assert SPARQL_PREDICATES & set(graph.predicates())
    assert SPARQL_TYPES & set(graph.objects(None, RDF.type))


def test_the_two_files_make_one_document_when_asked_and_the_core_file_alone_otherwise():
    assert seed.shapes_document(sparql_shapes=False) == CORE
    both = seed.shapes_document(sparql_shapes=True)
    assert CORE in both
    assert SPARQL in both
    Graph().parse(data=both, format="turtle")


@pytest.mark.parametrize(
    "function",
    [
        seed.reset_uc2,
        seed.seed_topology,
        seed.seed_two_modules,
        seed.seed_three_modules,
    ],
)
def test_no_seed_loads_the_sparql_shapes_unless_asked_to(function):
    assert inspect.signature(function).parameters["sparql_shapes"].default is False


def test_a_launch_seeds_the_core_shapes_only():
    store = Recorder()

    TripleStoreGraph(store).seed(load_topology("pair"), 0, collect_history=False)

    (sent,) = store.shapes
    assert CORE in sent
    assert SPARQL not in sent


def test_the_launcher_seeds_the_core_shapes_only():
    store = Recorder()

    LauncherGraph(store).seed(load_topology("pair"), 0, collect_history=False)

    (sent,) = store.shapes
    assert CORE in sent
    assert SPARQL not in sent


def test_the_evaluation_harness_seeds_the_core_shapes_only():
    store = Recorder()

    seed_with_prefill(
        store,
        load_topology("pair"),
        transit_seconds=0,
        prefill=None,
        collect_history=False,
    )

    (sent,) = store.shapes
    assert CORE in sent
    assert SPARQL not in sent
