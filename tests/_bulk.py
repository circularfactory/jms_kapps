"""Validate a whole dataset against use case 2's shapes, as the store's whole-dataset validation does.

A write the store accepted at commit may still break a shape that the store checks only in
bulk, so a run that must leave the graph valid is checked this way at its end. The shapes are use
case 2's shapes file, and the call is the triple-store interface's `validate`.
"""

from __future__ import annotations

from kapps_triplestore_interface import TripleStore
from rdflib import Graph, Literal
from rdflib.namespace import SH

from jms_kapps.common.triplestore import SHAPES_CONTENT_TYPE
from jms_kapps.uc2.seed import shapes_document


def bulk_validation_report(db: TripleStore) -> Graph:
    """Validate the whole dataset against use case 2's shapes, and return the report."""
    return Graph().parse(
        data=db.validate(shapes_document(), SHAPES_CONTENT_TYPE),
        format="turtle",
    )


def conforms(report: Graph) -> bool:
    return list(report.objects(None, SH.conforms)) == [Literal(True)]
