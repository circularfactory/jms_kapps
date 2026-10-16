"""The live criterion of the prefill generator (jms_kapps 79): a store that validates use case 2's
shapes at every commit admits a prefill of 100k statements, loaded in transactions, after the seed
of the 30-module grid.

The seed goes through the run core's graph port (`run_core.TripleStoreGraph`), as a run seeds, and
loads the shapes first, so each transaction of the prefill is validated when it commits. The file is
loaded through the triple-store interface, one transaction for each document that
`prefill.transactions` gives. A control write that breaks a shape shows that the dataset
validates at all.

**Only on an `eval-` dataset**, created for this from `graphdb/jms-kapps-repo-config.ttl` with
only its ID changed, so that SHACL is on, and deleted afterwards. These tests clear it. They take it
by `--dataset`, as every graphdb test does, and run only when the store's connection variables
are set.
"""

from __future__ import annotations

import pytest
from _bulk import bulk_validation_report, conforms
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import TripleStore

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.evaluation.prefill import transactions, write_prefill
from jms_kapps.uc2.run_core import TripleStoreGraph
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

EVALUATION_PREFIX = "eval-"

SIZE = 100_000
SEED = 79
STATEMENTS_PER_TRANSACTION = 10_000


@pytest.fixture
def db(dataset: str) -> TripleStore:
    """The `eval-` dataset, seeded with the grid as a run seeds it: emptied, then use case 2's
    shapes, ontologies and the 30 modules with the WarehouseManagementSystem."""
    if not dataset.startswith(EVALUATION_PREFIX):
        pytest.skip(
            f"refused on {dataset!r}: these tests clear the dataset and load a prefill, "
            f"so they run only on one whose name starts with {EVALUATION_PREFIX!r}"
        )
    db = connect(dataset)
    TripleStoreGraph(db).seed(shipped_topology("grid"), 0.0, collect_history=False)
    return db


def count(db: TripleStore, class_iri: str) -> int:
    rows = bindings(
        db, f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ ?s a <{class_iri}> }}"
    )
    return int(rows[0]["n"])


def test_the_store_admits_every_transaction_of_a_100k_prefill_after_the_grid(
    db, tmp_path
):
    path = tmp_path / "prefill.ttl"
    prefill = write_prefill(path, topology="grid", size=SIZE, seed=SEED)

    loaded = [
        db.graph_add(chunk, content_type="application/x-turtle")
        for chunk in transactions(path, statements=STATEMENTS_PER_TRANSACTION)
    ]

    assert len(loaded) >= SIZE // STATEMENTS_PER_TRANSACTION
    assert [n for n, admitted in enumerate(loaded) if not admitted] == []
    assert count(db, str(FC.Box)) == prefill.boxes
    assert count(db, str(FC.BoxTransportOperation)) == prefill.boxes
    assert conforms(bulk_validation_report(db))


def test_the_store_refuses_a_done_transport_whose_box_is_on_a_module(db):
    """The control: the dataset validates use case 2's shapes at commit, so the admission above
    means something."""
    broken = f"""
@prefix fc: <{FC_NS}> .
@prefix fci: <{FCI_NS}> .
@prefix cfc: <https://w3id.org/circularfactory/Core#> .
@prefix svc: <https://w3id.org/circularfactory/Service#> .
fci:Module1 cfc:hasPossessor fci:Module1_possession .
fci:Module1_possession a cfc:PossessionState .
fci:Box1 a fc:Box ;
    fc:hasTransport fci:Box1_transport ;
    cfc:hasPossessedWorkpiece fci:Module1_possession .
fci:Box1_transport a fc:BoxTransportOperation ;
    fc:hasBox fci:Box1 ;
    fc:hasOrigin fci:Module1 ;
    fc:hasDestination fci:Module27 ;
    svc:operationStatus "done" .
"""

    admitted = db.graph_add(broken, content_type="application/x-turtle")

    assert admitted is False
    assert count(db, str(FC.Box)) == 0
