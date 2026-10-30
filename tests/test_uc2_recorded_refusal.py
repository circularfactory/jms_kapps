"""The recorded refusal of a handover onto an occupied module, without a triple store: the
report the recording keeps verbatim, `tests/reports/skip-reserve.nq`, its Turtle copy beside it,
and the page that shows them, `docs/recorded-refusal.md`.

The two files hold the same graph, the report names the possession shape at Module 2, the Turtle
copy declares the use case's prefixes, and the page is linked from the page of use case 2 and the
README.
The page prints the Turtle copy as the paper's Listing 1 prints the report, and names the command
that makes the recording again. The recording script, which the checkout and the published tree
have and an installed copy does not, writes the same Turtle copy from the report.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _repo import find_repo_root
from kapps_semantic_middleware.vocabulary import CORE_NS
from rdflib import Dataset, Graph, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import SH

from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS, module_iri

ROOT = find_repo_root(Path(__file__))
REPORTS = Path(__file__).parent / "reports"
NQUADS = REPORTS / "skip-reserve.nq"
TURTLE = REPORTS / "skip-reserve.ttl"
SCRIPT = ROOT / "scripts" / "record_uc2_refusal.py" if ROOT is not None else None
PAGE = "recorded-refusal.md"
PAGES = ("docs",)


def graph_of(path: Path) -> Graph:
    """The graph a recorded file holds: N-Quads, whatever graph each quad names, or Turtle."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".nq":
        quads = Dataset(default_union=True)
        quads.parse(data=text, format="nquads")
        graph = Graph()
        for triple in quads.triples((None, None, None)):
            graph.add(triple)
        return graph
    return Graph().parse(data=text, format="turtle")


def test_the_report_and_its_turtle_copy_hold_the_same_graph():
    assert isomorphic(graph_of(NQUADS), graph_of(TURTLE))


def test_the_report_names_the_possession_shape_at_module_2():
    """What the paper's listing shows: the shape, its constraint, and the occupied module."""
    graph = graph_of(NQUADS)
    results = list(
        graph.subjects(SH.sourceShape, URIRef(FC.FlexConveyorModulePossessionShape))
    )

    assert len(results) == 1
    (result,) = results
    assert (
        graph.value(result, SH.sourceConstraintComponent)
        == SH.MaxCountConstraintComponent
    )
    assert graph.value(result, SH.focusNode) == URIRef(module_iri(2))
    assert graph.value(result, SH.resultSeverity) == SH.Violation
    assert (None, SH.conforms, None) in graph


def test_the_turtle_copy_declares_the_use_cases_prefixes():
    text = TURTLE.read_text(encoding="utf-8")

    for declaration in (
        f"@prefix : <{FC_NS}> .",
        f"@prefix fci: <{FCI_NS}> .",
        f"@prefix cfc: <{CORE_NS}> .",
        f"@prefix sh: <{SH}> .",
    ):
        assert declaration in text
    assert ":FlexConveyorModulePossessionShape" in text
    assert "fci:Module2" in text


def test_the_page_is_linked_from_the_page_of_use_case_2_and_the_readme():
    """The README's paper table links the page as Listing 1, and the page of use case 2 links it
    from its faults."""
    assert ROOT is not None
    use_case_2 = (ROOT.joinpath(*PAGES) / "use-case-2.md").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")

    assert f"]({PAGE})" in use_case_2
    assert f"](docs/{PAGE})" in readme
    assert (ROOT.joinpath(*PAGES) / PAGE).is_file()


def page_text() -> str:
    assert ROOT is not None
    return (ROOT.joinpath(*PAGES) / PAGE).read_text("utf-8")


def test_the_page_prints_the_turtle_copy_as_the_papers_listing():
    """The Turtle copy, verbatim, in one block."""
    turtle = TURTLE.read_text(encoding="utf-8").rstrip()

    assert f"```turtle\n{turtle}\n```" in page_text()


def test_the_page_names_the_command_that_makes_the_recording_again():
    page = page_text()

    assert (
        "TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python scripts/record_uc2_refusal.py "
        "--dataset <dataset>"
    ) in page
    assert "development checkout" not in page


def test_the_page_names_no_dataset_of_the_recordings_own_store():
    """The recording ran on a disposable dataset named after its build; the page names none."""
    page = page_text()

    assert "disposable-" not in page
    assert "--dataset Tests" not in page


def test_every_state_query_on_the_page_answers_a_row():
    """The page's state after the refusal is what its three queries answer: box B with module 1,
    box A with module 2, and box B's transport executed on module 1, running."""
    page = page_text()

    assert "(no row)" not in page
    assert '| fci:Module1 | "running" |' in page


@pytest.fixture(scope="module")
def script() -> ModuleType:
    if SCRIPT is None or not SCRIPT.exists():
        pytest.skip("an installed copy has no scripts")
    spec = importlib.util.spec_from_file_location("record_uc2_refusal", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve their annotations through it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_script_writes_the_turtle_copy_from_the_report(script: ModuleType):
    """The Turtle copy beside the report is the script's own rendering of it: the same graph,
    with the same prefixes declared. The blank-node labels of a rendering differ each time."""
    report = NQUADS.read_text(encoding="utf-8")
    rendered = script.turtle(report)
    committed = TURTLE.read_text(encoding="utf-8")

    assert isomorphic(script.report_graph(report), graph_of(NQUADS))
    assert isomorphic(Graph().parse(data=rendered, format="turtle"), graph_of(TURTLE))
    assert [line for line in rendered.splitlines() if line.startswith("@prefix")] == [
        line for line in committed.splitlines() if line.startswith("@prefix")
    ]
