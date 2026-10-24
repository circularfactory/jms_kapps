"""The intake's one fetch of an unscrewing operation, on a live GraphDB.

The operation names a Screw, the Screw names its Screw type, and the type holds the seven values
that an attempt is given. That is a chain three properties deep from the operation. The probe
reads the operation under the screwing resource's `OPERATION_SCOPE` in one fetch and asks for all
seven values of `:M4` to come back with it. It does so under two scopes: `OPERATION_SCOPE` alone
(the resource's own `fetch`) and `OPERATION_SCOPE` united with the capability and workflow chains
that the middleware adds when it reads a notified operation.

The probe seeds a disposable dataset: it clears the dataset that `--dataset` names.
"""

from __future__ import annotations

import pytest
from kapps_ogm import OGM, ClassScope
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.uc1.screwing_resource import OPERATION_SCOPE, unscrewing_operation_from
from jms_kapps.uc1.seed import M4_VALUES, reset_uc1
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, TYPE_LEVEL

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

_NOTIFIED_CHAINS: list[list[str | IRI]] = [
    [CFC.implementsCapability, SVC.realizedByWorkflow]
]
_NOTIFIED_CHAINS.extend(list(chain) for chain in OPERATION_SCOPE.to_property_chains())
NOTIFIED_SCOPE = ClassScope.from_property_chains(_NOTIFIED_CHAINS)

SCREW = IRI("ProbeScrew", base=JMS_NS)
OPERATION = IRI("ProbeUnscrewingOperation", base=JMS_NS)


@pytest.mark.parametrize(
    "scope", [OPERATION_SCOPE, NOTIFIED_SCOPE], ids=["fetch", "notified"]
)
def test_one_fetch_reaches_from_the_operation_to_the_seven_values_of_its_screw_type(
    dataset: str, scope: ClassScope
) -> None:
    db = connect_store(dataset)
    reset_uc1(db)
    ogm = OGM(db=db)
    ogm.commit(
        instance_iri=JMS.M4,
        data={str(prop): [value] for prop, value in M4_VALUES.items()},
    )
    db.graph_add(
        f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        f"<{OPERATION}> a <{JMS.UnscrewingOperation}> ; <{JMS.concernsScrew}> <{SCREW}> ; "
        f'<{SVC.hasOperationStatus}> "{OperationStatus.QUEUED}" .\n',
        content_type="application/x-turtle",
    )

    node = OGM(db=connect_store(dataset)).fetch(
        instance_iri=OPERATION, class_scope=scope
    )

    operation = unscrewing_operation_from(node)
    assert operation.screws == (SCREW,)
    assert operation.screw_types == (JMS.M4,)
    assert operation.status == OperationStatus.QUEUED
    assert dict(operation.type_values) == M4_VALUES
    assert set(operation.type_values) == set(TYPE_LEVEL)
