"""Validate a whole dataset against use case 2's shapes, as GraphDB's bulk validation does.

A write the store accepted at commit may still break a shape that the store evaluates only in
bulk, so a run that must leave the graph valid is checked this way at its end.
"""

from __future__ import annotations

import requests
from kapps_triplestore_interface import GraphDB
from rdflib import Graph, Literal
from rdflib.namespace import SH

from jms_kapps.common.triplestore import read_asset


def bulk_validation_report(db: GraphDB) -> Graph:
    """Validate the whole dataset against use case 2's shapes, and return the report."""
    # The triplestore interface has no call for GraphDB's bulk validation, so this posts to its
    # REST endpoint itself, with the adapter's private endpoint and authorization. GraphDB names
    # the store's unit a repository in its REST paths.
    response = requests.post(
        f"{db._endpoint.url}/rest/repositories/{db.dataset}/validate/text",
        headers={
            "Authorization": db._auth,
            "Content-Type": "text/turtle",
            "Accept": "text/turtle",
        },
        data=read_asset(
            "jms_kapps.uc2", "shapes", "flexconveyor_usecase_shapes.ttl"
        ).encode(),
        timeout=60,
    )
    response.raise_for_status()
    return Graph().parse(data=response.text, format="turtle")


def conforms(report: Graph) -> bool:
    return list(report.objects(None, SH.conforms)) == [Literal(True)]
