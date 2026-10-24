"""No service of use case 1 writes state to the store through SPARQL: every write it makes goes
through its middleware, `mw.commit()`, `mw.create()` or `mw.issue_operation()`, which commits
through the object-graph mapper.

The services are the planner, the screwing resource, the anomaly detector and the learner: every
module of `uc1/` but the seed, which writes the demo before any service starts. A write past the
middleware is a write of the store's client, a SPARQL update sent as it is, a write of the
object-graph mapper called on its own, or the text of a SPARQL update or of a store's statements
path anywhere in a module, where it could be sent by other means. Only the launcher, `jms-uc1`
(`run_core.py`, `__main__.py` and `infeasible_operation.py`, the run of
`jms-uc1 infeasible-operation`), may import a writer of the setup, because it seeds before it
starts any service.

A SPARQL query that only reads is no write, and stays: the planner's list of grinders, the
learner's list of completed ScrewIntact operations, and the screwing resource's search for the
detector by its capability. The middleware's own registration of a service, which writes
directly, is the middleware's, not the service's, and lies outside this package's source; its
move to the mapper is the middleware's to make. The modules of `common/` that a service uses are
read by claim 7's scan, `test_uc2_writes.py`.

These are read from the source under `src/`, without a store.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from _source import SOURCE, sources
from _writes import OGM_WRITES, direct_writes, receiver_of

USE_CASE_1 = Path("uc1")

# The seed of the demo, which writes before any service starts.
SEED = USE_CASE_1 / "seed.py"

# The launcher of a run, `jms-uc1`, which seeds and then starts the services.
LAUNCHER = {
    USE_CASE_1 / "run_core.py",
    USE_CASE_1 / "__main__.py",
    USE_CASE_1 / "infeasible_operation.py",
}

# The writers of the setup, by the module that defines them.
SETUP_WRITERS = {
    "jms_kapps.uc1.seed": {"reset_uc1", "seed_demo", "seed_robot"},
    "jms_kapps.uc1.run_core": {"seed"},
    "jms_kapps.common.triplestore": {"reset", "clear_graph_by_graph"},
}

# The text of a SPARQL update, by its operations' keywords, or a store's statements path, which
# takes a write over HTTP.
SPARQL_UPDATE = re.compile(
    r"\b(INSERT|DELETE)\s+(DATA\b|WHERE\b|\{)"
    r"|\b(CLEAR|DROP|CREATE)\s+(SILENT\s+)?(GRAPH|DEFAULT|NAMED|ALL)\b"
    r"|\bLOAD\s+(SILENT\s+)?<"
    r"|/statements\b"
)

# A receiver that holds the object-graph mapper, however a service names it.
MAPPER = re.compile(r"ogm|mapper", re.IGNORECASE)


def services() -> list[Path]:
    """Every module of use case 1 but the seed, in a fixed order."""
    return [
        path
        for path in sources()
        if path.relative_to(SOURCE).parts[0] == USE_CASE_1.name
        and path.relative_to(SOURCE) != SEED
    ]


def update_texts(tree: ast.AST) -> list[str]:
    """Every string in `tree` that holds the text of a SPARQL update, as `<line>: <the match>`."""
    return [
        f"{node.lineno}: {found.group(0)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for found in [SPARQL_UPDATE.search(node.value)]
        if found
    ]


def mapper_writes(tree: ast.AST) -> list[str]:
    """Every write of the object-graph mapper called on its own in `tree`, whatever the name of
    the receiver that holds it, as `<line>: <receiver>.<method>`."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = receiver_of(node.func.value)
        if node.func.attr in OGM_WRITES and MAPPER.search(name):
            found.append(f"{node.lineno}: {name}.{node.func.attr}")
    return found


def setup_imports(tree: ast.AST) -> list[str]:
    """Every import of a writer of the setup in `tree`, as `<line>: <module>.<name>`."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in SETUP_WRITERS:
            found += [
                f"{node.lineno}: {node.module}.{alias.name}"
                for alias in node.names
                if alias.name in SETUP_WRITERS[node.module] | {"*"}
            ]
    return found


def test_the_services_are_found():
    found = {path.relative_to(SOURCE) for path in services()}

    assert {
        USE_CASE_1 / "planner.py",
        USE_CASE_1 / "screwing_resource.py",
        USE_CASE_1 / "anomaly_detector.py",
        USE_CASE_1 / "learner.py",
    } <= found
    assert SEED not in found


@pytest.mark.parametrize(
    "path", services(), ids=lambda path: str(path.relative_to(SOURCE))
)
@pytest.mark.paper_claim
def test_no_service_of_use_case_1_writes_past_its_middleware(path):
    """Paper claim 13 (§5.1), read as: no service of use case 1 writes
    state to the store through SPARQL, and every write it makes goes through its middleware and
    the object-graph mapper. Its SPARQL lookups read only, and stay."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert direct_writes(tree) == []
    assert mapper_writes(tree) == []
    assert update_texts(tree) == []
    if path.relative_to(SOURCE) not in LAUNCHER:
        assert setup_imports(tree) == []


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ('q = "INSERT DATA { <a> <b> <c> }"', ["1: INSERT DATA"]),
        ('q = f"DELETE {{ ?s ?p ?o }} WHERE {{ }}"', ["1: DELETE {"]),
        ('q = "DELETE WHERE { ?s ?p ?o }"', ["1: DELETE WHERE"]),
        ('q = "CLEAR GRAPH <g>"', ["1: CLEAR GRAPH"]),
        ('q = "DROP SILENT ALL"', ["1: DROP SILENT ALL"]),
        ('q = "LOAD <http://x>"', ["1: LOAD <"]),
        ('httpx.post(f"{url}/repositories/{name}/statements")', ["1: /statements"]),
        ('q = "SELECT ?s WHERE { ?s ?p ?o }"', []),
        ('"""The planner does not delete a Screw or insert one."""', []),
    ],
)
def test_the_text_of_a_sparql_update_is_found(code, found):
    assert update_texts(ast.parse(code)) == found


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ("self._ogm.commit(changes=[])", ["1: _ogm.commit"]),
        ("mapper.create(class_iri=c)", ["1: mapper.create"]),
        ("OGM_.delete(x)", ["1: OGM_.delete"]),
        ("self.mw.commit(entry=[])", []),
        ("middleware.create(c, scope)", []),
        ("ogm.fetch(instance_iri=x, class_scope=s)", []),
    ],
)
def test_a_write_of_the_mapper_is_found_by_any_name_of_its_receiver(code, found):
    assert mapper_writes(ast.parse(code)) == found


@pytest.mark.parametrize(
    ("code", "found"),
    [
        (
            "from jms_kapps.uc1.seed import RECORDING_OF, seed_demo",
            ["1: jms_kapps.uc1.seed.seed_demo"],
        ),
        (
            "from jms_kapps.common.triplestore import reset",
            ["1: jms_kapps.common.triplestore.reset"],
        ),
        ("from jms_kapps.uc1.run_core import seed", ["1: jms_kapps.uc1.run_core.seed"]),
        ("from jms_kapps.uc1.seed import *", ["1: jms_kapps.uc1.seed.*"]),
        ("from jms_kapps.uc1.seed import RECORDING_OF, SCREW", []),
        ("from jms_kapps.common.triplestore import bindings, connect", []),
    ],
)
def test_an_import_of_a_writer_of_the_setup_is_found(code, found):
    assert setup_imports(ast.parse(code)) == found


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ("db.graph_clear()", ["1: db.graph_clear"]),
        ("db.shapes_set(turtle)", ["1: db.shapes_set"]),
    ],
)
def test_the_clients_writes_of_the_graph_and_its_shapes_are_direct_writes(code, found):
    assert direct_writes(ast.parse(code)) == found
