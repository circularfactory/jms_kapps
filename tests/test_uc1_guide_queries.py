"""The queries in the user guide's section on the uncertainty of a measured position.

Offline, no triple store. The guide shows two short queries: the best estimate of a Screw's head
position, and the literal with its datatype. The test reads them from the guide, parses them, and
runs them on an rdflib graph that holds the ontology, the uncertainty module and one written
variable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF
from rdflib.plugins.sparql import prepareQuery

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.uncertainty import GMM_LITERAL, measurement_literal
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, UQ

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "user-guide.md"
HEADING = "### The uncertainty of a measured position"
HEAD_POSITION = 12.5
STANDARD_DEVIATION = 0.1
SCREW = URIRef(JMS_NS + "Screw1")


def guide_queries() -> list[str]:
    """The SPARQL blocks of the section, in the order the guide gives them."""
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(HEADING)
    end = text.find("\n### ", start + len(HEADING))
    section = text[start : end if end != -1 else len(text)]
    return re.findall(r"```sparql\n(.*?)```", section, re.DOTALL)


@pytest.fixture(scope="module")
def graph() -> Graph:
    """The ontology and the uncertainty module, and one Screw with a written variable and its
    best estimate, as the anomaly detector commits them."""
    g = Graph()
    for name in ("integration_usecase.ttl", "uncertainty.ttl"):
        g.parse(data=read_asset("jms_kapps.uc1", "ontology", name), format="turtle")
    variable = URIRef(JMS_NS + "Variable_1")
    g.add((SCREW, RDF.type, URIRef(str(JMS.Screw))))
    g.add((SCREW, URIRef(str(JMS.hasMeasuredScrewHeadPosition)), Literal(HEAD_POSITION)))
    g.add((SCREW, URIRef(str(JMS.hasScrewHeadPositionVariable)), variable))
    g.add((variable, RDF.type, URIRef(str(UQ.RandomVariable))))
    g.add(
        (
            variable,
            URIRef(str(UQ.hasDistribution)),
            measurement_literal(HEAD_POSITION, STANDARD_DEVIATION),
        )
    )
    return g


def test_the_section_shows_the_two_queries() -> None:
    assert len(guide_queries()) == 2


def test_each_query_parses() -> None:
    for query in guide_queries():
        prepareQuery(query)


def test_the_best_estimate_query_returns_the_one_row(graph: Graph) -> None:
    rows = list(graph.query(guide_queries()[0]))
    assert len(rows) == 1
    assert rows[0][0] == SCREW
    assert float(rows[0][1]) == HEAD_POSITION


def test_the_literal_query_returns_the_one_row_with_its_datatype(graph: Graph) -> None:
    rows = list(graph.query(guide_queries()[1]))
    assert len(rows) == 1
    row = rows[0].asdict()
    assert row["literal"].datatype == GMM_LITERAL
    assert row["datatype"] == GMM_LITERAL
