"""What a GraphDB dataset holds as written, apart from what its reasoner infers.

The dataset's ruleset infers the inverse of a property that declares one, such as a box's
`fc:hasTransport` from its transport's `fc:hasBox`. A test of a write that must state both
directions itself reads only the explicit statements.
"""

from __future__ import annotations

from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI, SPARQLQuery

from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.vocabulary import FC


def explicit_objects(db: Queryable, subject: IRI, predicate: IRI) -> list[str]:
    """The objects of `subject`'s explicit statements of `predicate`, sorted."""
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?o"],
            where_clauses=[f"<{subject}> <{predicate}> ?o"],
            include_implicit=False,
        ),
    )
    return sorted(str(row["o"]) for row in rows)


def explicit_links(
    db: Queryable, box: IRI, transport: IRI
) -> tuple[list[str], list[str]]:
    """The transports `box` links to and the boxes `transport` links to, each as written: a box
    and its transport linked both ways are `([transport], [box])`."""
    return (
        explicit_objects(db, box, FC.hasTransport),
        explicit_objects(db, transport, FC.hasBox),
    )


def explicit_statements(db: Queryable) -> set[tuple[str, str, str]]:
    """Every explicit statement of the default graph, except heartbeats, which move on their own."""
    clauses = ["?s ?p ?o", f"FILTER (?p != <{SVC.hasLastHeartbeat}>)"]
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?s", "?p", "?o"],
            where_clauses=clauses,
            include_implicit=False,
        ),
        # Each term's lexical value, compared as it is written, with no conversion in between.
        convert=False,
    )
    return {(row["s"]["value"], row["p"]["value"], row["o"]["value"]) for row in rows}
