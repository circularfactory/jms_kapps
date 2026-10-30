"""What counts as a direct write in the package's source: a write of the store's client, a SPARQL
update sent as it is, or a write of the object-graph mapper called on its own.

The scans of the writes read it: the scan of every party (`test_uc2_writes.py`), and the scan of
the services of use case 1 (`test_uc1_writes.py`).
"""

from __future__ import annotations

import ast

# The writes of the store's client and of the object-graph mapper. (`graph_clear` and `shapes_set`
# are the client's own writes of the graph and of its shapes, so the scan names them. `clear_graph`
# is a name the client does not have; it stays, so that a call by that name is still found. The
# client's `prefix_set` and `prefix_delete` change only the names the workbench shows, not the
# graph, and stay out.)
STORE_WRITES = {
    "clear_graph",
    "graph_add",
    "graph_clear",
    "shapes_set",
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


def receiver_of(node: ast.expr) -> str:
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
        method, receiver = node.func.attr, receiver_of(node.func.value)
        if (
            method in STORE_WRITES
            or (method in OGM_WRITES and receiver == "ogm")
            or (method == "query" and _sends_an_update(node))
        ):
            found.append(f"{node.lineno}: {receiver}.{method}")
    return found
