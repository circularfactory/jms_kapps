"""No party finds a transport by its name: a box links to its transport, `fc:hasTransport`.

The WarehouseManagementSystem's opening of a transport and the seed's placing of a box mint the
IRI of the transport they create, with `transport_iri`, and so does the prefill of the measurement
harness, which creates the transports of past journeys as the seed creates its own. Nothing else under `src/` calls it, and nothing else spells the name it
gives: a party that holds a box reads the box's transport through the box, and a test
uses the IRI that its opening returns.

Read from the source under `src/`, without a store.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _source import SOURCE, sources

# Where a transport's IRI is minted, by file: the functions that may call `transport_iri`. The
# prefill of the measurement harness creates the transports of past journeys before any participant
# starts, as the seed does, and names each as the opening would, since the prefill is written as the seed is.
MINTERS = {
    Path("uc2/transport.py"): {"open"},
    Path("uc2/seed.py"): {"place_box"},
    Path("evaluation/prefill.py"): {"_write"},
}

MINT = "transport_iri"

# The one place that spells the name the mint gives, after its box: the mint itself.
SPELLER = Path("uc2/vocabulary.py")

# The suffix of that name, as `transport_iri` spells it.
NAME_SUFFIX = "_transport"


def mints(tree: ast.AST) -> list[str]:
    """Every call of `transport_iri` in `tree`, as `<function>:<line>`, where `<function>` is the
    innermost function the call is in, or `<module>`."""
    found: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                callee = child.func
                name = (
                    callee.id
                    if isinstance(callee, ast.Name)
                    else callee.attr
                    if isinstance(callee, ast.Attribute)
                    else ""
                )
                if name == MINT:
                    found.append(f"{function}:{child.lineno}")
            visit(child, function)

    visit(tree, "<module>")
    return found


def spellings(tree: ast.AST) -> list[int]:
    """The lines of `tree` with a string, or a piece of an f-string, that holds the suffix of the
    mint's name as the mint puts it after a box's name: at the start of the piece, after the
    box's number, and at the end of a word. Such code builds or reads the name itself."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _spells(node.value)
    ]


def _spells(text: str) -> bool:
    index = text.find(NAME_SUFFIX)
    while index != -1:
        before = text[index - 1 : index] if index else ""
        after = text[index + len(NAME_SUFFIX) : index + len(NAME_SUFFIX) + 1]
        if (not before or before.isdigit()) and not (after.isalnum() or after == "_"):
            return True
        index = text.find(NAME_SUFFIX, index + 1)
    return False


def test_the_minters_are_found():
    found = {path.relative_to(SOURCE) for path in sources()}
    assert {*MINTERS, SPELLER} <= found


@pytest.mark.parametrize(
    "path", sources(), ids=lambda path: str(path.relative_to(SOURCE))
)
def test_only_the_opening_and_the_seed_mint_a_transports_iri(path):
    allowed = MINTERS.get(path.relative_to(SOURCE), set())
    called = mints(ast.parse(path.read_text(encoding="utf-8")))

    assert [where for where in called if where.split(":")[0] not in allowed] == []


@pytest.mark.parametrize(
    "path", sources(), ids=lambda path: str(path.relative_to(SOURCE))
)
def test_nothing_but_the_mint_spells_a_transports_name(path):
    if path.relative_to(SOURCE) == SPELLER:
        pytest.skip("the mint itself")

    assert spellings(ast.parse(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ("transport_iri(box)", ["<module>:1"]),
        ("def f(box):\n    return vocabulary.transport_iri(box)", ["f:2"]),
        ("def f(box):\n    def g():\n        transport_iri(box)", ["g:3"]),
        ("records.open(box)", []),
    ],
)
def test_a_call_of_the_mint_is_told_with_the_function_it_is_in(code, found):
    assert mints(ast.parse(code)) == found


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ('IRI(f"{box.fragment}_transport", base=FCI_NS)', [1]),
        ('name = box + "_transport"', [1]),
        ('"see Box1_transport."', [1]),
        ('"its box_transport workflow"', []),
        ("def box_transport(transport): pass", []),
    ],
)
def test_a_spelling_of_the_name_is_told_from_a_word_that_contains_it(code, found):
    assert spellings(ast.parse(code)) == found
