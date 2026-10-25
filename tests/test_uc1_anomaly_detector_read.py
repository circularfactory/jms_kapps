"""The anomaly detector's own read of an unscrewing operation, sent through the middleware, the
object-graph mapper and the triple-store interface to a store: the stand-in of GraphDB's HTTP API
(`_fake_store.FakeStore`), which rdflib answers in memory. No GraphDB, no network beyond the
loopback.

The store holds what the KIT store held after a classification with the mode "Save measurements as
uncertainty quantifications" on, as written, nothing inferred: the links that the middleware writes
when the screwing resource registers (its capability, the capability's workflow, the workflow's
service, the service's resource), and the operation as the planner and the screwing resource write
it. Neither writes `svc:executedByWorkflow`: only the middleware's `claim_next()` does, and the
screwing resource commits its attempt with `mw.commit()`. A read that wanted that link found no
resource on the KIT store, while a test that handed the detector a nested node built by hand
passed. This one reads from the store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _fake_store import FakeStore
from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.auth import GDBTokenAuth
from kapps_triplestore_interface.graph_db import GraphDB
from kapps_triplestore_interface.store import TripleStoreEndpoint

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.anomaly_detector import read_operation
from jms_kapps.uc1.seed import uc1_ontology
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

OPERATION = IRI("UnscrewingOperation_0a1b2c3d4e5f", base=JMS_NS)
SCREW = IRI("TestScrew", base=JMS_NS)
SCREWING_RESOURCE = IRI("TestScrewingResource", base=JMS_NS)
DETECTOR = IRI("TestAnomalyDetector", base=JMS_NS)
CAPABILITY = IRI("TestScrewingResource_capability_unscrew", base=JMS_NS)
REFERENCE = "influxdb:///Tests/unscrewing?frame=f1&start=2026-10-25T00:00:00Z&stop=2026-10-25T00:00:07Z"


def _registration(
    resource: IRI, port: int, name: str, workflow_class: IRI
) -> tuple[str, IRI]:
    """The links that the middleware writes when `resource` registers its service at `port` with
    one workflow `name`, named as the middleware names them; and the workflow."""
    service = IRI(f"{resource}_service_http_c__s__s_127_d_0_d_0_d_1_c_{port}")
    workflow = IRI(f"{service}_workflow_{name}")
    return (
        f"<{service}> a <{SVC.Service}> ; <{SVC.isServiceOf}> <{resource}> .\n"
        f"<{workflow}> a <{workflow_class}> ; <{SVC.isWorkflowOf}> <{service}> .\n"
    ), workflow


def _held() -> str:
    """What the store holds, as written: the screwing resource registered twice (a restart),
    the detector once, and the running operation."""
    first, workflow = _registration(
        SCREWING_RESOURCE, 40751, "unscrew", JMS.UnscrewingWorkflow
    )
    again, workflow_again = _registration(
        SCREWING_RESOURCE, 40752, "unscrew", JMS.UnscrewingWorkflow
    )
    detector, _ = _registration(DETECTOR, 39141, "classify", JMS.ClassifyWorkflow)
    return (
        f"<{SCREWING_RESOURCE}> a <{JMS.ScrewingResource}> ; <{CFC.hasCapability}> <{CAPABILITY}> .\n"
        f"<{DETECTOR}> a <{CFC.Resource}> .\n"
        f"<{CAPABILITY}> a <{JMS.UnscrewingCapability}> ; "
        f"<{SVC.realizedByWorkflow}> <{workflow}>, <{workflow_again}> .\n"
        + first
        + again
        + detector
        + f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        f"<{OPERATION}> a <{JMS.UnscrewingOperation}> ; "
        f"<{CFC.implementsCapability}> <{CAPABILITY}> ; "
        f'<{SVC.hasOperationStatus}> "{OperationStatus.RUNNING}" ; '
        f"<{JMS.concernsScrew}> <{SCREW}> ; "
        f'<{JMS.hasTimeSeriesReference}> "{REFERENCE}" .\n'
    )


@pytest.fixture
def middleware() -> Iterator[SemanticMiddleware]:
    """The detector's middleware instance, never served, on the stand-in store that holds Core,
    `svc:`, `mes:`, use case 1's ontology and `_held()`."""
    store = FakeStore("uc1-read-fake")
    for turtle in (
        read_asset("kapps_semantic_middleware", "ontology", "core.ttl"),
        read_asset("kapps_semantic_middleware", "ontology", "service.ttl"),
        read_asset("kapps_semantic_middleware", "ontology", "mes.ttl"),
        uc1_ontology(),
        _held(),
    ):
        store.graph.default_context.parse(data=turtle, format="turtle")
    with store:
        db = GraphDB(
            endpoint=TripleStoreEndpoint(url=store.url, dataset=store.dataset),
            auth=GDBTokenAuth("detector", "detector-secret"),
        )
        yield SemanticMiddleware(
            mode=Mode.RESOURCE,
            resource_iri=str(DETECTOR),
            service_class=str(JMS.AnomalyDetectorService),
            ogm=OGM(db=db),
            heartbeat_interval=None,
        )
        db.close()


def test_with_the_mode_on_the_read_finds_the_one_resource_whose_capability_the_operation_implements(
    middleware,
):
    found = read_operation(middleware, OPERATION, saves_uq=True)

    assert found.status == OperationStatus.RUNNING
    assert found.screws == (SCREW,)
    assert found.references == (REFERENCE,)
    assert found.resources == (SCREWING_RESOURCE,)


def test_with_the_mode_off_the_read_finds_no_resource(middleware):
    found = read_operation(middleware, OPERATION)

    assert found.screws == (SCREW,)
    assert found.resources == ()
