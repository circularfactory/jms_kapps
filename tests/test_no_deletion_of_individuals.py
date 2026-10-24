"""Nothing in the use cases deletes an individual from the knowledge graph.

The OGM keeps the deletion of individuals out of its public surface: its `delete` only warns and
raises, and its private deletion entry point is not for a consumer. The use cases keep every
individual they write, and end a thing's life with a state, not by removing it. This reads every
Python module of the package and fails when a call's function is an attribute named `delete` or
`_delete`, whatever object it is called on. A planted call is found by the same scan.
"""

from __future__ import annotations

import ast
from pathlib import Path

from _source import SOURCE, sources

FORBIDDEN = {"delete", "_delete"}


def deletions_in(text: str) -> list[int]:
    """The line of each call of `delete` or `_delete` in the module `text`."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FORBIDDEN
    ]


def test_no_call_of_delete_or_private_delete() -> None:
    root = SOURCE.parents[1]
    offences = [
        f"{Path(path).relative_to(root)}:{line}"
        for path in sources()
        for line in deletions_in(path.read_text(encoding="utf-8"))
    ]
    assert not offences, "calls of delete/_delete found:\n" + "\n".join(offences)


def test_the_scan_finds_a_planted_call() -> None:
    planted = "def forget(ogm, node):\n    ogm.delete(node)\n    ogm._delete(node)\n"
    assert deletions_in(planted) == [2, 3]
    assert deletions_in("def keep(ogm, node):\n    ogm.commit(node)\n") == []
