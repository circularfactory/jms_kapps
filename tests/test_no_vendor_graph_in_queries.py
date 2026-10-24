"""No query text in jms_kapps names a store's own graph.

A read of the asserted statements only states that through the interface's query builder
(`include_implicit=False`), which writes the scope in the form its store understands. The
exceptions are listed here by file.

The store's own graphs include those of GraphDB's history plugin, one for each point in time,
whose namespace also holds the plugin's switch: the one module that uses the plugin, the vendor
exception, is listed here, so that no other file names it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# GraphDB's pseudo-graphs of the explicit and the implicit statements, and the namespace of its
# history plugin, whose graphs hold the data as it was at one point in time.
VENDOR_GRAPH = re.compile(r"ontotext\.com/(explicit|implicit|at/)")

# Files that may name one, relative to the repository root: the possession-history read, which
# uses the history plugin, and the switch that turns the plugin on.
EXCEPTIONS: frozenset[str] = frozenset({"src/jms_kapps/uc2/history.py"})


def test_no_python_source_names_a_vendors_graph():
    named = [
        f"{path.relative_to(ROOT)}:{number}"
        for folder in ("src", "tests")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if path.relative_to(ROOT).as_posix() not in EXCEPTIONS
        and path != Path(__file__).resolve()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if VENDOR_GRAPH.search(line)
    ]
    assert named == []
