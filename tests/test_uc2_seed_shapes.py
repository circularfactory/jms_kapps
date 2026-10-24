"""Use case 2's seed sets its shapes through the store's `shapes_set`, and a SHACL-off seed sets
none.

A dataset without SHACL validation refuses `shapes_set` as not supported: shapes stored there
would validate nothing. So the seed's default refuses such a dataset, before it loads anything,
and only a seed that the caller asks to skip the shapes, `with_shapes=False`, as a run of the
measurement harness on its SHACL-off twin does, seeds it, with no shapes. Without a store: a
stand-in records every request of the seed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _loopback import LOOPBACK_ENDPOINT
from kapps_triplestore_interface import TripleStoreNotSupportedError

import jms_kapps
from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.topology import load_topology

UC2_ONTOLOGY = read_asset("jms_kapps.uc2", "ontology", "flexconveyor_usecase.ttl")
UC2_SHAPES = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_shapes.ttl")
UC2_SPARQL_SHAPES = read_asset(
    "jms_kapps.uc2", "shapes", "flexconveyor_usecase_sparql_shapes.ttl"
)


@dataclass
class StandIn:
    """A dataset as a seed sees it, through the store's contract: it keeps nothing and records
    every request in order, `("clear", graph)`, `("import", content)`, `("shapes", data)` and
    `("query", text)`. With `shacl=False` it has no SHACL validation, and refuses `shapes_set`
    as the contract says such a dataset does. It has no prefix registry either."""

    shacl: bool = True
    dataset: str = "stand-in"
    _endpoint = LOOPBACK_ENDPOINT
    sent: list[tuple[str, object]] = field(default_factory=list)

    def graph_clear(self, graph_iri=None) -> bool:
        self.sent.append(("clear", graph_iri))
        return True

    def graph_add(self, content, **kwargs) -> bool:
        self.sent.append(("import", content))
        return True

    def shapes_set(self, data, content_type) -> None:
        if not self.shacl:
            raise TripleStoreNotSupportedError("the dataset has no SHACL validation")
        self.sent.append(("shapes", data))

    def query(self, query, update=False, **kwargs):
        self.sent.append(("query", str(query)))
        return True if update else {"boolean": False, "results": {"bindings": []}}

    def prefixes_get(self) -> dict[str, str]:
        raise TripleStoreNotSupportedError("no prefix registry")

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.sent]


def seed(store: StandIn, **options: object) -> None:
    seed_topology(store, load_topology("pair"), transit_seconds=0, **options)


def test_a_seed_sets_use_case_2_s_shapes_after_the_clear_and_before_the_ontology():
    store = StandIn()

    seed(store)

    (shapes,) = [i for i, (kind, _) in enumerate(store.sent) if kind == "shapes"]
    assert UC2_SHAPES in store.sent[shapes][1]
    assert store.kinds()[0] == "clear"
    assert shapes < store.sent.index(("import", UC2_ONTOLOGY))


def test_a_seed_sends_the_sparql_shapes_file_only_when_asked_to():
    default, asked = StandIn(), StandIn()

    seed(default)
    seed(asked, sparql_shapes=True)

    (core_only,) = [data for kind, data in default.sent if kind == "shapes"]
    (both,) = [data for kind, data in asked.sent if kind == "shapes"]
    assert UC2_SHAPES in core_only
    assert UC2_SHAPES in both
    assert UC2_SPARQL_SHAPES in both
    assert UC2_SPARQL_SHAPES not in core_only


def test_a_seed_refuses_a_dataset_without_shapes_validation_before_it_loads_anything():
    store = StandIn(shacl=False)

    with pytest.raises(TripleStoreNotSupportedError):
        seed(store)

    assert "import" not in store.kinds()


def test_a_shacl_off_seed_skips_the_shapes_and_seeds_the_rest():
    store = StandIn(shacl=False)

    seed(store, with_shapes=False)

    assert "shapes" not in store.kinds()
    assert ("import", UC2_ONTOLOGY) in store.sent
    # The topology, the seed's last import, after the ontology.
    assert store.kinds().count("import") >= 5


def test_a_shacl_off_seed_sends_no_shapes_to_a_dataset_that_has_validation_either():
    store = StandIn(shacl=True)

    seed(store, with_shapes=False)

    assert "shapes" not in store.kinds()


# Where a call may pass `with_shapes`: the seed's own chain, and the measurement harness, which
# passes its configuration's `shacl`. No demo path, no Launcher path and no `run-uc2` path does.
WITH_SHAPES_CALLERS = {
    Path("uc2/seed.py"),
    Path("evaluation/seed.py"),
    Path("evaluation/bench.py"),
    Path("evaluation/run_uc2.py"),
}


def test_only_the_seed_and_the_measurement_harness_pass_with_shapes():
    source = Path(jms_kapps.__file__).parent
    passing = {
        path.relative_to(source)
        for path in sorted(source.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and any(keyword.arg == "with_shapes" for keyword in node.keywords)
    }

    assert passing <= WITH_SHAPES_CALLERS, passing - WITH_SHAPES_CALLERS
    assert Path("evaluation/run_uc2.py") in passing
