"""The PROV witness of use case 1, on a live GraphDB: the store refuses a parameter copy that
does not name the classification that generated it.

The shape is `:ParameterCopyGeneratedByShape` in `uc1/shapes/integration_usecase_shapes.ttl`,
which the seed's reset sends. A snapshot without `prov:wasGeneratedBy` is refused at commit, with
the triplestore interface's `ShaclValidationError`, whose report names the shape, and the graph
does not change. The same snapshot with its classification is admitted, and the store infers the
classification's back-link, `prov:generated`.

That the detector's own commit writes the classification, its agent and its end time is proved
offline (`test_uc1_anomaly_detector.py`), and on a real run by `test_uc1_run_live.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kapps_ogm import OGM, ClassScope, Create
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import (
    ValidationResult,
    validation_results,
)
from kapps_triplestore_interface import IRI, ShaclValidationError

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.run_core import resources_turtle
from jms_kapps.uc1.seed import reset_uc1
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PROV

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

SNAPSHOT = IRI("ProvTestSnapshot", base=JMS_NS)
CLASSIFICATION = IRI("Classification_provtest", base=JMS_NS)
SHAPE = IRI("ParameterCopyGeneratedByShape", base=JMS_NS)


@pytest.fixture
def ogm(dataset):
    """The dataset, cleared, with use case 1's ontology, its shapes and the seeded resources."""
    db = connect(dataset)
    reset_uc1(db, individuals=resources_turtle())
    return OGM(db=db)


def snapshot_count(ogm: OGM) -> int:
    (row,) = bindings(
        ogm.db,
        f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s a <{JMS.ParameterCopy}> }}",
    )
    return int(row["n"])


@pytest.mark.paper_claim
def test_a_snapshot_without_its_classification_is_refused_by_the_store(ogm):
    """Paper claim 16: the store refuses the write, the report names the shape at the snapshot,
    and nothing of the write enters the graph."""
    with pytest.raises(ShaclValidationError) as raised:
        ogm.commit(
            changes=[
                Create(class_iri=JMS.ParameterCopy, instance_iri=SNAPSHOT, data={})
            ]
        )

    assert ValidationResult(
        focus=str(SNAPSHOT), path=f"<{PROV.wasGeneratedBy}>", shape=str(SHAPE)
    ) in validation_results(raised.value.report)
    assert snapshot_count(ogm) == 0


def test_a_snapshot_with_its_classification_is_admitted_and_the_back_link_is_inferred(
    ogm,
):
    ended = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)

    ogm.commit(
        changes=[
            Create(
                class_iri=PROV.Activity,
                instance_iri=CLASSIFICATION,
                class_scope=ClassScope.from_property_chains(
                    [[PROV.wasAssociatedWith], [PROV.endedAtTime]]
                ),
                data={
                    str(PROV.wasAssociatedWith): [{"id": str(DETECTOR)}],
                    str(PROV.endedAtTime): [ended],
                },
            ),
            Create(
                class_iri=JMS.ParameterCopy,
                instance_iri=SNAPSHOT,
                class_scope=ClassScope.from_property_chains([[PROV.wasGeneratedBy]]),
                data={str(PROV.wasGeneratedBy): [{"id": str(CLASSIFICATION)}]},
            ),
        ]
    )

    rows = bindings(
        ogm.db,
        f"""
        SELECT ?agent ?ended ?generated WHERE {{
            <{SNAPSHOT}> <{PROV.wasGeneratedBy}> <{CLASSIFICATION}> .
            <{CLASSIFICATION}> <{PROV.wasAssociatedWith}> ?agent ;
                <{PROV.endedAtTime}> ?ended ;
                <{PROV.generated}> ?generated .
        }}
        """,
    )
    assert [(str(r["agent"]), str(r["generated"])) for r in rows] == [
        (str(DETECTOR), str(SNAPSHOT))
    ]
    assert datetime.fromisoformat(str(rows[0]["ended"])) == ended
