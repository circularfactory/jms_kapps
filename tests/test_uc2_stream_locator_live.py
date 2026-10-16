"""Where a participant's handover event stream lives is stored as the xsd:anyURI literal that the
ontology declares for `fc:hasHandoverEventStream`, and a second write of it leaves one locator.

Module 1 commits its locator when it starts, in one `mw.commit()`. The test reads it back through
the triplestore interface, which gives an xsd:anyURI value back as an rdflib `Literal` with that
datatype and the URL as stored. It then writes the locator again, as a restart at the same
address does, and once more at another address, as a restart on another port does. Each write
replaces the stored locator, so the graph holds exactly one.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import pytest
from _participants import HOST, M1, participants
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import IRI
from rdflib import XSD, Literal

from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.discovery import service_address
from jms_kapps.uc2.handover_events import STREAM_ROUTE, stream_locator
from jms_kapps.uc2.vocabulary import FC

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# Where a restart on another port serves the stream. Nothing listens there: the test reads only
# the graph.
ANOTHER_ADDRESS = f"http://{HOST}:1/"


def locators(db: Queryable, resource: IRI) -> list:
    """Every locator of `resource`'s stream in the graph, as the interface reads it back."""
    rows = bindings(
        db,
        f"SELECT ?stream WHERE {{ <{resource}> <{FC.hasHandoverEventStream}> ?stream }}",
    )
    return [row["stream"] for row in rows]


def any_uri(address: str) -> Literal:
    """The locator of the stream served at `address`, as the store holds it."""
    return Literal(f"{address.rstrip('/')}{STREAM_ROUTE}", datatype=XSD.anyURI)


def test_the_locator_reads_back_as_an_xsd_anyuri_literal_and_a_second_write_leaves_one(
    dataset,
):
    with participants(dataset) as party:
        address = service_address(party.db, M1)
        assert address is not None
        middleware = party.served[M1].middleware

        # Written once, at the start.
        assert locators(party.db, M1) == [any_uri(address)]

        with middleware.commit(entry=stream_locator(M1, address)):
            pass
        assert locators(party.db, M1) == [any_uri(address)]

        with middleware.commit(entry=stream_locator(M1, ANOTHER_ADDRESS)):
            pass
        assert locators(party.db, M1) == [any_uri(ANOTHER_ADDRESS)]
