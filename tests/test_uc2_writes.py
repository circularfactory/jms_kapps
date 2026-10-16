"""Every write that a party of use case 2 makes is a commit of the middleware.

A party writes only through a context manager of its middleware (`mw.create()`, `mw.commit()`,
`mw.handover()`), so the store admits or refuses each state transition before the work it
describes. Only the setup writes directly, before any party starts: the reset of a dataset and
the seed, which stand in for a running factory, a bench run's prefill after the seed (added on
2026-10-11, jms_kapps 47), the Launcher's Clear, which stops every process first, and the switch of
the store's history, the last step of a reset. A direct write is a write of the store's client, a
SPARQL update it sends as it is, or a write of the object-graph mapper called on its own. And
nothing imports the middleware's private `registration` module. (Changed on 2026-10-11, jms_kapps
19: the Launcher's Clear and the history switch count as reset and setup, decided by Etienne; that
a SPARQL update counts as a direct write is the build's reading, so that the scan sees the switch.
Until then the scan did not see an update, and the setup was the reset, the seed and the prefill.)

These are read from the source under `src/`, without a store.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _source import SOURCE, sources

# The setup, which runs before any party starts, and may write directly: the reset and the
# Launcher's Clear (`common/triplestore.py`), the seed, a bench run's prefill after the seed (added
# on 2026-10-11, jms_kapps 47), and the switch of the store's history, which a reset sends as its
# last step (`uc2/history.py`; added on 2026-10-11, jms_kapps 19).
SETUP = {
    Path("common/triplestore.py"),
    Path("uc1/seed.py"),
    Path("uc2/seed.py"),
    Path("evaluation/seed.py"),
    Path("uc2/history.py"),
}

# The writes of the store's client and of the object-graph mapper.
STORE_WRITES = {
    "clear_graph",
    "graph_add",
    "triple_add",
    "triple_delete",
    "triple_update",
    "triples_add",
    "triples_delete",
    "triples_update",
}
OGM_WRITES = {"commit", "create", "create_blank_instance", "delete"}
# The seed's own direct write of a box, which only the setup may call: a party starts no earlier.
SEED_WRITES = {"place_box"}


REGISTRATION = "kapps_semantic_middleware.registration"


def _receiver(node: ast.expr) -> str:
    """The last name of the object a method is called on: `ogm` for `self.ogm.commit`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _sends_an_update(call: ast.Call) -> bool:
    """Whether `call` passes `update=` with any value but a literal False."""
    return any(
        keyword.arg == "update"
        and not (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is False
        )
        for keyword in call.keywords
    )


def direct_writes(tree: ast.AST) -> list[str]:
    """Every direct write in `tree`, as `<line>: <receiver>.<method>`."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in SEED_WRITES:
            found.append(f"{node.lineno}: {node.func.id}")
        if not isinstance(node.func, ast.Attribute):
            continue
        method, receiver = node.func.attr, _receiver(node.func.value)
        if (
            method in STORE_WRITES
            or (method in OGM_WRITES and receiver == "ogm")
            or (method == "query" and _sends_an_update(node))
        ):
            found.append(f"{node.lineno}: {receiver}.{method}")
    return found


def registration_imports(tree: ast.AST) -> list[int]:
    """The lines of `tree` that import the middleware's `registration` module or from it."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        if any(
            name == REGISTRATION or name.startswith(REGISTRATION + ".")
            for name in names
        ):
            lines.append(node.lineno)
    return lines


def test_the_source_is_found():
    assert Path("uc2/module.py") in {path.relative_to(SOURCE) for path in sources()}


@pytest.mark.parametrize(
    "path", sources(), ids=lambda path: str(path.relative_to(SOURCE))
)
@pytest.mark.paper_claim
def test_no_party_writes_past_the_middleware(path):
    """Paper claim 7 (§5.2, §4.3), on jms_kapps' side: every write of a party is a commit of its
    middleware, which goes through the object-graph mapper. Only the reset and the setup write
    directly, before any party starts: the seed, the Launcher's Clear, which stops every process
    first, and the switch of the store's history, the last step of a reset."""
    if path.relative_to(SOURCE) in SETUP:
        pytest.skip("setup, before any party starts")

    assert direct_writes(ast.parse(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    "path", sources(), ids=lambda path: str(path.relative_to(SOURCE))
)
def test_nothing_imports_the_middlewares_registration_module(path):
    assert registration_imports(ast.parse(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ("db.triple_add((s, p, o))", ["1: db.triple_add"]),
        ("self.ogm.commit(instance_iri=x, data={})", ["1: ogm.commit"]),
        ("ogm.create(class_iri=c)", ["1: ogm.create"]),
        ("place_box(ogm, box, on=m, origin=m, destination=n)", ["1: place_box"]),
        ("self.mw.commit(entry=[])", []),
        ("middleware.create(c, scope)", []),
        ("db.query('SELECT ?s WHERE { ?s ?p ?o }')", []),
        ("db.query(ENABLE_HISTORY, update=True)", ["1: db.query"]),
        ("db.query('ASK {}', update=False)", []),
    ],
)
def test_a_direct_write_is_told_from_a_commit_of_the_middleware(code, found):
    assert direct_writes(ast.parse(code)) == found


@pytest.mark.parametrize(
    "code",
    [
        "from kapps_semantic_middleware.registration import mint_capability_iri",
        "import kapps_semantic_middleware.registration",
        "from kapps_semantic_middleware import registration",
    ],
)
def test_every_form_of_importing_registration_is_found(code):
    assert registration_imports(ast.parse(code)) == [1]
