"""How the observation reads one store request (jms_kapps 48): its kind, the statuses it writes,
its receipt, and the store's answer, worded as GraphDB words it. The updates are those the
triple-store interface renders for a checked write, as the object-graph mapper sends a commit.
Nothing reaches a store.
"""

from __future__ import annotations

import pytest
from _fake_store import HEAP_REFUSAL, SHACL_MEDIA_TYPE, SHACL_REPORT
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface.queries.checked_write import (
    RECEIPT_GRAPH,
    render_update,
)
from rdflib import Literal, URIRef
from rdflib.namespace import XSD

from jms_kapps.evaluation import store_requests
from jms_kapps.evaluation.store_requests import StatusWrite
from jms_kapps.uc2.vocabulary import FC, FCI_NS

OPERATION = URIRef(f"{FCI_NS}Box7_transport")
BOX = URIRef(f"{FCI_NS}Box7")
RECEIPT = URIRef("urn:uuid:0d9a5c35-6b0e-4a51-9f5e-1d2a3b4c5d6e")
STATUS = URIRef(str(SVC.operationStatus))


def status(value: str) -> Literal:
    return Literal(value, datatype=XSD.string)


def commit(old: str, new: str, *more: tuple) -> str:
    """The update of a commit that changes the status of `OPERATION` from `old` to `new`."""
    return render_update(
        [(OPERATION, STATUS, status(old))],
        [(OPERATION, STATUS, status(new)), *more],
        None,
        receipt=RECEIPT,
        applied_at=Literal("2026-10-12T12:00:00+00:00", datatype=XSD.dateTime).n3(),
    )


def test_a_commit_writes_the_status_it_inserts_and_not_the_one_it_deletes():
    assert store_requests.status_writes(commit("running", "done")) == [
        StatusWrite(str(OPERATION), "done", None)
    ]


def test_the_box_is_the_one_the_request_names_for_the_operation():
    named = commit("queued", "running", (OPERATION, URIRef(str(FC.hasBox)), BOX))

    assert store_requests.status_writes(named) == [
        StatusWrite(str(OPERATION), "running", str(BOX))
    ]


def test_a_brace_or_a_status_inside_a_literal_does_not_count():
    reason = Literal('no live route } "done" { to Module9', datatype=XSD.string)
    failed = commit(
        "running", "failed", (OPERATION, URIRef(str(SVC.executionResult)), reason)
    )

    assert store_requests.status_writes(failed) == [
        StatusWrite(str(OPERATION), "failed", None)
    ]


def test_a_query_or_a_delete_writes_no_status():
    assert (
        store_requests.status_writes(
            f'SELECT ?s WHERE {{ ?s <{SVC.operationStatus}> "done" }}'
        )
        == []
    )
    assert (
        store_requests.status_writes(
            f'DELETE DATA {{ <{OPERATION}> <{SVC.operationStatus}> "done" }}'
        )
        == []
    )
    assert store_requests.status_writes(None) == []


def test_the_receipt_of_a_checked_write_is_read_from_its_update_and_its_query():
    assert store_requests.receipt_written(commit("queued", "running")) == str(RECEIPT)
    ask = f"ASK {{ GRAPH <{RECEIPT_GRAPH}> {{ <{RECEIPT}> ?p ?o }} }}"
    assert store_requests.receipt_asked(ask) == str(RECEIPT)
    cleanup = f"DELETE WHERE {{ GRAPH <{RECEIPT_GRAPH}> {{ <{RECEIPT}> ?p ?o }} }}"
    assert store_requests.receipt_asked(cleanup) is None
    assert store_requests.receipt_written(cleanup) is None
    assert store_requests.removes_receipt(cleanup)
    assert not store_requests.removes_receipt(commit("queued", "running"))
    assert not store_requests.removes_receipt(None)


@pytest.mark.parametrize(
    ("method", "path", "content_type", "kind"),
    [
        ("POST", "rest/login", "application/json", "login"),
        ("POST", "repositories/eval-x", "application/sparql-query", "read"),
        ("GET", "repositories/eval-x", None, "read"),
        (
            "POST",
            "repositories/eval-x/statements",
            "application/sparql-update",
            "write",
        ),
        ("POST", "repositories/eval-x/statements", "text/turtle", "write"),
        ("GET", "repositories/eval-x/statements", None, "read"),
        ("PUT", "repositories/eval-x/rdf-graphs/service", "text/turtle", "write"),
        ("GET", "rest/repositories", None, "other"),
    ],
)
def test_the_kind_of_a_store_request(method, path, content_type, kind):
    assert store_requests.kind_of(method, path, content_type) == kind


def test_the_store_s_answers_as_graphdb_words_them():
    assert store_requests.answer_of(204, None, "") == ("accepted", None, None)
    assert store_requests.answer_of(500, SHACL_MEDIA_TYPE, SHACL_REPORT) == (
        "refused",
        "shacl",
        SHACL_REPORT,
    )
    assert store_requests.answer_of(500, "text/plain;charset=UTF-8", HEAP_REFUSAL) == (
        "refused",
        "heap",
        HEAP_REFUSAL,
    )
    assert store_requests.answer_of(400, "text/plain", "MALFORMED QUERY") == (
        "refused",
        "other",
        "MALFORMED QUERY",
    )
    assert store_requests.answer_of(503, None, "") == ("refused", "other", "HTTP 503")
